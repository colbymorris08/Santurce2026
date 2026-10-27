#!/usr/bin/env python3
"""Fill missing XY movement plots in small checkpointed batches.

Tries 2026, then 2025, then 2024. Prospect Savant / Statcast first
(via the existing arsenal builder), then Synergy IVB/HB including
LBPRC winter. Never replaces a pitcher who already has a plot.
Does not invent numbers. Writes the arsenal JSON after every batch.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import build_advance_synergy as bas
import build_pregame_arsenal as bpa
import fill_remaining_holes as holes

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
ARSENAL = DATA / "pregame_pitcher_arsenals.json"
CHECKPOINT = RUNS / "movement_plot_batch_checkpoint.json"
REPORT = RUNS / "movement_plot_batch_report.json"
YEARS = (2026, 2025, 2024, 2023)
MOVE_KEYS = (
    "inducedverticalbreak",
    "inducedvertbreak",
    "induced_vertical_break",
    "ivb",
    "pfx_z",
    "pfxz",
    "pfxZ",
)
HB_KEYS = (
    "horizontalbreak",
    "horzbreak",
    "horizbreak",
    "horizontal_break",
    "hb",
    "pfx_x",
    "pfxx",
    "pfxX",
)
VELO_KEYS = ("releasespeed", "release_speed", "velocity", "velo", "speed", "startspeed")
SPIN_KEYS = ("releasespinrate", "release_spin_rate", "spinrate", "spin")


def load_checkpoint() -> dict:
    if CHECKPOINT.is_file():
        return json.loads(CHECKPOINT.read_text())
    return {"pitchers": {}, "synergy_pitch_keys": [], "batches": []}


def save_checkpoint(doc: dict) -> None:
    doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    holes.dump(CHECKPOINT, doc)


def save_arsenal(doc: dict) -> None:
    doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    holes.dump(ARSENAL, doc)


def missing_pitchers(
    arsenal_by: dict,
    roster: list[dict],
    *,
    already_checked: set[str] | None = None,
) -> list[dict]:
    checked = already_checked or set()
    return [
        player
        for player in roster
        if holes.player_key(player) not in checked
        and not holes.has_movement(arsenal_by.get(holes.player_key(player)))
    ]


def flatten_nums(obj: Any, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten_nums(value, path))
    elif isinstance(obj, (int, float)) and holes.finite(obj):
        out[prefix] = float(obj)
    return out


def pick_num(flat: dict[str, float], names: tuple[str, ...]) -> float | None:
    lowered = {key.lower(): value for key, value in flat.items()}
    for name in names:
        target = name.lower()
        if target in lowered:
            return lowered[target]
        for key, value in lowered.items():
            tail = key.rsplit(".", 1)[-1]
            if tail == target or key.endswith("." + target):
                return value
    return None


def pack_statcast_year(pid: int, year: int, kind: str) -> tuple[list[dict], dict]:
    return holes.pack_statcast(pid, [str(year)], kind)


def merge_plot_only(row: dict, pitches: list[dict], *, source: str, season, throws: str) -> bool:
    if holes.has_movement(row):
        return False
    incoming = []
    for pitch in pitches:
        code = holes.chart_code(pitch)
        if not code or not holes.finite(pitch.get("ivb")):
            continue
        item = dict(pitch)
        item["type"] = code
        item.pop("hb", None)
        if not holes.finite(item.get("hb_pitcher")) and holes.finite(item.get("hb_hitter")):
            item["hb_pitcher"] = round(-float(item["hb_hitter"]), 2)
        if not holes.finite(item.get("hb_pitcher")):
            continue
        if not holes.finite(item.get("hb_hitter")):
            item["hb_hitter"] = round(-float(item["hb_pitcher"]), 2)
        incoming.append(item)
    if not incoming:
        return False
    by_code = {holes.chart_code(pitch): pitch for pitch in incoming}
    arsenal = list(row.get("arsenal") or [])
    matched = set()
    for pitch in arsenal:
        code = holes.chart_code(pitch)
        source_pitch = by_code.get(code)
        if not source_pitch:
            continue
        matched.add(code)
        for key in ("ivb", "hb_pitcher", "hb_hitter", "spin"):
            if not holes.finite(pitch.get(key)) and holes.finite(source_pitch.get(key)):
                pitch[key] = source_pitch[key]
        if pitch.get("velo") in (None, "", 0, 0.0) and source_pitch.get("velo"):
            pitch["velo"] = source_pitch["velo"]
        pitch.pop("hb", None)
    for code, source_pitch in by_code.items():
        if code not in matched:
            clean = {
                "type": source_pitch.get("type") or code,
                "name": source_pitch.get("name") or code,
                "velo": source_pitch.get("velo"),
                "spin": source_pitch.get("spin"),
                "ivb": source_pitch.get("ivb"),
                "hb_pitcher": source_pitch.get("hb_pitcher"),
                "hb_hitter": source_pitch.get("hb_hitter"),
                "usage": source_pitch.get("usage"),
                "pitches": source_pitch.get("pitches"),
            }
            arsenal.append(clean)
    row["arsenal"] = arsenal
    if throws and not row.get("throws"):
        row["throws"] = throws
    row["view"] = "hitter"
    row["movement_source"] = source
    row["movement_season"] = season
    if str(source).startswith("Prospect Savant"):
        row["ps_season"] = season
        row["ps_source"] = "Prospect Savant"
    elif str(source).startswith("MLB Statcast"):
        row["statcast_source"] = source
        row["statcast_seasons"] = [str(season)] if season else []
    elif str(source).startswith("Synergy"):
        row["synergy_movement_source"] = source
        row["synergy_movement_season"] = season
    return holes.has_movement(row)


def pack_synergy_year(events: list[dict], year: int) -> tuple[list[dict], list[str], str]:
    by_code: dict[str, dict] = defaultdict(lambda: {
        "ivb": [], "hb": [], "pfx_x": [], "pfx_z": [], "velo": [], "spin": [],
    })
    keys: set[str] = set()
    n_year = 0
    n_break = 0
    for event in events:
        if bas.event_season_year(event) != year:
            continue
        n_year += 1
        pitch = event.get("pitch") or {}
        code = holes.chart_code({"type": bas._pitch_code(pitch.get("pitchKind"))})
        if not code:
            continue
        flat = flatten_nums(pitch)
        keys.update(flat)
        ivb = pick_num(flat, MOVE_KEYS)
        hb = pick_num(flat, HB_KEYS)
        pfx_x = pick_num(flat, ("pfx_x", "pfxx", "pfxX"))
        pfx_z = pick_num(flat, ("pfx_z", "pfxz", "pfxZ"))
        velo = pick_num(flat, VELO_KEYS)
        spin = pick_num(flat, SPIN_KEYS)
        bag = by_code[code]
        if pfx_x is not None:
            bag["pfx_x"].append(pfx_x)
        if pfx_z is not None:
            bag["pfx_z"].append(pfx_z)
        if ivb is not None and "pfx" not in {k.lower() for k in flat if k.lower().endswith(("pfx_z", "pfxz"))}:
            bag["ivb"].append(ivb)
        if hb is not None and "pfx" not in {k.lower() for k in flat if k.lower().endswith(("pfx_x", "pfxx"))}:
            bag["hb"].append(hb)
        if velo is not None:
            bag["velo"].append(velo)
        if spin is not None:
            bag["spin"].append(spin)
        if (ivb is not None and hb is not None) or (pfx_x is not None and pfx_z is not None):
            n_break += 1

    if n_year and not n_break:
        return [], sorted(keys), f"{year} had {n_year} Synergy pitches but no IVB/HB"

    pitches = []
    total = sum(max(len(bag["ivb"]), len(bag["pfx_z"])) for bag in by_code.values())
    for code, bag in by_code.items():
        ivb = hb_pitcher = hb_hitter = None
        if bag["pfx_z"] and bag["pfx_x"]:
            pfx_z = sum(bag["pfx_z"]) / len(bag["pfx_z"])
            pfx_x = sum(bag["pfx_x"]) / len(bag["pfx_x"])
            # pfx in feet if typical Statcast range, else already inches.
            if abs(pfx_z) <= 4 and abs(pfx_x) <= 4:
                ivb = bpa.ivb_in(pfx_z)
                hb_pitcher = bpa.pitcher_view_hb_in(pfx_x)
                hb_hitter = bpa.hitter_view_hb_in(pfx_x)
            else:
                ivb = round(pfx_z, 2)
                hb_hitter = round(pfx_x, 2)
                hb_pitcher = round(-pfx_x, 2)
        elif bag["ivb"] and bag["hb"]:
            ivb = round(sum(bag["ivb"]) / len(bag["ivb"]), 2)
            hb_raw = sum(bag["hb"]) / len(bag["hb"])
            # Synergy/Trackman HB stored as arm-side-positive inches when
            # the field is named horizontalBreak / hb (page mirrors RHP only).
            hb_pitcher = round(hb_raw, 2)
            hb_hitter = round(-hb_raw, 2)
        if ivb is None or hb_pitcher is None:
            continue
        n = max(len(bag["ivb"]), len(bag["pfx_z"]), 1)
        velo = round(sum(bag["velo"]) / len(bag["velo"]), 1) if bag["velo"] else None
        spin = int(round(sum(bag["spin"]) / len(bag["spin"]))) if bag["spin"] else None
        pitches.append({
            "type": code,
            "name": code,
            "velo": velo,
            "spin": spin,
            "ivb": ivb,
            "hb_pitcher": hb_pitcher,
            "hb_hitter": hb_hitter,
            "usage": round(100.0 * n / total, 1) if total else None,
            "pitches": n,
        })
    pitches.sort(key=lambda row: -(row.get("pitches") or 0))
    return pitches, sorted(keys), ""


def best_why(attempts: list[dict]) -> str:
    for attempt in attempts:
        result = str(attempt.get("result") or "")
        if "but no IVB/HB" in result or "no IVB/HB" in result:
            return result
    for attempt in reversed(attempts):
        if attempt.get("result"):
            return str(attempt["result"])
    return "no tracked IVB/HB"


def fetch_synergy_events(token: str, sid: str) -> tuple[list[dict], str]:
    scan: dict = {}
    try:
        events = bas.fetch_events_pages(
            token,
            pitcher_id=sid,
            max_events=100,
            take=100,
            pause_s=0.05,
            timeout_s=20,
            max_attempts=2,
            stop_before_year=2023,
            retain=lambda ev: bas.event_season_year(ev) in YEARS,
            max_scan=200,
            scan_out=scan,
        )
    except Exception as exc:  # noqa: BLE001
        return [], f"Synergy fetch error: {type(exc).__name__}: {exc}"
    return events, f"scanned={scan.get('scanned')} kept={len(events)} complete={scan.get('complete')}"


def lookup_pitcher(player: dict, row: dict, checkpoint_ids: dict, token_box: dict) -> dict:
    key = holes.player_key(player)
    mlb_id = holes.mlb_id_for(player, {"resolved_ids": checkpoint_ids})
    if not mlb_id:
        try:
            pid = int(row.get("player_id") or player.get("id") or 0)
        except (TypeError, ValueError):
            pid = 0
        mlb_id = pid if pid > 0 else None
    throws = holes.throws_for(player, {"resolved_ids": checkpoint_ids}) or row.get("throws") or ""
    sid = str(row.get("synergy_id") or "") or None
    attempts: list[dict] = []

    if mlb_id:
        pitches, season = holes.pack_ps(mlb_id)
        if pitches:
            return {
                "pitches": pitches,
                "source": f"Prospect Savant {season}",
                "season": season,
                "throws": throws,
                "attempts": [{"source": "Prospect Savant", "season": season, "n": len(pitches)}],
            }
        attempts.append({"source": "Prospect Savant", "result": "no IVB/HB in 2026-2024"})

        for year in YEARS:
            regular, packed = pack_statcast_year(mlb_id, year, "regular")
            if regular:
                return {
                    "pitches": regular,
                    "source": f"MLB Statcast regular {year}",
                    "season": year,
                    "throws": throws,
                    "attempts": attempts + [{"source": "MLB Statcast regular", "season": year, "n": len(regular)}],
                }
            attempts.append({
                "source": "MLB Statcast regular",
                "season": year,
                "result": "pitches but no break" if (packed or {}).get("statcast_pitches") else "empty",
                "statcast_pitches": (packed or {}).get("statcast_pitches"),
            })
        for year in YEARS:
            spring, packed = pack_statcast_year(mlb_id, year, "spring")
            if spring:
                return {
                    "pitches": spring,
                    "source": f"MLB Statcast spring {year}",
                    "season": year,
                    "throws": throws,
                    "attempts": attempts + [{"source": "MLB Statcast spring", "season": year, "n": len(spring)}],
                }
            attempts.append({
                "source": "MLB Statcast spring",
                "season": year,
                "result": "pitches but no break" if (packed or {}).get("statcast_pitches") else "empty",
            })
    else:
        attempts.append({"source": "Prospect Savant/Statcast", "result": "no MLB id"})

    if sid:
        if not token_box.get("token") and not token_box.get("error"):
            try:
                token_box["token"] = bas.login_token(headed=False)
            except Exception as exc:  # noqa: BLE001
                token_box["error"] = f"{type(exc).__name__}: {exc}"
        if token_box.get("token"):
            events, note = fetch_synergy_events(token_box["token"], sid)
            attempts.append({"source": "Synergy events", "note": note, "events": len(events)})
            if events:
                for year in YEARS:
                    pitches, keys, why = pack_synergy_year(events, year)
                    token_box.setdefault("keys", set()).update(keys)
                    if pitches:
                        return {
                            "pitches": pitches,
                            "source": f"Synergy IVB/HB {year} including LBPRC winter",
                            "season": year,
                            "throws": throws,
                            "attempts": attempts + [{"source": "Synergy", "season": year, "n": len(pitches), "keys": keys}],
                        }
                    attempts.append({"source": "Synergy", "season": year, "result": why or "no events that year"})
            else:
                attempts.append({"source": "Synergy", "result": note or "no events"})
        else:
            attempts.append({"source": "Synergy", "result": token_box.get("error") or "no token"})
    else:
        attempts.append({"source": "Synergy", "result": "no synergy_id"})

    return {"pitches": [], "source": "", "season": None, "throws": throws, "attempts": attempts}


def ensure_row(doc: dict, player: dict, arsenal_by: dict) -> dict:
    key = holes.player_key(player)
    row = arsenal_by.get(key)
    if row:
        return row
    row = {
        "name": player["name"],
        "player_id": player["id"],
        "team": player["team"],
        "throws": None,
        "arsenal": [],
        "view": "hitter",
    }
    doc.setdefault("pitchers", []).append(row)
    arsenal_by[key] = row
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--include-santurce",
        action="store_true",
        help="Also fill Cangrejeros de Santurce pitchers (skipped by default).",
    )
    parser.add_argument(
        "--team",
        action="append",
        default=[],
        help="Only process this team abbrev (repeatable), e.g. --team SAN",
    )
    parser.add_argument(
        "--retry",
        action="store_true",
        help="Ignore prior no-plot checkpoint rows for selected targets.",
    )
    args = parser.parse_args()
    RUNS.mkdir(exist_ok=True)

    holes_ckpt = json.loads(holes.CHECKPOINT.read_text()) if holes.CHECKPOINT.is_file() else {}
    resolved = holes_ckpt.get("resolved_ids") or {}
    arsenal_doc = holes.load("pregame_pitcher_arsenals.json")
    arsenal_by = holes.index_rows(arsenal_doc.get("pitchers") or [])
    roster, _hitters = holes.roster_players(include_santurce=args.include_santurce or ("SAN" in args.team))
    if args.team:
        want = {t.upper() for t in args.team}
        roster = [p for p in roster if str(p.get("team") or "").upper() in want]
    before = sum(1 for player in roster if holes.has_movement(arsenal_by.get(holes.player_key(player))))
    ckpt = load_checkpoint()
    if args.retry:
        for player in roster:
            ckpt.get("pitchers", {}).pop(holes.player_key(player), None)
    checked_keys = set((ckpt.get("pitchers") or {}).keys())
    targets = missing_pitchers(arsenal_by, roster, already_checked=checked_keys)
    batch = targets[: args.batch_size]
    token_box: dict = {"keys": set(ckpt.get("synergy_pitch_keys") or [])}
    fills = []
    errors = []

    print(f"plots {before}/94 missing={len(targets)} this_batch={len(batch)}", flush=True)
    for index, player in enumerate(batch, 1):
        key = holes.player_key(player)
        row = ensure_row(arsenal_doc, player, arsenal_by)
        if holes.has_movement(row):
            print(f"[{index}/{len(batch)}] skip {player['team']} {player['name']} already plotted", flush=True)
            continue
        print(f"[{index}/{len(batch)}] {player['team']} {player['name']}", flush=True)
        try:
            found = lookup_pitcher(player, row, resolved, token_box)
            added = False
            if found["pitches"]:
                added = merge_plot_only(
                    row,
                    found["pitches"],
                    source=found["source"],
                    season=found["season"],
                    throws=found["throws"],
                )
            rec = {
                "team": player["team"],
                "name": player["name"],
                "key": key,
                "added": added,
                "source": found.get("source") or None,
                "season": found.get("season"),
                "attempts": found.get("attempts") or [],
            }
            if not added:
                rec["why"] = best_why(found.get("attempts") or [])
            ckpt["pitchers"][key] = rec
            fills.append(rec)
            print(
                f"  {'filled' if added else 'no plot'} {found.get('source') or rec.get('why')}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            rec = {
                "team": player["team"],
                "name": player["name"],
                "key": key,
                "added": False,
                "why": f"{type(exc).__name__}: {exc}",
            }
            ckpt["pitchers"][key] = rec
            errors.append(rec)
            fills.append(rec)
            print(f"  error {rec['why']}", flush=True)

        save_arsenal(arsenal_doc)
        ckpt["synergy_pitch_keys"] = sorted(token_box.get("keys") or [])
        save_checkpoint(ckpt)

    arsenal_by = holes.index_rows(arsenal_doc.get("pitchers") or [])
    after = sum(1 for player in roster if holes.has_movement(arsenal_by.get(holes.player_key(player))))
    added_rows = [row for row in fills if row.get("added")]
    batch_rec = {
        "before": before,
        "after": after,
        "added": [
            {
                "name": row["name"],
                "team": row["team"],
                "source": row.get("source"),
                "season": row.get("season"),
            }
            for row in added_rows
        ],
        "unchanged": [
            {"name": row["name"], "team": row["team"], "why": row.get("why")}
            for row in fills
            if not row.get("added")
        ],
        "errors": errors,
    }
    ckpt.setdefault("batches", []).append(batch_rec)
    save_checkpoint(ckpt)
    holes.dump(REPORT, {
        "updated": ckpt["updated"],
        "plots_before_session": ckpt.get("plots_before_session") or before,
        "plots_now": after,
        "roster": 94,
        "latest_batch": batch_rec,
        "batches": ckpt.get("batches") or [],
    })
    if "plots_before_session" not in ckpt:
        ckpt["plots_before_session"] = before
        save_checkpoint(ckpt)
    print(json.dumps(batch_rec, indent=2, ensure_ascii=False), flush=True)
    print(f"WROTE arsenal JSON plots {before} -> {after}", flush=True)


if __name__ == "__main__":
    main()

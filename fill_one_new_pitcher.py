#!/usr/bin/env python3
"""Fill ONE new pitcher (velo bands + count_hand + summer). Checkpoint-safe.

Skips Prospect Savant movement by default (HTTP 500 hangs kill long runs).
Use --try-movement for Synergy-only IVB/HB attempt.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import synergy_dns_fix  # noqa: F401
import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import fill_movement_plot_batches as move
import fill_remaining_holes as holes
import fill_velo_bands as velo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
PROGRESS = RUNS / "new_pitchers_batch_progress.json"
VELO_CKPT = RUNS / "velo_band_checkpoint.json"
SITU_CKPT = RUNS / "situation_rows_new_ckpt.json"


def dump(path: Path, doc) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def load(name: str):
    return json.loads((DATA / name).read_text())


def norm(name: str) -> str:
    return ligapr.norm_name(name or "")


def synergy_token() -> str:
    p = Path("/tmp/synergy_token.txt")
    if p.is_file() and p.read_text().strip():
        return p.read_text().strip()
    tok = bas.login_token(headed=False)
    p.write_text(tok)
    return tok


def find_row(ars: dict, team: str, name: str) -> dict | None:
    want = norm(name)
    for r in ars.get("pitchers") or []:
        if r.get("team") == team and norm(r.get("name") or "") == want:
            return r
    for r in ars.get("pitchers") or []:
        if norm(r.get("name") or "") == want:
            return r
    return None


def find_adv(adv: dict, team: str, name: str) -> dict | None:
    want = norm(name)
    for r in adv.get("pitchers") or []:
        if r.get("team") == team and norm(r.get("name") or "") == want:
            return r
    return None


def has_velo(row: dict | None) -> bool:
    return any(
        a.get("velo_p25") is not None and a.get("velo_p75") is not None
        for a in ((row or {}).get("arsenal") or [])
    )


def has_chm(row: dict | None) -> bool:
    return bool((row or {}).get("count_hand_mix"))


def fill_velo(row: dict, token: str, ckpt: dict) -> dict:
    sid = str(row.get("synergy_id") or "")
    if not sid:
        return {"status": "no_sid"}
    if has_velo(row) and sid in ckpt and ckpt[sid].get("bands"):
        velo.attach_bands([row], {sid: ckpt[sid]})
        return {"status": "already"}
    events = bas.fetch_events_pages(
        token,
        pitcher_id=sid,
        max_events=velo.MAX_EVENTS,
        take=200,
        pause_s=0.05,
        timeout_s=60,
        max_attempts=2,
    )
    by_code: dict[str, list[float]] = defaultdict(list)
    for ev in events:
        pitch = ev.get("pitch") or {}
        if not isinstance(pitch, dict):
            continue
        speed = pitch.get("pitchSpeedMph")
        if speed is None:
            continue
        try:
            v = float(speed)
        except (TypeError, ValueError):
            continue
        code = bas._pitch_code(pitch.get("pitchKind") or pitch.get("pitchType"))
        by_code[code].append(v)
    packed = {}
    for code, speeds in by_code.items():
        band = velo.bands(speeds)
        if band:
            packed[code] = band
    ckpt[sid] = {"name": row.get("name"), "events": len(events), "bands": packed}
    velo.attach_bands([row], {sid: ckpt[sid]})
    label = ", ".join(f"{c} {b['velo_p25']:.0f}-{b['velo_p75']:.0f}" for c, b in packed.items())
    return {
        "status": "filled" if packed and has_velo(row) else "no_bands",
        "events": len(events),
        "bands": label,
    }


def fill_count_hand(row: dict, token: str, ckpt: dict) -> dict:
    sid = str(row.get("synergy_id") or "")
    if not sid:
        return {"status": "no_sid"}
    if has_chm(row) and "0-0" in (row.get("count_hand_mix") or {}):
        return {"status": "already"}
    years = set(range(2022, 2027))

    def retain(ev: dict) -> bool:
        year = bas.event_season_year(ev) or 0
        return year in years and bas.event_league_id(ev) in set(bas.SAMPLE_LEAGUES.values())

    events = bas.fetch_events_pages(
        token,
        pitcher_id=sid,
        max_events=800,
        take=200,
        pause_s=0.05,
        timeout_s=90,
        max_attempts=2,
        stop_before_year=2022,
        retain=retain,
        max_scan=5000,
    )
    mix = bas.hand_situation_mix(events)
    row["count_hand_mix"] = mix
    ckpt[sid] = {"name": row.get("name"), "events": len(events), "mix_keys": sorted(mix.keys())}
    return {"status": "filled", "events": len(events), "keys": sorted(mix.keys())}


def fill_summer(row: dict, adv: dict | None, mlb_id: int | None) -> dict:
    if (row or {}).get("summer_line") or (adv or {}).get("summer_line"):
        return {"status": "already"}
    mid = int(mlb_id or row.get("mlb_id") or row.get("player_id") or 0)
    if mid <= 0:
        return {"status": "no_mlb_id"}
    lines = bas.fetch_2026_pitcher_summer_lines([mid])
    line = lines.get(mid)
    if not line:
        return {"status": "no_line"}
    row["summer_line"] = line
    if adv is not None:
        adv["summer_line"] = line
    return {
        "status": "filled",
        "summary": f"{line.get('team')} {line.get('level')} {line.get('ip')} IP ERA {line.get('era')}",
    }


def try_synergy_movement(row: dict, token: str) -> dict:
    if holes.has_movement(row):
        return {"status": "already", "source": row.get("ps_source") or row.get("movement_source")}
    sid = str(row.get("synergy_id") or "")
    if not sid:
        return {"status": "no_sid"}
    events, note = move.fetch_synergy_events(token, sid)
    if not events:
        return {"status": "no_events", "note": note}
    for year in move.YEARS:
        pitches, keys, why = move.pack_synergy_year(events, year)
        if pitches:
            added = move.merge_plot_only(
                row,
                pitches,
                source=f"Synergy IVB/HB {year}",
                season=year,
                throws=row.get("throws") or "",
            )
            return {
                "status": "filled" if added else "merge_fail",
                "source": f"Synergy {year}",
                "n": len(pitches),
            }
        if why:
            last_why = why
    return {"status": "no_plot", "why": last_why if "last_why" in dir() else "no IVB/HB"}


def save_all(ars: dict, adv: dict, velo_ckpt: dict, situ_ckpt: dict) -> None:
    ars["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    adv["updated"] = ars["updated"]
    holes.dump(DATA / "pregame_pitcher_arsenals.json", ars)
    holes.dump(DATA / "advance_opposing_pitchers.json", adv)
    dump(VELO_CKPT, velo_ckpt)
    dump(SITU_CKPT, situ_ckpt)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--try-movement", action="store_true")
    ap.add_argument("--skip-one-page", action="store_true")
    args = ap.parse_args()
    team = args.team.upper()
    name = args.name

    RUNS.mkdir(exist_ok=True)
    progress = json.loads(PROGRESS.read_text()) if PROGRESS.is_file() else {"done": {}, "log": []}
    key = f"{team}|{norm(name)}"
    print(f"=== {team} {name} ===", flush=True)

    ars = load("pregame_pitcher_arsenals.json")
    adv_doc = load("advance_opposing_pitchers.json")
    row = find_row(ars, team, name)
    if not row:
        raise SystemExit(f"no arsenal row for {team} {name}")
    adv = find_adv(adv_doc, team, name)
    velo_ckpt = json.loads(VELO_CKPT.read_text()) if VELO_CKPT.is_file() else {}
    situ_ckpt = json.loads(SITU_CKPT.read_text()) if SITU_CKPT.is_file() else {}
    token = synergy_token()

    # Resolve mlb id from roster
    mlb_id = None
    rost = load("lbprc_2025_rosters.json")
    for pack in rost.values():
        if pack.get("teamAbbrev") != team:
            continue
        for p in pack.get("players") or []:
            if norm(p.get("name") or "") == norm(name):
                try:
                    mid = int(p.get("id") or 0)
                except (TypeError, ValueError):
                    mid = 0
                if mid > 0:
                    mlb_id = mid
                break

    rec: dict = {"team": team, "name": name, "mlb_id": mlb_id, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    if args.try_movement:
        try:
            mrec = try_synergy_movement(row, token)
        except Exception as exc:  # noqa: BLE001
            mrec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        rec["movement"] = mrec
        print(f"  move {mrec}", flush=True)
        save_all(ars, adv_doc, velo_ckpt, situ_ckpt)

    try:
        vrec = fill_velo(row, token, velo_ckpt)
    except Exception as exc:  # noqa: BLE001
        vrec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    rec["velo"] = vrec
    print(f"  velo {vrec.get('status')} {vrec.get('bands') or vrec.get('error') or ''}", flush=True)
    save_all(ars, adv_doc, velo_ckpt, situ_ckpt)

    try:
        crec = fill_count_hand(row, token, situ_ckpt)
    except Exception as exc:  # noqa: BLE001
        crec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    rec["count_hand"] = crec
    print(f"  count_hand {crec.get('status')} {crec.get('keys') or crec.get('error') or ''}", flush=True)
    save_all(ars, adv_doc, velo_ckpt, situ_ckpt)

    try:
        srec = fill_summer(row, adv, mlb_id)
    except Exception as exc:  # noqa: BLE001
        srec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    rec["summer"] = srec
    print(f"  summer {srec.get('status')} {srec.get('summary') or srec.get('error') or ''}", flush=True)
    save_all(ars, adv_doc, velo_ckpt, situ_ckpt)

    rec["ok"] = has_velo(row) and has_chm(row)
    rec["movement_present"] = holes.has_movement(row)
    progress["done"][key] = rec
    progress["log"].append(rec)
    progress["updated"] = rec["at"]
    dump(PROGRESS, progress)
    print(f"  DONE ok={rec['ok']} mov={rec['movement_present']}", flush=True)

    if not args.skip_one_page:
        one = ligapr.build_pitcher_one_page()
        ligapr.write_json("pitcher_one_page.json", one)
        print("  one-page rebuilt", flush=True)


if __name__ == "__main__":
    main()

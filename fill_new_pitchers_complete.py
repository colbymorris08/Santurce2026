#!/usr/bin/env python3
"""100% table fill for newly synced pitchers (Murphy + Nov-4 adds).

Fills, when source data exists:
  - Prospect Savant / Statcast / Synergy IVB+HB movement
  - Synergy velo p25/p75 bands (one-page ranges)
  - Synergy count_hand_mix (0-0 / 2K / RISP × hand)
  - Summer AAA/MiLB lines via MLB Stats API
  - Platoon + usage already present are left alone
  - Rebuilds pitcher_one_page.json

Does not invent numbers.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import synergy_dns_fix  # noqa: F401
import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import ensure_roster_player as erp
import fill_missing_platoon_synergy as fmp
import fill_movement_plot_batches as move
import fill_remaining_holes as holes
import fill_velo_bands as velo
import refresh_roster_synergy as rrs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
REPORT = RUNS / "new_pitchers_complete_report.json"
BEFORE = RUNS / "new_pitchers_before_status.json"
AFTER = RUNS / "new_pitchers_after_status.json"

# Official Nov-4 adds + Ponce Murphy/Aponte + any forced names from the user list.
TARGET_NAMES = {
    "blayne enlow",
    "juan javier echavarria",
    "tanner burns",
    "aaron mishoulam",
    "miguel ausua",
    "jose acosta",
    "gabriel cotto",
    "luis rodriguez",
    "albert oliva",
    "jake gozzo",
    "keishin nagamizu",
    "kaito uchino",
    "yusei tobita",
    "luis ramos",
    "shadai colon",
    "sebastian bentz",
    "sean barnett",
    "yariel rodriguez",
    "giovanni soto",
    "matthew rivera",
    "ryder ryan",
    "ryan murphy",
    "john aponte",
}


def dump(path: Path, doc) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def load_json(name: str):
    return json.loads((DATA / name).read_text())


def norm(name: str) -> str:
    return ligapr.norm_name(name or "")


def roster_targets() -> list[dict]:
    rost = load_json("lbprc_2025_rosters.json")
    want_norms = {norm(t) for t in TARGET_NAMES}
    # Also include every Nov-4 official pitcher add
    for r in load_json("roster_official_2026_11_04_added.json").get("added") or []:
        if str(r.get("position") or "").upper() == "P":
            want_norms.add(norm(r.get("name") or ""))
    out = []
    seen = set()
    for pack in rost.values():
        team = pack.get("teamAbbrev")
        for p in pack.get("players") or []:
            name = p.get("name") or ""
            if norm(name) not in want_norms:
                continue
            sk = (team, norm(name))
            if sk in seen:
                continue
            seen.add(sk)
            out.append(
                {
                    "name": name,
                    "team": team,
                    "id": p.get("id"),
                    "throws": p.get("throws"),
                    "jersey": p.get("jersey"),
                    "position": "P",
                }
            )
    out.sort(
        key=lambda p: (
            0 if norm(p["name"]) == "ryanmurphy" else 1,
            p.get("team") or "",
            p.get("name") or "",
        )
    )
    return out


def find_arsenal(ars_by_name: dict, player: dict) -> dict | None:
    return ars_by_name.get((player.get("team"), norm(player.get("name") or ""))) or ars_by_name.get(
        (None, norm(player.get("name") or ""))
    )


def index_arsenals(doc: dict) -> dict:
    by = {}
    for r in doc.get("pitchers") or []:
        by[(r.get("team"), norm(r.get("name") or ""))] = r
        by[(None, norm(r.get("name") or ""))] = r
    return by


def has_velo_bands(row: dict | None) -> bool:
    for pitch in (row or {}).get("arsenal") or []:
        if pitch.get("velo_p25") is not None and pitch.get("velo_p75") is not None:
            return True
    return False


def has_platoon(row: dict | None) -> bool:
    pl = (row or {}).get("platoon") or {}
    return bool(pl.get("vs_RHB") or pl.get("vs_LHB"))


def has_count(row: dict | None) -> bool:
    return bool((row or {}).get("usage_by_count") or (row or {}).get("count_mix"))


def has_count_hand(row: dict | None) -> bool:
    return bool((row or {}).get("count_hand_mix"))


def has_summer(row: dict | None, adv: dict | None) -> bool:
    return bool((row or {}).get("summer_line") or (adv or {}).get("summer_line"))


def heat_located(heat_doc: dict, player: dict, row: dict | None) -> int:
    pitchers = heat_doc.get("pitchers") or {}
    keys = []
    mid = int(player.get("id") or 0)
    if mid > 0:
        keys.append(str(mid))
    if row and row.get("mlb_id"):
        keys.append(str(row["mlb_id"]))
    if row and row.get("synergy_id"):
        keys.append(str(row["synergy_id"]))
    if row and row.get("player_id"):
        keys.append(str(row["player_id"]))
    best = 0
    for k in keys:
        rec = pitchers.get(k)
        if rec:
            best = max(best, int(rec.get("located") or 0))
    # name fallback
    want = norm(player.get("name") or "")
    for rec in pitchers.values():
        if norm(rec.get("name") or "") == want:
            best = max(best, int(rec.get("located") or 0))
    return best


def status_row(player: dict, ars_by: dict, adv_by: dict, heat_doc: dict, one_by: dict) -> dict:
    row = find_arsenal(ars_by, player)
    adv = adv_by.get((player.get("team"), norm(player.get("name") or ""))) or adv_by.get(
        (None, norm(player.get("name") or ""))
    )
    one = one_by.get((player.get("team"), norm(player.get("name") or "")))
    arsenal = (row or {}).get("arsenal") or []
    bands = sum(
        1
        for a in arsenal
        if a.get("velo_p25") is not None and a.get("velo_p75") is not None
    )
    ivb_n = sum(1 for a in arsenal if a.get("ivb") is not None)
    velo_preview = {}
    if one:
        velo_preview = one.get("velo") or {}
    return {
        "name": player["name"],
        "team": player["team"],
        "mlb_id": player.get("id"),
        "pitches": int((row or {}).get("pitches") or (adv or {}).get("pitches") or 0),
        "movement": holes.has_movement(row),
        "movement_source": (row or {}).get("ps_source")
        or (row or {}).get("movement_source")
        or (row or {}).get("synergy_movement_source"),
        "ivb_pitches": ivb_n,
        "velo_bands": has_velo_bands(row),
        "velo_band_pitches": bands,
        "platoon": has_platoon(row) or has_platoon(adv),
        "count": has_count(row),
        "count_hand_mix": has_count_hand(row),
        "summer": has_summer(row, adv),
        "heat_located": heat_located(heat_doc, player, row),
        "one_page": bool(one),
        "one_page_velo": velo_preview,
        "one_page_freq_r": (one or {}).get("freq_r"),
        "synergy_id": (row or {}).get("synergy_id") or (adv or {}).get("synergy_id"),
        "complete": bool(
            int((row or {}).get("pitches") or 0) >= 10
            and holes.has_movement(row)
            and has_velo_bands(row)
            and (has_platoon(row) or has_platoon(adv))
            and has_count(row)
            and has_summer(row, adv)
            and bool(one)
        ),
        # soft-complete when movement/summer genuinely unavailable
        "filled_when_possible": None,
    }


def snapshot(players: list[dict]) -> list[dict]:
    ars = load_json("pregame_pitcher_arsenals.json")
    adv = load_json("advance_opposing_pitchers.json")
    heat = load_json("advance_heatmaps.json")
    one = load_json("pitcher_one_page.json")
    ars_by = index_arsenals(ars)
    adv_by = {}
    for r in adv.get("pitchers") or []:
        adv_by[(r.get("team"), norm(r.get("name") or ""))] = r
        adv_by[(None, norm(r.get("name") or ""))] = r
    one_by = {}
    for team, pack in (one.get("teams") or {}).items():
        for p in pack.get("pitchers") or []:
            one_by[(team, norm(p.get("name") or ""))] = p
    return [status_row(p, ars_by, adv_by, heat, one_by) for p in players]


def synergy_token() -> str:
    path = Path("/tmp/synergy_token.txt")
    if path.is_file():
        tok = path.read_text().strip()
        if tok:
            return tok
    return bas.login_token(headed=False)


def fill_velo_for(player: dict, row: dict, token: str, ckpt: dict) -> dict:
    sid = str(row.get("synergy_id") or "")
    if not sid:
        return {"status": "no_sid"}
    if has_velo_bands(row) and sid in ckpt and (ckpt[sid].get("bands") or {}):
        # re-attach from checkpoint
        velo.attach_bands([row], {sid: ckpt[sid]})
        return {"status": "already" if has_velo_bands(row) else "reattach_fail"}
    try:
        events = bas.fetch_events_pages(
            token,
            pitcher_id=sid,
            max_events=velo.MAX_EVENTS,
            take=200,
            pause_s=0.05,
            timeout_s=60,
            max_attempts=2,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
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
    ckpt[sid] = {"name": player["name"], "events": len(events), "bands": packed}
    velo.attach_bands([row], {sid: ckpt[sid]})
    label = ", ".join(
        f"{c} {b['velo_p25']:.0f}-{b['velo_p75']:.0f}" for c, b in packed.items()
    )
    return {
        "status": "filled" if packed and has_velo_bands(row) else "no_bands",
        "events": len(events),
        "bands": label,
    }


def fill_count_hand_for(player: dict, row: dict, token: str, ckpt: dict) -> dict:
    sid = str(row.get("synergy_id") or "")
    if not sid:
        return {"status": "no_sid"}
    if has_count_hand(row) and "0-0" in (row.get("count_hand_mix") or {}):
        return {"status": "already"}
    years = set(range(2022, 2027))

    def retain(ev: dict) -> bool:
        year = bas.event_season_year(ev) or 0
        return year in years and bas.event_league_id(ev) in set(bas.SAMPLE_LEAGUES.values())

    try:
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
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    mix = bas.hand_situation_mix(events)
    row["count_hand_mix"] = mix
    ckpt[sid] = {
        "name": player["name"],
        "events": len(events),
        "mix_keys": sorted(mix.keys()),
    }
    return {"status": "filled", "events": len(events), "keys": sorted(mix.keys())}


def fill_summer_for(player: dict, row: dict, adv: dict | None) -> dict:
    # Keep arsenal + advance in sync when either side already has a line
    if (adv or {}).get("summer_line") and not (row or {}).get("summer_line"):
        row["summer_line"] = adv["summer_line"]
        line = adv["summer_line"]
        return {
            "status": "copied_from_advance",
            "summary": f"{line.get('team')} {line.get('level')} {line.get('ip')} IP ERA {line.get('era')}",
        }
    if (row or {}).get("summer_line") and adv is not None and not adv.get("summer_line"):
        adv["summer_line"] = row["summer_line"]
        return {"status": "copied_to_advance"}
    if has_summer(row, adv):
        return {"status": "already"}
    mid = int(player.get("id") or row.get("mlb_id") or row.get("player_id") or 0)
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


def pitcher_done(row: dict | None, adv: dict | None) -> bool:
    """Skip when velo bands + count_hand already attached (prior mid-run)."""
    if not row:
        return False
    return has_velo_bands(row) and has_count_hand(row)


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    players = roster_targets()
    print(f"targets={len(players)}", flush=True)
    # Preserve original BEFORE snapshot if present (true pre-fill baseline)
    if BEFORE.is_file():
        before = (json.loads(BEFORE.read_text()).get("players") or [])
        print(f"reusing BEFORE snapshot ({len(before)} players)", flush=True)
    else:
        before = snapshot(players)
        dump(BEFORE, {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "players": before})
    print("BEFORE complete:", sum(1 for r in before if r["complete"]), "/", len(before), flush=True)
    for r in before:
        flags = "".join(
            [
                "M" if r["movement"] else ".",
                "V" if r["velo_bands"] else ".",
                "P" if r["platoon"] else ".",
                "C" if r["count"] else ".",
                "H" if r["count_hand_mix"] else ".",
                "S" if r["summer"] else ".",
                "O" if r["one_page"] else ".",
            ]
        )
        print(
            f"  {r['team']} {r['name']}: pit={r['pitches']} [{flags}] heat={r['heat_located']} src={r['movement_source'] or '-'}",
            flush=True,
        )

    arsenal_doc = load_json("pregame_pitcher_arsenals.json")
    adv_doc = load_json("advance_opposing_pitchers.json")
    ars_by = index_arsenals(arsenal_doc)
    adv_by = {}
    for r in adv_doc.get("pitchers") or []:
        adv_by[(r.get("team"), norm(r.get("name") or ""))] = r
        adv_by[(None, norm(r.get("name") or ""))] = r

    holes_ckpt = json.loads(holes.CHECKPOINT.read_text()) if holes.CHECKPOINT.is_file() else {}
    resolved = holes_ckpt.get("resolved_ids") or {}
    velo_ckpt: dict = json.loads((RUNS / "velo_band_checkpoint.json").read_text()) if (RUNS / "velo_band_checkpoint.json").is_file() else {}
    situ_ckpt: dict = json.loads((RUNS / "situation_rows_new_ckpt.json").read_text()) if (RUNS / "situation_rows_new_ckpt.json").is_file() else {}
    token = synergy_token()
    Path("/tmp/synergy_token.txt").write_text(token)
    print(f"Synergy token ready; velo_ckpt={len(velo_ckpt)} situ_ckpt={len(situ_ckpt)}", flush=True)

    report = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "movement": [],
        "velo": [],
        "count_hand": [],
        "summer": [],
        "synergy_thin": [],
    }
    token_box: dict = {"token": token, "keys": set()}

    # Resume: load prior velo/situation checkpoints if present
    if (RUNS / "velo_band_checkpoint.json").is_file():
        velo_ckpt.update(json.loads((RUNS / "velo_band_checkpoint.json").read_text()))
    if (RUNS / "situation_rows_new_ckpt.json").is_file():
        situ_ckpt.update(json.loads((RUNS / "situation_rows_new_ckpt.json").read_text()))

    for i, player in enumerate(players, 1):
        row = find_arsenal(ars_by, player)
        if row is None:
            row = move.ensure_row(arsenal_doc, player, holes.index_rows(arsenal_doc.get("pitchers") or []))
            ars_by = index_arsenals(arsenal_doc)
            row = find_arsenal(ars_by, player)
        adv = adv_by.get((player.get("team"), norm(player.get("name") or "")))
        print(f"\n[{i}/{len(players)}] {player['team']} {player['name']}", flush=True)

        # Skip Synergy re-pulls when already fully banded + situationed (resume-friendly)
        already_rich = (
            row
            and int(row.get("pitches") or 0) >= 10
            and has_velo_bands(row)
            and has_count_hand(row)
            and has_platoon(row)
            and (holes.has_movement(row) or True)  # movement may be unavailable
        )
        if already_rich and has_velo_bands(row) and has_count_hand(row):
            # Still try movement/summer if missing, but skip heavy velo/count fetches
            if not holes.has_movement(row):
                try:
                    found = move.lookup_pitcher(player, row, resolved, token_box)
                    added = False
                    if found.get("pitches"):
                        added = move.merge_plot_only(
                            row,
                            found["pitches"],
                            source=found["source"],
                            season=found["season"],
                            throws=found.get("throws") or player.get("throws") or "",
                        )
                    print(
                        f"  resume move {'filled' if added else 'no_plot'} {found.get('source') or move.best_why(found.get('attempts') or [])}",
                        flush=True,
                    )
                    report["movement"].append(
                        {
                            "name": player["name"],
                            "team": player["team"],
                            "status": "filled" if added else "no_plot",
                            "source": found.get("source"),
                            "resumed": True,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  resume move error {exc}", flush=True)
            else:
                report["movement"].append(
                    {
                        "name": player["name"],
                        "team": player["team"],
                        "status": "already",
                        "source": row.get("ps_source") or row.get("movement_source"),
                        "resumed": True,
                    }
                )
            if not has_summer(row, adv):
                srec = fill_summer_for(player, row, adv)
                report["summer"].append({"name": player["name"], "team": player["team"], **srec, "resumed": True})
                print(f"  resume summer {srec.get('status')} {srec.get('summary') or ''}", flush=True)
            else:
                report["summer"].append(
                    {"name": player["name"], "team": player["team"], "status": "already", "resumed": True}
                )
            report["velo"].append(
                {"name": player["name"], "team": player["team"], "status": "already", "resumed": True}
            )
            report["count_hand"].append(
                {"name": player["name"], "team": player["team"], "status": "already", "resumed": True}
            )
            print("  resume skip velo/count (already filled)", flush=True)
            arsenal_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            holes.dump(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
            adv_doc["updated"] = arsenal_doc["updated"]
            holes.dump(DATA / "advance_opposing_pitchers.json", adv_doc)
            continue

        # 1) Synergy usage if thin
        pitches = int((row or {}).get("pitches") or (adv or {}).get("pitches") or 0)
        if pitches < 10 or not has_platoon(row):
            try:
                syn = erp.fill_synergy_now(player)
            except Exception as exc:  # noqa: BLE001
                syn = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            report["synergy_thin"].append({"name": player["name"], "team": player["team"], **syn})
            print(f"  synergy {syn.get('status')} pitches={syn.get('pitches')}", flush=True)
            # reload row after synergy write
            arsenal_doc = load_json("pregame_pitcher_arsenals.json")
            adv_doc = load_json("advance_opposing_pitchers.json")
            ars_by = index_arsenals(arsenal_doc)
            adv_by = {}
            for r in adv_doc.get("pitchers") or []:
                adv_by[(r.get("team"), norm(r.get("name") or ""))] = r
                adv_by[(None, norm(r.get("name") or ""))] = r
            row = find_arsenal(ars_by, player)
            adv = adv_by.get((player.get("team"), norm(player.get("name") or "")))

        # 2) Movement
        if row and not holes.has_movement(row):
            try:
                found = move.lookup_pitcher(player, row, resolved, token_box)
                added = False
                if found.get("pitches"):
                    added = move.merge_plot_only(
                        row,
                        found["pitches"],
                        source=found["source"],
                        season=found["season"],
                        throws=found.get("throws") or player.get("throws") or "",
                    )
                rec = {
                    "name": player["name"],
                    "team": player["team"],
                    "status": "filled" if added else "no_plot",
                    "source": found.get("source"),
                    "why": None if added else move.best_why(found.get("attempts") or []),
                }
            except Exception as exc:  # noqa: BLE001
                rec = {
                    "name": player["name"],
                    "team": player["team"],
                    "status": "error",
                    "why": f"{type(exc).__name__}: {exc}",
                }
            report["movement"].append(rec)
            print(f"  move {rec['status']} {rec.get('source') or rec.get('why')}", flush=True)
        else:
            report["movement"].append(
                {
                    "name": player["name"],
                    "team": player["team"],
                    "status": "already",
                    "source": (row or {}).get("ps_source") or (row or {}).get("movement_source"),
                }
            )
            print(f"  move already {(row or {}).get('ps_source') or (row or {}).get('movement_source')}", flush=True)

        # 3) Velo bands
        if row:
            vrec = fill_velo_for(player, row, token_box.get("token") or token, velo_ckpt)
            report["velo"].append({"name": player["name"], "team": player["team"], **vrec})
            print(f"  velo {vrec.get('status')} {vrec.get('bands') or vrec.get('error') or ''}", flush=True)

        # 4) count_hand_mix
        if row:
            crec = fill_count_hand_for(player, row, token_box.get("token") or token, situ_ckpt)
            report["count_hand"].append({"name": player["name"], "team": player["team"], **crec})
            print(f"  count_hand {crec.get('status')} {crec.get('keys') or crec.get('error') or ''}", flush=True)

        # 5) Summer line
        if row is not None:
            srec = fill_summer_for(player, row, adv)
            report["summer"].append({"name": player["name"], "team": player["team"], **srec})
            print(f"  summer {srec.get('status')} {srec.get('summary') or ''}", flush=True)

        # Persist after each pitcher
        arsenal_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        holes.dump(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
        adv_doc["updated"] = arsenal_doc["updated"]
        holes.dump(DATA / "advance_opposing_pitchers.json", adv_doc)
        dump(RUNS / "velo_band_checkpoint.json", velo_ckpt)
        dump(RUNS / "situation_rows_new_ckpt.json", situ_ckpt)
        time.sleep(0.05)

    # Platoon copy + one-page rebuild
    try:
        fmp.copy_arsenal_platoon_to_advance()
    except Exception as exc:  # noqa: BLE001
        print(f"platoon copy warn: {exc}", flush=True)
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)

    after = snapshot(players)
    dump(AFTER, {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "players": after})
    report["before_complete"] = sum(1 for r in before if r["complete"])
    report["after_complete"] = sum(1 for r in after if r["complete"])
    report["before"] = before
    report["after"] = after
    dump(REPORT, report)

    print("\n=== BEFORE → AFTER ===", flush=True)
    by_before = {(r["team"], norm(r["name"])): r for r in before}
    for r in after:
        b = by_before.get((r["team"], norm(r["name"]))) or {}
        def mark(x):
            return "Y" if x else "."
        print(
            f"{r['team']:3} {r['name']:28} "
            f"pit {b.get('pitches')}→{r['pitches']} "
            f"mov {mark(b.get('movement'))}→{mark(r['movement'])} "
            f"vel {mark(b.get('velo_bands'))}→{mark(r['velo_bands'])} "
            f"chm {mark(b.get('count_hand_mix'))}→{mark(r['count_hand_mix'])} "
            f"sum {mark(b.get('summer'))}→{mark(r['summer'])} "
            f"complete {mark(b.get('complete'))}→{mark(r['complete'])} "
            f"veloFB={((r.get('one_page_velo') or {}).get('FB') or '')[:20]!r}",
            flush=True,
        )
    print(
        f"\ncomplete {report['before_complete']} → {report['after_complete']} / {len(players)}",
        flush=True,
    )
    print(f"report → {REPORT}", flush=True)


if __name__ == "__main__":
    main()

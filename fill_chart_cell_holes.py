#!/usr/bin/env python3
"""Fill remaining opposing-pitcher chart cells from Synergy/Savant/Statcast.

Targets:
  - count_hand_mix + IZ% (zone rates)
  - velo bands
  - SLG heat maps
  - thin/empty arsenals (usages/platoon)
  - summer / movement only when a real source returns them

Does not invent IVB/HB or summer lines.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import synergy_dns_fix  # noqa: F401
import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import ensure_roster_player as erp
import fill_remaining_holes as holes
import fill_velo_bands as velo
import refresh_roster_synergy as rrs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
REPORT = RUNS / "chart_cell_holes_report.json"
BEFORE = RUNS / "chart_cell_holes_before.json"
AFTER = RUNS / "chart_cell_holes_after.json"


def dump(path: Path, doc) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def load(name: str):
    return json.loads((DATA / name).read_text())


def norm(name: str) -> str:
    return ligapr.norm_name(name or "")


def synergy_token() -> str:
    path = Path("/tmp/synergy_token.txt")
    if path.is_file() and path.read_text().strip():
        return path.read_text().strip()
    tok = bas.login_token(headed=False)
    path.write_text(tok)
    return tok


def find_arsenal(ars: list[dict], name: str, team: str, mlb_id) -> dict | None:
    for p in ars:
        if p.get("name") == name and p.get("team") == team:
            return p
    if mlb_id and int(mlb_id) > 0:
        mid = int(mlb_id)
        for p in ars:
            if p.get("mlb_id") == mid or p.get("player_id") == mid:
                return p
    for p in ars:
        if p.get("name") == name:
            return p
    return None


def heat_located(heat: dict, row: dict | None) -> int:
    if not row:
        return 0
    pitchers = heat.get("pitchers") or {}
    best = 0
    for key in (
        str(row.get("mlb_id") or ""),
        str(row.get("player_id") or ""),
        str(row.get("synergy_id") or ""),
    ):
        if key and key in pitchers:
            best = max(best, int((pitchers[key] or {}).get("located") or 0))
    want = norm(row.get("name") or "")
    for rec in pitchers.values():
        if norm((rec or {}).get("name") or "") == want:
            best = max(best, int((rec or {}).get("located") or 0))
    return best


def status_for(op: dict, team: str, ars: list[dict], heat: dict) -> dict:
    row = find_arsenal(ars, op["name"], team, op.get("mlb_id"))
    chm = (row or {}).get("count_hand_mix") or {}
    arsenal = (row or {}).get("arsenal") or []
    pitches = int((row or {}).get("pitches") or 0)
    # Real p25–p75 only — one-page average-as-band (e.g. "95-95") does not count.
    velo_any = any(
        a.get("velo_p25") is not None and a.get("velo_p75") is not None for a in arsenal
    )
    movement = holes.has_movement(row)
    missing = []
    if not row:
        missing.append("no_arsenal_row")
    if pitches <= 0:
        missing.append("no_pitches")
    if not arsenal:
        missing.append("no_arsenal")
    if not (chm.get("overall") and chm.get("0-0") and chm.get("2_strikes")):
        missing.append("count_hand")
    if pitches > 0 and not chm.get("iz"):
        missing.append("iz")
    if pitches > 0 and not velo_any:
        missing.append("velo_bands")
    if pitches > 0 and not ((row or {}).get("platoon") or {}).get("vs_RHB") and not (
        (row or {}).get("platoon") or {}
    ).get("vs_LHB"):
        missing.append("platoon")
    hl = heat_located(heat, row)
    if pitches > 0 and hl <= 0:
        missing.append("heat")
    if pitches > 0 and not movement:
        missing.append("movement")
    if not (row or {}).get("summer_line"):
        missing.append("summer")
    return {
        "team": team,
        "name": op["name"],
        "mlb_id": op.get("mlb_id") or (row or {}).get("mlb_id"),
        "player_id": (row or {}).get("player_id"),
        "synergy_id": (row or {}).get("synergy_id"),
        "pitches": pitches,
        "heat_located": hl,
        "has_iz": bool(chm.get("iz")),
        "iz_overall_l": ((chm.get("iz") or {}).get("vs_LHB") or {}).get("overall"),
        "iz_overall_r": ((chm.get("iz") or {}).get("vs_RHB") or {}).get("overall"),
        "count_hand": bool(chm.get("overall") and chm.get("0-0") and chm.get("2_strikes")),
        "velo_bands": velo_any,
        "movement": movement,
        "summer": bool((row or {}).get("summer_line")),
        "missing": missing,
        "movement_source": (row or {}).get("movement_source")
        or (row or {}).get("ps_source")
        or (row or {}).get("synergy_movement_source"),
    }


def snapshot() -> list[dict]:
    one = load("pitcher_one_page.json")
    ars = load("pregame_pitcher_arsenals.json")["pitchers"]
    heat = load("advance_heatmaps.json")
    out = []
    for team, pack in (one.get("teams") or {}).items():
        if team == "SAN":
            continue
        for op in pack.get("pitchers") or []:
            out.append(status_for(op, team, ars, heat))
    return out


def roster_player(team: str, name: str, mlb_id) -> dict:
    rost = load("lbprc_2025_rosters.json")
    want = norm(name)
    for pack in rost.values():
        if pack.get("teamAbbrev") != team:
            continue
        for p in pack.get("players") or []:
            if norm(p.get("name") or "") == want:
                return {
                    "name": p.get("name") or name,
                    "team": team,
                    "id": p.get("id") or mlb_id,
                    "throws": p.get("throws"),
                    "position": "P",
                    "jersey": p.get("jersey"),
                }
    return {
        "name": name,
        "team": team,
        "id": mlb_id if mlb_id and int(mlb_id) > 0 else None,
        "position": "P",
    }


def fill_count_hand(row: dict, token: str) -> dict:
    sid = str(row.get("synergy_id") or "")
    if not sid:
        return {"status": "no_sid"}
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
    return {
        "status": "filled",
        "events": len(events),
        "keys": sorted(mix.keys()),
        "has_iz": bool(mix.get("iz")),
    }


def fill_velo(row: dict, token: str, ckpt: dict) -> dict:
    sid = str(row.get("synergy_id") or "")
    if not sid:
        return {"status": "no_sid"}
    if any(a.get("velo_p25") is not None for a in (row.get("arsenal") or [])):
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
    return {
        "status": "filled" if packed else "no_bands",
        "events": len(events),
        "bands": len(packed),
    }


def fill_heat(player: dict, row: dict, token: str) -> dict:
    sid = str(row.get("synergy_id") or "")
    if not sid:
        return {"status": "no_sid"}
    # Prefer ensure path which also refreshes usages if thin
    mid = int(player.get("id") or row.get("mlb_id") or 0)
    search_player = {
        "name": player["name"],
        "team": player["team"],
        "mlb_id": mid if mid > 0 else None,
        "player_id": mid if mid else row.get("player_id"),
    }
    raw = bas.fetch_events_pages(
        token,
        pitcher_id=sid,
        max_events=2500,
        take=200,
        pause_s=0.05,
        timeout_s=90,
        max_attempts=2,
        retain=bas.event_in_heat_fallback_scan,
        max_scan=8000,
    )
    heat_doc = load("advance_heatmaps.json")
    p_heat = dict(heat_doc.get("pitchers") or {})
    heat = rrs.make_heat(search_player, sid, raw, True) if raw else rrs.empty_heat(
        player["name"], player["team"], sid, mid if mid > 0 else None, True
    )
    heat_key = str(mid if mid > 0 else sid)
    p_heat[heat_key] = heat
    p_heat[sid] = heat
    heat_doc["pitchers"] = p_heat
    heat_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dump(DATA / "advance_heatmaps.json", heat_doc)
    return {
        "status": "filled" if int(heat.get("located") or 0) > 0 else "empty",
        "located": int(heat.get("located") or 0),
        "events": len(raw),
        "window": heat.get("window_used"),
    }


def fill_summer(player: dict, row: dict, adv: dict | None) -> dict:
    if (row or {}).get("summer_line"):
        if adv is not None and not adv.get("summer_line"):
            adv["summer_line"] = row["summer_line"]
        return {"status": "already"}
    if adv and adv.get("summer_line"):
        row["summer_line"] = adv["summer_line"]
        return {"status": "copied_from_advance"}
    mid = int(player.get("id") or row.get("mlb_id") or row.get("player_id") or 0)
    if mid <= 0:
        return {"status": "no_mlb_id", "reason": "No positive MLB id for Stats API summer lookup"}
    lines = bas.fetch_2026_pitcher_summer_lines([mid])
    line = lines.get(mid)
    if not line:
        return {"status": "unavailable", "reason": "No 2026 MiLB/MLB summer pitching line on Stats API"}
    row["summer_line"] = line
    if adv is not None:
        adv["summer_line"] = line
    return {
        "status": "filled",
        "summary": f"{line.get('team')} {line.get('level')} {line.get('ip')} IP ERA {line.get('era')}",
    }


def fill_movement(player: dict, row: dict) -> dict:
    if holes.has_movement(row):
        return {"status": "already", "source": row.get("movement_source") or row.get("ps_source")}
    try:
        rec = erp.fill_movement_now(player)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    # reload arsenal row after movement write
    return {"status": rec.get("status") or "attempted", **{k: v for k, v in rec.items() if k != "status"}}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--try-movement",
        action="store_true",
        help="Also attempt Prospect Savant/Statcast/Synergy IVB+HB (can hang)",
    )
    ap.add_argument(
        "--summer-all",
        action="store_true",
        help="Attempt Stats API summer for every arm missing a line",
    )
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    before = snapshot()
    dump(BEFORE, {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "players": before})
    miss_c = Counter()
    for r in before:
        for m in r["missing"]:
            miss_c[m] += 1
    print(f"BEFORE opposing={len(before)} hole_players={sum(1 for r in before if r['missing'])}")
    print(" missing", dict(miss_c))
    murphy = next((r for r in before if r["name"] == "Ryan Murphy"), None)
    print(
        " Murphy BEFORE:",
        {
            k: murphy.get(k)
            for k in (
                "pitches",
                "has_iz",
                "iz_overall_l",
                "iz_overall_r",
                "count_hand",
                "heat_located",
                "velo_bands",
                "movement",
                "summer",
                "missing",
            )
        }
        if murphy
        else None,
    )

    # Primary: Synergy chart cells (zone rates / count-hand / heat / velo / usages).
    # Summer/movement are optional follow-ups — PS movement hangs kill long runs.
    synergy_flags = {
        "count_hand",
        "iz",
        "heat",
        "velo_bands",
        "no_pitches",
        "no_arsenal",
        "no_arsenal_row",
        "platoon",
    }
    targets = [
        r
        for r in before
        if any(m in synergy_flags for m in r["missing"])
        or r["name"] == "Ryan Murphy"
        or (args.summer_all and "summer" in r["missing"])
    ]
    print(f"targets={len(targets)} try_movement={args.try_movement} summer_all={args.summer_all}", flush=True)

    arsenal_doc = load("pregame_pitcher_arsenals.json")
    adv_doc = load("advance_opposing_pitchers.json")
    ars_by_team_name = {
        (r.get("team"), norm(r.get("name") or "")): r for r in arsenal_doc.get("pitchers") or []
    }
    adv_by = {
        (r.get("team"), norm(r.get("name") or "")): r for r in adv_doc.get("pitchers") or []
    }
    velo_ckpt = (
        json.loads((RUNS / "velo_band_checkpoint.json").read_text())
        if (RUNS / "velo_band_checkpoint.json").is_file()
        else {}
    )
    token = synergy_token()
    Path("/tmp/synergy_token.txt").write_text(token)
    print("Synergy token ready", flush=True)

    report = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actions": [],
        "unavailable": [],
    }

    for i, st in enumerate(targets, 1):
        team, name = st["team"], st["name"]
        missing = set(st["missing"])
        # Murphy: force refresh of zone rates even if already present
        force_murphy = name == "Ryan Murphy"
        need_synergy_boot = bool(
            missing & {"no_pitches", "no_arsenal", "no_arsenal_row", "platoon"}
            or (not st.get("synergy_id") and (missing & synergy_flags))
        )
        need_chm = bool(missing & {"count_hand", "iz"}) or force_murphy
        need_heat = "heat" in missing or force_murphy
        need_velo = "velo_bands" in missing
        need_summer = "summer" in missing and (args.summer_all or force_murphy or st.get("mlb_id"))
        # Only attempt summer for targets already selected; skip pure-summer unless --summer-all
        if "summer" in missing and not args.summer_all and not force_murphy:
            # still try summer cheaply for arms we are already touching
            need_summer = True
        need_move = args.try_movement and ("movement" in missing)
        if not (need_synergy_boot or need_chm or need_heat or need_velo or need_summer or need_move or force_murphy):
            continue

        print(f"[{i}/{len(targets)}] {team} {name} miss={sorted(missing)}", flush=True)
        player = roster_player(team, name, st.get("mlb_id"))
        row = ars_by_team_name.get((team, norm(name)))
        adv = adv_by.get((team, norm(name)))
        action = {"team": team, "name": name, "before_missing": sorted(missing)}

        if need_synergy_boot or (not row) or (not row.get("synergy_id") and need_chm):
            try:
                boot = erp.fill_synergy_now(player)
            except Exception as exc:  # noqa: BLE001
                boot = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            action["synergy_boot"] = boot
            print(f"  synergy_boot {boot.get('status')} {boot.get('error') or boot.get('pitches') or ''}", flush=True)
            # reload docs after boot writes
            arsenal_doc = load("pregame_pitcher_arsenals.json")
            adv_doc = load("advance_opposing_pitchers.json")
            ars_by_team_name = {
                (r.get("team"), norm(r.get("name") or "")): r
                for r in arsenal_doc.get("pitchers") or []
            }
            adv_by = {
                (r.get("team"), norm(r.get("name") or "")): r for r in adv_doc.get("pitchers") or []
            }
            row = ars_by_team_name.get((team, norm(name)))
            adv = adv_by.get((team, norm(name)))
            token = synergy_token()

        if row is None:
            action["skipped"] = "no_arsenal_row_after_boot"
            report["actions"].append(action)
            if need_summer or need_move:
                report["unavailable"].append(
                    {
                        "team": team,
                        "name": name,
                        "fields": sorted(missing & {"summer", "movement", "count_hand", "iz", "heat"}),
                        "reason": "No arsenal/Synergy identity resolved",
                    }
                )
            continue

        if need_chm and row.get("synergy_id"):
            try:
                crec = fill_count_hand(row, token)
            except Exception as exc:  # noqa: BLE001
                crec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            action["count_hand"] = crec
            print(f"  count_hand {crec.get('status')} iz={crec.get('has_iz')} {crec.get('error') or ''}", flush=True)

        if need_velo and row.get("synergy_id"):
            try:
                vrec = fill_velo(row, token, velo_ckpt)
            except Exception as exc:  # noqa: BLE001
                vrec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            action["velo"] = vrec
            print(f"  velo {vrec.get('status')} {vrec.get('error') or ''}", flush=True)

        if need_heat and row.get("synergy_id"):
            try:
                hrec = fill_heat(player, row, token)
            except Exception as exc:  # noqa: BLE001
                hrec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            action["heat"] = hrec
            print(f"  heat {hrec.get('status')} located={hrec.get('located')} {hrec.get('error') or ''}", flush=True)

        if need_summer:
            try:
                srec = fill_summer(player, row, adv)
            except Exception as exc:  # noqa: BLE001
                srec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            action["summer"] = srec
            print(f"  summer {srec.get('status')} {srec.get('summary') or srec.get('reason') or srec.get('error') or ''}", flush=True)
            if srec.get("status") in {"unavailable", "no_mlb_id"}:
                report["unavailable"].append(
                    {
                        "team": team,
                        "name": name,
                        "fields": ["summer"],
                        "reason": srec.get("reason") or srec.get("status"),
                    }
                )

        if need_move:
            try:
                mrec = fill_movement(player, row)
            except Exception as exc:  # noqa: BLE001
                mrec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            action["movement"] = mrec
            print(f"  movement {mrec.get('status')} {mrec.get('error') or mrec.get('source') or ''}", flush=True)
            # movement writes its own files; reload arsenal
            arsenal_doc = load("pregame_pitcher_arsenals.json")
            ars_by_team_name = {
                (r.get("team"), norm(r.get("name") or "")): r
                for r in arsenal_doc.get("pitchers") or []
            }
            row = ars_by_team_name.get((team, norm(name))) or row
            if mrec.get("status") not in {"already", "filled", "ok", "updated"} and not holes.has_movement(row):
                report["unavailable"].append(
                    {
                        "team": team,
                        "name": name,
                        "fields": ["movement"],
                        "reason": mrec.get("error")
                        or mrec.get("status")
                        or "No Prospect Savant / Statcast / Synergy IVB+HB sample",
                    }
                )

        report["actions"].append(action)
        # persist arsenal/advance after each pitcher
        arsenal_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        dump(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
        dump(DATA / "advance_opposing_pitchers.json", adv_doc)
        dump(RUNS / "velo_band_checkpoint.json", velo_ckpt)
        dump(REPORT, report)
        time.sleep(0.05)

    # Rebuild one-page
    print("Rebuilding pitcher_one_page.json", flush=True)
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)

    after = snapshot()
    dump(AFTER, {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "players": after})
    after_c = Counter()
    for r in after:
        for m in r["missing"]:
            after_c[m] += 1
    murphy_a = next((r for r in after if r["name"] == "Ryan Murphy"), None)
    report["before_missing"] = dict(miss_c)
    report["after_missing"] = dict(after_c)
    report["murphy_before"] = murphy
    report["murphy_after"] = murphy_a
    dump(REPORT, report)
    print(f"AFTER hole_players={sum(1 for r in after if r['missing'])}")
    print(" missing", dict(after_c))
    print(
        " Murphy AFTER:",
        {
            k: murphy_a.get(k)
            for k in (
                "pitches",
                "has_iz",
                "iz_overall_l",
                "iz_overall_r",
                "count_hand",
                "heat_located",
                "velo_bands",
                "movement",
                "summer",
                "missing",
            )
        }
        if murphy_a
        else None,
    )


if __name__ == "__main__":
    main()

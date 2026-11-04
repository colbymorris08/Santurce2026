#!/usr/bin/env python3
"""Aggressive re-fill for Nov-4 stub players still empty/unresolved.

Resolves MLB ids via free_agent_pool + Synergy mlbProfileId, then pulls
events with broad 2025–2026 any-league fallback (Pioneer/indy/showcase OK).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import ensure_roster_player as erp
import fill_missing_platoon_synergy as fmp
import refresh_roster_synergy as rrs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
REPORT = RUNS / "nov4_empty_aggressive_report.json"
PRIOR = RUNS / "nov4_stub_synergy_report.json"


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc, *, compact: bool = False) -> None:
    path = DATA / name
    if compact:
        path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def targets() -> list[dict]:
    # Prefer prior report empties/unresolved; always include Murphy/Aponte/Ponce adds
    want = []
    if PRIOR.exists():
        prior = json.loads(PRIOR.read_text())
        for row in prior.get("players") or []:
            if row.get("status") in ("empty", "unresolved", "no_events", "error"):
                want.append(row)
    # Ensure Ponce specials
    names = {(r.get("team"), rrs.norm(r.get("name") or "")) for r in want}
    for name, team, pos in (
        ("Ryan Murphy", "PON", "P"),
        ("John Aponte", "PON", "P"),
        ("Hemmanuel Rosario", "PON", "C"),
        ("Jose Rivera", "PON", "INF"),
    ):
        key = (team, rrs.norm(name))
        if key not in names:
            want.append({"name": name, "team": team, "position": pos, "status": "forced"})
    return want


def pick_window(raw: list[dict]) -> tuple[list[dict], str]:
    stats, window = rrs.select_stats_window(raw)
    recent = [e for e in raw if bas.event_season_year(e) in (2025, 2026)]
    if recent and (not stats or len(recent) >= max(40, 3 * len(stats))):
        return recent, "2025-2026 any league"
    if stats:
        return stats, window
    if raw:
        return raw, "unfiltered"
    return [], "none"


def resolve_mlb(player: dict) -> tuple[int | None, str | None]:
    mid = player.get("mlb_id") or (player.get("player_id") if int(player.get("player_id") or 0) > 0 else None)
    try:
        mid = int(mid) if mid else None
    except (TypeError, ValueError):
        mid = None
    if mid and mid > 0:
        return mid, None
    fa = erp.lookup_mlb_id_from_fa(player["name"], pitcher=str(player.get("position") or "").upper() in erp.PITCHER_POS)
    syn_mid, syn_sid = erp.lookup_mlb_id_from_synergy(player["name"])
    return (fa or syn_mid), syn_sid


def resolve_sid(client: rrs.Synergy, player: dict, hint_sid: str | None) -> tuple[str, dict | None, list]:
    if hint_sid:
        return str(hint_sid), {"id": hint_sid, "name": player["name"]}, []
    forced = str(player.get("synergy_id") or "")
    if forced and player.get("status") != "unresolved":
        # Still re-search unresolved; keep known sid for empty
        pass
    if forced:
        return forced, {"id": forced, "name": player.get("synergy_name") or player["name"]}, []
    sid, matched, rows = client.search({"name": player["name"], "mlb_id": player.get("mlb_id")})
    if sid:
        return str(sid), matched, rows
    want = rrs.norm(player["name"])
    exact = [r for r in rows if rrs.norm(r.get("name") or "") == want]
    mid = player.get("mlb_id")
    if mid:
        for r in exact or rows:
            raw = r.get("mlbProfileId") or r.get("ids")
            try:
                if int(raw or 0) == int(mid):
                    return str(r["id"]), r, rows
            except (TypeError, ValueError):
                pass
    with_mlb = []
    for r in exact:
        raw = r.get("mlbProfileId") or r.get("ids")
        try:
            if int(raw or 0) > 0:
                with_mlb.append(r)
        except (TypeError, ValueError):
            pass
    if len(with_mlb) == 1:
        return str(with_mlb[0]["id"]), with_mlb[0], rows
    if len(exact) == 1 and exact[0].get("id"):
        return str(exact[0]["id"]), exact[0], rows
    # Variants: first initial, flip tokens for Hispanic names already covered by search
    parts = player["name"].split()
    if len(parts) >= 2:
        for alt in (f"{parts[0][0]} {parts[-1]}", parts[-1], f"{parts[-1]} {parts[0]}"):
            sid2, matched2, rows2 = client.search({"name": alt, "mlb_id": player.get("mlb_id")})
            if sid2:
                return str(sid2), matched2, rows2
            rows.extend(rows2)
    return "", None, rows


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    jobs = targets()
    print(f"targets={len(jobs)}")
    client = rrs.Synergy()

    hitter_doc = load("advance_opposing_hitters.json")
    pitcher_doc = load("advance_opposing_pitchers.json")
    arsenal_doc = load("pregame_pitcher_arsenals.json")
    heat_doc = load("advance_heatmaps.json")
    hitter_by = {rrs.row_key(r): r for r in hitter_doc.get("hitters") or []}
    pitcher_by = {rrs.row_key(r): r for r in pitcher_doc.get("pitchers") or []}
    arsenal_by = {rrs.row_key(r): r for r in arsenal_doc.get("pitchers") or []}
    h_heat = dict(heat_doc.get("hitters") or {})
    p_heat = dict(heat_doc.get("pitchers") or {})

    results = []
    for i, row in enumerate(jobs, 1):
        name = row["name"]
        team = row["team"]
        pos = str(row.get("position") or "").upper()
        kind = "pitcher" if pos in erp.PITCHER_POS or pos == "P" else "hitter"
        print(f"[{i}/{len(jobs)}] {kind} {team} {name}", flush=True)

        mid, hint_sid = resolve_mlb(row)
        if mid and mid > 0:
            # rewrite roster if synthetic
            rost = erp.find_roster(name, None, team)
            if rost and int(rost.get("id") or 0) <= 0:
                try:
                    erp.rewrite_roster_id({**rost, "team": team}, mid)
                    print(f"  roster id → {mid}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  roster rewrite warn: {exc}")
        player = {
            "name": name,
            "team": team,
            "mlb_id": mid if mid and mid > 0 else None,
            "player_id": mid if mid and mid > 0 else int(row.get("player_id") or 0),
            "position": pos or ("P" if kind == "pitcher" else "H"),
            "synergy_id": row.get("synergy_id"),
            "status": row.get("status"),
        }
        try:
            sid, matched, cands = resolve_sid(client, player, hint_sid)
            if not sid:
                results.append(
                    {
                        **player,
                        "kind": kind,
                        "status": "unfindable",
                        "reason": "no unique Synergy/MLB id",
                        "candidates": [c.get("name") for c in cands[:12]],
                    }
                )
                print("  UNFINDABLE")
                continue
            raw = client.events(sid=sid, pitcher=kind == "pitcher")
            if kind == "pitcher" and len(raw) < 100:
                raw = bas.fetch_events_pages(
                    client.token,
                    pitcher_id=sid,
                    max_events=2000,
                    take=200,
                    pause_s=0.03,
                    timeout_s=20,
                    max_attempts=3,
                    stop_before_year=2018,
                    retain=lambda e: True,
                    max_scan=5000,
                )
            stats, window = pick_window(raw)
            key = rrs.row_key(player)
            if kind == "hitter":
                old = hitter_by.get(key) or {}
                if stats:
                    hitter_by[key] = rrs.merge_hitter(old, player, sid, stats, window)
                else:
                    old.update({"name": name, "team": team, "synergy_id": sid, "mlb_id": player["mlb_id"]})
                    hitter_by[key] = old
                h_heat[sid] = (
                    rrs.make_heat(player, sid, raw, False)
                    if raw
                    else rrs.empty_heat(name, team, sid, player.get("mlb_id"), False)
                )
                new = hitter_by[key]
                try:
                    bas.merge_spray_into_pregame([new])
                except Exception as exc:  # noqa: BLE001
                    print(f"  spray warn: {exc}")
                result = {
                    **player,
                    "kind": kind,
                    "status": "populated" if (new.get("overall") or {}).get("pitches") else ("empty" if not raw else "thin"),
                    "synergy_id": sid,
                    "synergy_name": (matched or {}).get("name"),
                    "raw_events": len(raw),
                    "stats_events": len(stats),
                    "window_used": window,
                    "pitches": (new.get("overall") or {}).get("pitches"),
                    "spray": len(new.get("spray_points") or []),
                }
            else:
                old = pitcher_by.get(key) or {}
                packed = bas.aggregate_pitcher_count_mixes(stats or raw)
                mix = packed.get(sid) or (next(iter(packed.values())) if packed else {})
                mix = dict(mix or {})
                mix.update(
                    {
                        "name": name,
                        "team": team,
                        "mlb_id": player["mlb_id"],
                        "player_id": player["player_id"] or None,
                        "synergy_id": sid,
                        "throws": old.get("throws") or "R",
                        "window_used": window,
                        "source": window,
                    }
                )
                if old.get("summer_line"):
                    mix["summer_line"] = old["summer_line"]
                pitcher_by[key] = {**old, **mix}
                ar = arsenal_by.get(key) or {
                    "name": name,
                    "player_id": player["player_id"] or None,
                    "team": team,
                    "throws": "R",
                    "arsenal": [],
                    "view": "hitter",
                }
                ar = rrs.merge_arsenal(ar, mix, stats or raw)
                ar.update(
                    {
                        "name": name,
                        "team": team,
                        "player_id": player["player_id"] or None,
                        "mlb_id": player["mlb_id"],
                        "synergy_id": sid,
                    }
                )
                if mix.get("platoon"):
                    ar["platoon"] = mix["platoon"]
                if mix.get("count_mix"):
                    ar["count_mix"] = mix["count_mix"]
                ar.pop("note", None)
                arsenal_by[key] = ar
                heat_key = str(player["mlb_id"] or sid)
                p_heat[heat_key] = (
                    rrs.make_heat(player, sid, raw, True)
                    if raw
                    else rrs.empty_heat(name, team, sid, player.get("mlb_id"), True)
                )
                p_heat[sid] = p_heat[heat_key]
                result = {
                    **player,
                    "kind": kind,
                    "status": "populated" if (mix.get("pitches") or 0) > 0 else ("empty" if not raw else "thin"),
                    "synergy_id": sid,
                    "synergy_name": (matched or {}).get("name"),
                    "raw_events": len(raw),
                    "stats_events": len(stats),
                    "window_used": window,
                    "pitches": mix.get("pitches") or 0,
                    "arsenal": len(ar.get("arsenal") or []),
                }
            results.append(result)
            print(
                f"  {result['status']} sid={sid} raw={result['raw_events']} "
                f"kept={result['stats_events']} pitches={result.get('pitches')} {window}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            results.append({**player, "kind": kind, "status": "error", "error": repr(exc)})
            print(f"  ERROR {exc!r}")
        time.sleep(0.15)

    # Summer lines for pitchers with mlb ids
    need = [int(r["mlb_id"]) for r in pitcher_by.values() if int(r.get("mlb_id") or 0) > 0 and not r.get("summer_line")]
    if need:
        lines = bas.fetch_2026_pitcher_summer_lines(sorted(set(need)))
        for r in pitcher_by.values():
            mid = int(r.get("mlb_id") or 0)
            if mid in lines:
                r["summer_line"] = lines[mid]

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    hitter_doc["hitters"] = sorted(hitter_by.values(), key=lambda r: (r.get("team") or "", r.get("name") or ""))
    hitter_doc["updated"] = now
    pitcher_doc["pitchers"] = sorted(pitcher_by.values(), key=lambda r: (r.get("team") or "", r.get("name") or ""))
    pitcher_doc["updated"] = now
    arsenal_doc["pitchers"] = sorted(arsenal_by.values(), key=lambda r: (r.get("team") or "", r.get("name") or ""))
    arsenal_doc["updated"] = now
    heat_doc["hitters"], heat_doc["pitchers"], heat_doc["updated"] = h_heat, p_heat, now
    dump("advance_opposing_hitters.json", hitter_doc)
    dump("advance_opposing_pitchers.json", pitcher_doc)
    dump("pregame_pitcher_arsenals.json", arsenal_doc)
    dump("advance_heatmaps.json", heat_doc, compact=True)

    mixes = {str(r["synergy_id"]): r for r in pitcher_by.values() if r.get("synergy_id") and r.get("pitches")}
    try:
        bas.merge_synergy_count_into_arsenals(mixes)
    except Exception as exc:  # noqa: BLE001
        print("merge warn", exc)
    try:
        fmp.copy_arsenal_platoon_to_advance()
    except Exception as exc:  # noqa: BLE001
        print("platoon warn", exc)
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)

    by_status = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    summary = {"updated": now, "targets": len(jobs), "by_status": by_status, "players": results}
    REPORT.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"done": True, "by_status": by_status}, indent=2))
    for r in results:
        print(
            f"  {r['status']:12} {r['team']} {r['name']:22} "
            f"pitches={r.get('pitches')} spray={r.get('spray')} sid={r.get('synergy_id')}"
        )


if __name__ == "__main__":
    main()

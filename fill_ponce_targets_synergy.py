#!/usr/bin/env python3
"""Targeted Synergy fill for Ponce red-roster players (hitters + NPB pitchers)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import fill_missing_count_synergy as fmc
import fill_missing_platoon_synergy as fmp
import refresh_roster_synergy as roster

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

TARGETS = [
    # existing
    {"name": "William Simoneit", "team": "PON", "mlb_id": 686766, "kind": "hitter"},
    {"name": "Jose Torres", "team": "PON", "mlb_id": 686958, "kind": "hitter"},
    {"name": "Jacob Reimer", "team": "PON", "mlb_id": 702544, "kind": "hitter"},
    # new hitters
    {"name": "Colton Becker", "team": "PON", "mlb_id": 813851, "kind": "hitter"},
    {"name": "Austin Murr", "team": "PON", "mlb_id": 689456, "kind": "hitter"},
    {"name": "John Bay", "team": "PON", "mlb_id": 700083, "kind": "hitter"},
    # new NPB pitchers (synthetic negative ids)
    {"name": "Keito Matsuura", "team": "PON", "mlb_id": -799554792, "kind": "pitcher"},
    {"name": "Kyohei Yamashiro", "team": "PON", "mlb_id": -172871700, "kind": "pitcher"},
    {"name": "Kai Yokogawa", "team": "PON", "mlb_id": -173172553, "kind": "pitcher"},
]

NAME_VARIANTS = {
    "william simoneit": ["Will Simoneit", "William Simoneit"],
    "keito matsuura": ["Keito Matsuura", "Matsuura Keito"],
    "kyohei yamashiro": ["Kyohei Yamashiro", "Yamashiro Kyohei"],
    "kai yokogawa": ["Kai Yokogawa", "Yokogawa Kai"],
}


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc, *, compact: bool = False) -> None:
    path = DATA / name
    if compact:
        path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def upsert_spray(hitter: dict) -> None:
    spray = load("pregame_spray_charts.json")
    rows = spray.get("synergy") or []
    mid = int(hitter.get("mlb_id") or 0)
    key = ligapr.norm_name(hitter.get("name") or "")
    payload = {
        "name": hitter["name"],
        "synergy_id": hitter.get("synergy_id"),
        "team": hitter.get("team"),
        "mlb_id": hitter.get("mlb_id") if mid > 0 else None,
        "player_id": mid if mid > 0 else None,
        "source": "Synergy events (landingLocationX/Y)",
        "splits": hitter.get("spray_splits") or [],
        "points": hitter.get("spray_points") or [],
    }
    found = False
    for i, row in enumerate(rows):
        rid = int(row.get("mlb_id") or row.get("player_id") or 0)
        if (mid > 0 and rid == mid) or ligapr.norm_name(row.get("name") or "") == key:
            rows[i] = {**row, **payload}
            found = True
            break
    if not found and (payload["points"] or payload["splits"]):
        rows.append(payload)
    spray["synergy"] = rows
    spray["updated_synergy"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dump("pregame_spray_charts.json", spray)


def main() -> None:
    client = roster.Synergy()
    hitter_doc = load("advance_opposing_hitters.json")
    pitcher_doc = load("advance_opposing_pitchers.json")
    arsenal_doc = load("pregame_pitcher_arsenals.json")
    heat_doc = load("advance_heatmaps.json")

    hitter_by = {roster.row_key(r): r for r in hitter_doc.get("hitters") or []}
    pitcher_by = {roster.row_key(r): r for r in pitcher_doc.get("pitchers") or []}
    arsenal_by = {roster.row_key(r): r for r in arsenal_doc.get("pitchers") or []}
    h_heat = dict(heat_doc.get("hitters") or {})
    p_heat = dict(heat_doc.get("pitchers") or {})

    report = []
    for i, player in enumerate(TARGETS, 1):
        kind = player["kind"]
        key = roster.row_key(player)
        old = (hitter_by if kind == "hitter" else pitcher_by).get(key) or {}
        sid = str(old.get("synergy_id") or "")
        print(f"[{i}/{len(TARGETS)}] {kind} {player['name']} sid={sid or '-'}", flush=True)

        # Name variants for Synergy search
        search_player = dict(player)
        variants = NAME_VARIANTS.get(roster.norm(player["name"]), [])
        if not sid:
            # temporarily monkey variants into roster.NAME_VARIANTS
            roster.NAME_VARIANTS[roster.norm(player["name"])] = variants
            sid_found, matched, candidates = client.search(search_player)
            sid = str(sid_found or "")
            if not sid and variants:
                for alt in variants:
                    alt_player = {**player, "name": alt}
                    sid_found, matched, candidates = client.search(alt_player)
                    if sid_found:
                        sid = str(sid_found)
                        break
            if not sid:
                # last resort via count filler resolver
                try:
                    sid = str(fmc.resolve_sid(client.token, player["name"], player.get("mlb_id")) or "")
                except Exception as exc:  # noqa: BLE001
                    print(f"  resolve_sid fail: {exc}")
            if not sid:
                print(f"  UNRESOLVED candidates={[c.get('name') for c in (candidates or [])][:8]}")
                report.append({**player, "status": "unresolved"})
                continue

        raw = client.events(sid=sid, pitcher=kind == "pitcher")
        stats_events, window = roster.select_stats_window(raw)
        # NPB / farm: if sample windows empty, keep any 2025-2026 events
        if not stats_events and raw:
            stats_events = [e for e in raw if bas.event_season_year(e) in (2025, 2026)]
            window = "2025-2026 any league" if stats_events else window
            if not stats_events:
                stats_events = raw
                window = "unfiltered"

        if kind == "hitter":
            if stats_events:
                new = roster.merge_hitter(old, player, sid, stats_events, window)
            else:
                new = {**old, "name": player["name"], "team": "PON", "synergy_id": sid, "mlb_id": player["mlb_id"]}
            hitter_by[key] = new
            h_heat[sid] = roster.make_heat(player, sid, raw, False) if raw else roster.empty_heat(
                player["name"], "PON", sid, player.get("mlb_id"), False
            )
            upsert_spray(new)
            print(
                f"  window={window} pitches={(new.get('overall') or {}).get('pitches')} "
                f"spray={len(new.get('spray_points') or [])} heat_located={h_heat[sid].get('located')}"
            )
            report.append(
                {
                    **player,
                    "status": "ok",
                    "synergy_id": sid,
                    "window": window,
                    "pitches": (new.get("overall") or {}).get("pitches"),
                    "spray": len(new.get("spray_points") or []),
                }
            )
        else:
            mix_pack = bas.aggregate_pitcher_count_mixes(stats_events or raw)
            mix = mix_pack.get(sid) or (next(iter(mix_pack.values())) if mix_pack else {})
            mix = dict(mix or {})
            mix["synergy_id"] = sid
            mix["name"] = player["name"]
            mix["team"] = "PON"
            mix["mlb_id"] = player["mlb_id"]
            mix["source"] = window

            adv = {**old}
            adv.update(
                {
                    "name": player["name"],
                    "team": "PON",
                    "mlb_id": player["mlb_id"],
                    "synergy_id": sid,
                    "throws": adv.get("throws") or "L",
                    "pitches": mix.get("pitches") or adv.get("pitches") or 0,
                    "platoon": mix.get("platoon") or adv.get("platoon") or {},
                    "count_mix": mix.get("count_mix") or adv.get("count_mix") or {},
                    "hand_n": mix.get("hand_n"),
                    "leagues": mix.get("leagues"),
                    "years": mix.get("years"),
                    "source": window,
                }
            )
            pitcher_by[key] = adv

            ar = arsenal_by.get(key) or {
                "name": player["name"],
                "player_id": player["mlb_id"],
                "team": "PON",
                "throws": "L",
                "arsenal": [],
            }
            ar = roster.merge_arsenal(ar, mix, stats_events or raw)
            ar["name"] = player["name"]
            ar["team"] = "PON"
            ar["player_id"] = player["mlb_id"]
            ar["mlb_id"] = player["mlb_id"]
            ar["throws"] = ar.get("throws") or "L"
            if mix.get("platoon"):
                ar["platoon"] = mix["platoon"]
            if mix.get("count_mix"):
                ar["count_mix"] = mix["count_mix"]
            arsenal_by[key] = ar

            heat_key = str(player["mlb_id"] if player["mlb_id"] else sid)
            p_heat[heat_key] = roster.make_heat(player, sid, raw, True) if raw else roster.empty_heat(
                player["name"], "PON", sid, player.get("mlb_id"), True
            )
            # also index by synergy id
            p_heat[sid] = p_heat[heat_key]
            print(
                f"  window={window} pitches={mix.get('pitches')} arsenal={len(ar.get('arsenal') or [])} "
                f"platoon={bool((mix.get('platoon') or {}).get('vs_RHB') or (mix.get('platoon') or {}).get('vs_LHB'))}"
            )
            report.append(
                {
                    **player,
                    "status": "ok",
                    "synergy_id": sid,
                    "window": window,
                    "pitches": mix.get("pitches"),
                    "arsenal": len(ar.get("arsenal") or []),
                }
            )

        time.sleep(0.15)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    hitter_doc["hitters"] = sorted(hitter_by.values(), key=lambda r: (r.get("team") or "", r.get("name") or ""))
    hitter_doc["select_all"] = bas.select_all_aggregate(hitter_doc["hitters"])
    hitter_doc["updated"] = now
    pitcher_doc["pitchers"] = sorted(pitcher_by.values(), key=lambda r: (r.get("team") or "", r.get("name") or ""))
    pitcher_doc["updated"] = now
    arsenal_doc["pitchers"] = sorted(arsenal_by.values(), key=lambda r: (r.get("team") or "", r.get("name") or ""))
    arsenal_doc["updated"] = now
    heat_doc["hitters"] = h_heat
    heat_doc["pitchers"] = p_heat
    heat_doc["updated"] = now

    dump("advance_opposing_hitters.json", hitter_doc)
    dump("advance_opposing_pitchers.json", pitcher_doc)
    dump("pregame_pitcher_arsenals.json", arsenal_doc)
    dump("advance_heatmaps.json", heat_doc, compact=True)

    # Rebuild one-page + copy platoon
    fmp.copy_arsenal_platoon_to_advance()
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)

    out = ROOT / "runs" / "ponce_red_synergy_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"updated": now, "players": report}, indent=2) + "\n")
    print(f"report → {out}")
    for row in report:
        print(f"  {row['status']:10} {row['name']:20} {row.get('synergy_id')} pitches={row.get('pitches')} spray={row.get('spray')} arsenal={row.get('arsenal')}")


if __name__ == "__main__":
    main()

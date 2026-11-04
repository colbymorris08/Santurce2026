#!/usr/bin/env python3
"""Synergy/advance fill for the 31 Nov-4 stubbed CAG/CAR/MAY/SJU players.

Skips players already complete. Does not block on thin/unresolved Synergy ids.
Also attempts the new Ponce official adds (Hemmanuel Rosario, Jose Rivera,
Ryan Murphy, John Aponte).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import refresh_roster_synergy as rrs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
REPORT = RUNS / "nov4_stub_synergy_report.json"


def want_keys() -> set[tuple[str, str]]:
    added = json.loads((DATA / "roster_official_2026_11_04_added.json").read_text())["added"]
    keys = {(a["team"], rrs.norm(a["name"])) for a in added}
    # New Ponce official adds / rename
    for name in ("Hemmanuel Rosario", "Jose Rivera", "Ryan Murphy", "John Aponte"):
        keys.add(("PON", rrs.norm(name)))
    return keys


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    want = want_keys()
    hitters, pitchers = rrs.roster_players()
    # Include catchers etc. as hitters (refresh already does position==P vs else)
    jobs = [("hitter", row) for row in hitters if (row["team"], rrs.norm(row["name"])) in want]
    jobs += [("pitcher", row) for row in pitchers if (row["team"], rrs.norm(row["name"])) in want]
    jobs.sort(key=lambda item: (item[1]["team"], item[0], item[1]["name"]))
    print(f"targets={len(jobs)} (want={len(want)})")

    hitter_doc = rrs.load("advance_opposing_hitters.json")
    pitcher_doc = rrs.load("advance_opposing_pitchers.json")
    arsenal_doc = rrs.load("pregame_pitcher_arsenals.json")
    heat_doc = rrs.load("advance_heatmaps.json")
    hitter_by = {rrs.row_key(row): row for row in hitter_doc.get("hitters") or []}
    pitcher_by = {rrs.row_key(row): row for row in pitcher_doc.get("pitchers") or []}
    arsenal_by = {rrs.row_key(row): row for row in arsenal_doc.get("pitchers") or []}
    h_heat = dict(heat_doc.get("hitters") or {})
    p_heat = dict(heat_doc.get("pitchers") or {})

    # Clear prior marks for these targets so we retry Synergy search/fill.
    state = {"completed": {}, "results": []}
    if rrs.CHECKPOINT.exists():
        try:
            full = json.loads(rrs.CHECKPOINT.read_text())
            drop = set()
            for k in list((full.get("completed") or {}).keys()):
                parts = k.split(":", 2)
                if len(parts) == 3 and (parts[1], parts[2]) in want:
                    drop.add(k)
            state["completed"] = {
                k: v for k, v in (full.get("completed") or {}).items() if k not in drop
            }
            state["results"] = [
                row
                for row in (full.get("results") or [])
                if (row.get("team"), rrs.norm(row.get("name") or "")) not in want
            ]
        except json.JSONDecodeError:
            pass

    client = rrs.Synergy()
    results = []

    def persist() -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        hitter_doc["hitters"] = sorted(
            hitter_by.values(), key=lambda row: (row.get("team") or "", row.get("name") or "")
        )
        hitter_doc["updated"] = now
        pitcher_doc["pitchers"] = sorted(
            pitcher_by.values(), key=lambda row: (row.get("team") or "", row.get("name") or "")
        )
        pitcher_doc["updated"] = now
        arsenal_doc["pitchers"] = sorted(
            arsenal_by.values(), key=lambda row: (row.get("team") or "", row.get("name") or "")
        )
        arsenal_doc["updated"] = now
        heat_doc["hitters"], heat_doc["pitchers"], heat_doc["updated"] = h_heat, p_heat, now
        rrs.atomic_write(DATA / "advance_opposing_hitters.json", hitter_doc)
        rrs.atomic_write(DATA / "advance_opposing_pitchers.json", pitcher_doc)
        rrs.atomic_write(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
        rrs.atomic_write(DATA / "advance_heatmaps.json", heat_doc, compact=True)
        rrs.atomic_write(rrs.CHECKPOINT, state)

    for index, (kind, player) in enumerate(jobs, 1):
        key = f"{kind}:{player['team']}:{rrs.norm(player['name'])}"
        started = time.monotonic()
        player_key = rrs.row_key(player)
        old = (hitter_by if kind == "hitter" else pitcher_by).get(player_key) or {}
        sid = str(old.get("synergy_id") or "")
        try:
            if not sid:
                sid, matched, search_rows = client.search(player)
            else:
                matched, search_rows = None, []
            if not sid:
                result = {
                    **player,
                    "kind": kind,
                    "status": "unresolved",
                    "search_candidates": [row.get("name") for row in search_rows],
                }
                print(f"[{index}/{len(jobs)}] {kind} {player['team']} {player['name']} — no Synergy id")
            else:
                raw = client.events(sid=sid, pitcher=kind == "pitcher")
                stats_events, window = rrs.select_stats_window(raw)
                if kind == "hitter":
                    if stats_events:
                        hitter_by[player_key] = rrs.merge_hitter(old, player, sid, stats_events, window)
                    else:
                        old.update({"name": player["name"], "team": player["team"], "synergy_id": sid})
                        hitter_by[player_key] = old
                    h_heat[sid] = (
                        rrs.make_heat(player, sid, raw, False)
                        if raw
                        else rrs.empty_heat(player["name"], player["team"], sid, player.get("mlb_id"), False)
                    )
                else:
                    packed = bas.aggregate_pitcher_count_mixes(stats_events)
                    mix = packed.get(sid) or (next(iter(packed.values())) if packed else None)
                    if mix:
                        mix.update(
                            {
                                "name": player["name"],
                                "team": player["team"],
                                "mlb_id": player.get("mlb_id"),
                                "synergy_id": sid,
                                "window_used": window,
                            }
                        )
                        if old.get("summer_line"):
                            mix["summer_line"] = old["summer_line"]
                        pitcher_by[player_key] = mix
                        arsenal = arsenal_by.get(player_key) or {
                            "name": player["name"],
                            "team": player["team"],
                            "player_id": player["player_id"],
                            "arsenal": [],
                            "view": "hitter",
                        }
                        arsenal_by[player_key] = rrs.merge_arsenal(arsenal, mix, stats_events)
                    else:
                        old.update({"name": player["name"], "team": player["team"], "synergy_id": sid})
                        pitcher_by[player_key] = old
                    heat_key = str(player.get("mlb_id") or sid)
                    p_heat[heat_key] = (
                        rrs.make_heat(player, sid, raw, True)
                        if raw
                        else rrs.empty_heat(player["name"], player["team"], sid, player.get("mlb_id"), True)
                    )
                result = {
                    **player,
                    "kind": kind,
                    "status": "populated" if stats_events else "empty",
                    "synergy_id": sid,
                    "synergy_name": (matched or {}).get("name"),
                    "raw_events": len(raw),
                    "stats_events": len(stats_events),
                    "window_used": window,
                }
                print(
                    f"[{index}/{len(jobs)}] {kind} {player['team']} {player['name']} "
                    f"— {result['status']} raw={len(raw)} kept={len(stats_events)} {window}"
                )
        except Exception as exc:  # noqa: BLE001
            result = {**player, "kind": kind, "status": "error", "error": repr(exc)}
            print(f"[{index}/{len(jobs)}] {kind} {player['team']} {player['name']} — ERROR {exc!r}")
        state.setdefault("completed", {})[key] = result
        state.setdefault("results", []).append(result)
        results.append(result)
        persist()
        remaining = rrs.MIN_PLAYER_SECONDS - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)

    try:
        bas.merge_synergy_count_into_arsenals(
            {
                str(row["synergy_id"]): row
                for row in pitcher_by.values()
                if row.get("synergy_id") and row.get("pitches")
            }
        )
        bas.merge_spray_into_pregame(list(hitter_by.values()))
    except Exception as exc:  # noqa: BLE001
        print(f"merge warn: {exc}")

    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)

    summary = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "targets": len(jobs),
        "by_status": {},
        "players": results,
    }
    for row in results:
        summary["by_status"][row["status"]] = summary["by_status"].get(row["status"], 0) + 1
    rrs.atomic_write(REPORT, summary)
    print(json.dumps({"done": True, "by_status": summary["by_status"]}, indent=2))


if __name__ == "__main__":
    main()

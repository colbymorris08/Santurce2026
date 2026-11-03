#!/usr/bin/env python3
"""Ensure a roster player appears in every pregame tab that applies to their role.

Pitchers → Pitcher Advance, One Page, Stuff, Bullpen gate, Matchup/HVP gate,
          TTH, Strategy (roster), card_whiff when SAN.
Catchers → TTH (pop/arm), plus hitter tabs below.
Hitters  → Hitter Advance, Spray, BR card, Lineup spots (SAN), OF tabs gate,
          Strategy (roster), card_whiff when SAN. (TTH only if C.)
Both     → lbprc_2025_rosters.json (must already list them).

This only stubs / wires roster identity into the site caches. Run the Synergy
fillers afterward for real samples:

  python3 fill_missing_count_synergy.py
  python3 fill_missing_platoon_synergy.py --new-only
  python3 fill_movement_plot_batches.py --team CAG
  python3 build_ligapr_daily.py   # or ligapr.build_pitcher_one_page()
  python3 fill_card_whiff.py      # SAN strategy whiff
  python3 build_pregame_cards.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import build_ligapr_daily as ligapr

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

PITCHER_POS = {"P", "RHP", "LHP", "RHR", "LHR", "RHS", "LHS"}
CATCHER_POS = {"C"}


def load(name: str) -> Any:
    return json.loads((DATA / name).read_text())


def dump(name: str, doc: Any) -> None:
    (DATA / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def find_roster(name: str | None, mlb_id: int | None, team: str | None) -> dict | None:
    rosters = load("lbprc_2025_rosters.json")
    want = ligapr.norm_name(name or "")
    for pack in rosters.values():
        abbr = pack.get("teamAbbrev")
        if team and abbr != team.upper():
            continue
        for p in pack.get("players") or []:
            pid = int(p.get("id") or 0)
            if mlb_id and pid == mlb_id:
                return {**p, "team": abbr, "team_name": pack.get("teamName")}
            if want and ligapr.norm_name(p.get("name") or "") == want:
                return {**p, "team": abbr, "team_name": pack.get("teamName")}
    return None


def is_pitcher(player: dict) -> bool:
    return str(player.get("position") or "").upper() in PITCHER_POS


def is_catcher(player: dict) -> bool:
    return str(player.get("position") or "").upper() in CATCHER_POS


def ensure_list_row(rows: list, player: dict, id_keys: tuple[str, ...], stub: dict) -> str:
    mid = int(player.get("id") or 0)
    key = ligapr.norm_name(player.get("name") or "")
    for row in rows:
        for ik in id_keys:
            try:
                if mid > 0 and int(row.get(ik) or 0) == mid:
                    row["name"] = player["name"]
                    row["team"] = player["team"]
                    return "updated"
            except (TypeError, ValueError):
                pass
        if ligapr.norm_name(row.get("name") or "") == key:
            row["name"] = player["name"]
            row["team"] = player["team"]
            if mid > 0:
                for ik in id_keys:
                    if ik in row or ik.endswith("_id"):
                        row[ik] = mid
                        break
            return "updated"
    rows.append(stub)
    return "added"


def ensure_pitcher(player: dict) -> dict[str, str]:
    mid = int(player.get("id") or 0)
    actions = {}

    ar = load("pregame_pitcher_arsenals.json")
    rows = ar.get("pitchers") or []
    actions["arsenal"] = ensure_list_row(
        rows,
        player,
        ("player_id", "mlb_id"),
        {
            "name": player["name"],
            "player_id": mid if mid > 0 else None,
            "team": player["team"],
            "throws": player.get("throws"),
            "mlb_debut": None,
            "level_hint": None,
            "stuff_plus": None,
            "pitches": 0,
            "arsenal": [],
            "usage_by_count": {},
            "count_mix": {},
            "platoon": {},
            "view": "hitter",
            "note": "Roster stub — run Synergy / Prospect Savant fills",
        },
    )
    ar["pitchers"] = rows
    ar["updated"] = now()
    dump("pregame_pitcher_arsenals.json", ar)

    stuff = load("pregame_opposing_stuff.json")
    stuff_rows = stuff if isinstance(stuff, list) else (stuff.get("pitchers") or [])
    actions["stuff"] = ensure_list_row(
        stuff_rows,
        player,
        ("player_id", "mlb_id"),
        {
            "name": player["name"],
            "player_id": mid if mid > 0 else None,
            "team": player["team"],
            "mlb_debut": None,
            "birth_country": None,
            "stuff_plus": None,
            "pitches": None,
            "arsenal": [],
            "source": "unavailable",
            "level_hint": None,
        },
    )
    if isinstance(stuff, list):
        dump("pregame_opposing_stuff.json", stuff_rows)
    else:
        stuff["pitchers"] = stuff_rows
        dump("pregame_opposing_stuff.json", stuff)

    adv = load("advance_opposing_pitchers.json")
    arows = adv.get("pitchers") or []
    actions["advance"] = ensure_list_row(
        arows,
        player,
        ("mlb_id", "player_id"),
        {
            "name": player["name"],
            "mlb_id": mid if mid > 0 else None,
            "team": player["team"],
            "throws": player.get("throws"),
            "synergy_id": None,
            "pitches": 0,
            "summer_line": None,
            "platoon": {},
            "count_mix": {},
        },
    )
    adv["pitchers"] = arows
    adv["updated"] = now()
    dump("advance_opposing_pitchers.json", adv)

    heat = load("advance_heatmaps.json")
    heats = heat.get("pitchers")
    if isinstance(heats, dict) and mid > 0:
        key = str(mid)
        if key not in heats:
            heats[key] = {
                "name": player["name"],
                "mlb_id": mid,
                "team": player["team"],
                "located": False,
                "vs_r": {},
                "vs_l": {},
            }
            heat["pitchers"] = heats
            dump("advance_heatmaps.json", heat)
            actions["heatmap"] = "added"
        else:
            heats[key]["name"] = player["name"]
            heats[key]["team"] = player["team"]
            actions["heatmap"] = "updated"
    elif isinstance(heats, list):
        actions["heatmap"] = ensure_list_row(
            heats,
            player,
            ("mlb_id", "player_id"),
            {
                "name": player["name"],
                "mlb_id": mid if mid > 0 else None,
                "team": player["team"],
                "vs_r": {},
                "vs_l": {},
            },
        )
        heat["pitchers"] = heats
        dump("advance_heatmaps.json", heat)

    return actions


def ensure_hitter(player: dict) -> dict[str, str]:
    mid = int(player.get("id") or 0)
    actions = {}

    adv = load("advance_opposing_hitters.json")
    rows = adv.get("hitters") or []
    actions["advance"] = ensure_list_row(
        rows,
        player,
        ("mlb_id", "player_id"),
        {
            "name": player["name"],
            "mlb_id": mid if mid > 0 else None,
            "team": player["team"],
            "bats": player.get("bats"),
            "throws": player.get("throws"),
            "position": player.get("position"),
            "synergy_id": None,
            "overall": {},
            "by_pitch_type": [],
            "vs_l_by_pitch_type": [],
            "vs_r_by_pitch_type": [],
            "risp_by_pitch_type": [],
            "spray_points": [],
            "spray_splits": {},
            "summer_line": None,
        },
    )
    adv["hitters"] = rows
    adv["updated"] = now()
    dump("advance_opposing_hitters.json", adv)

    br = load("pregame_baserunning_bunting.json")
    if isinstance(br, list):
        actions["br"] = ensure_list_row(
            br,
            player,
            ("player_id", "mlb_id"),
            {
                "player_id": mid if mid > 0 else None,
                "name": player["name"],
                "team": player["team"],
                "sb": None,
                "cs": None,
                "sac": None,
            },
        )
        dump("pregame_baserunning_bunting.json", br)

    spray = load("pregame_spray_charts.json")
    syn = spray.get("synergy") or []
    if isinstance(syn, list) and mid > 0 and not any(int(r.get("player_id") or r.get("mlb_id") or 0) == mid for r in syn):
        syn.append(
            {
                "player_id": mid,
                "mlb_id": mid,
                "name": player["name"],
                "team": player["team"],
                "points": [],
                "splits": {},
            }
        )
        spray["synergy"] = syn
        dump("pregame_spray_charts.json", spray)
        actions["spray"] = "added"
    else:
        actions["spray"] = "ok"

    return actions


def ensure_shared(player: dict) -> dict[str, str]:
    mid = int(player.get("id") or 0)
    actions = {}

    # TTH / catcher-pop sheet is pitchers + catchers only (no INF/OF).
    if is_pitcher(player) or is_catcher(player):
        tth = load("pregame_tth_pop.json")
        rows = tth.get("players") or []
        actions["tth"] = ensure_list_row(
            rows,
            player,
            ("player_id",),
            {
                "player_id": mid if mid > 0 else None,
                "name": player["name"],
                "team": player["team"],
                "position": "P" if is_pitcher(player) else "C",
                "jersey": player.get("jersey") or "",
                "status": "Active",
                "time_to_home": None,
                "catcher_pop_time": None,
                "catcher_arm": None,
                "notes": "",
            },
        )
        tth["players"] = rows
        dump("pregame_tth_pop.json", tth)
    else:
        actions["tth"] = "skipped"

    # Strategy card reads live roster — no stub file. card_whiff for SAN.
    if player.get("team") == "SAN" and mid > 0:
        whiff = load("card_whiff.json")
        wrows = whiff.get("players") or whiff.get("pitchers") or whiff.get("hitters") or []
        # structure varies; only touch list-of-players shapes
        if isinstance(wrows, list):
            actions["card_whiff"] = ensure_list_row(
                wrows,
                player,
                ("mlb_id", "player_id", "id"),
                {
                    "name": player["name"],
                    "mlb_id": mid,
                    "team": "SAN",
                    "role": "pitcher" if is_pitcher(player) else "hitter",
                    "whiff": None,
                },
            )
            if "players" in whiff:
                whiff["players"] = wrows
            dump("card_whiff.json", whiff)

    return actions


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--id", type=int, default=None, help="MLBAM id")
    ap.add_argument("--team", default=None, help="Team abbrev filter")
    ap.add_argument("--rebuild-one-page", action="store_true")
    args = ap.parse_args()

    player = find_roster(args.name, args.id, args.team)
    if not player:
        raise SystemExit(
            f"Not on roster yet: {args.name!r} id={args.id} team={args.team}. "
            "Add them to data/lbprc_2025_rosters.json first."
        )

    print(f"{player['team']} {player['name']} ({player.get('position')}) id={player.get('id')}")
    actions = ensure_shared(player)
    if is_pitcher(player):
        actions.update(ensure_pitcher(player))
        role = "pitcher"
    else:
        actions.update(ensure_hitter(player))
        role = "hitter"

    if args.rebuild_one_page or role == "pitcher":
        one = ligapr.build_pitcher_one_page()
        ligapr.write_json("pitcher_one_page.json", one)
        actions["one_page"] = "rebuilt"

    print(json.dumps({"role": role, "actions": actions}, indent=2))
    print(
        "Next: Synergy/PS fills for real samples "
        "(count, platoon/freq, movement, heatmaps, whiff, summer lines), then push."
    )


if __name__ == "__main__":
    main()

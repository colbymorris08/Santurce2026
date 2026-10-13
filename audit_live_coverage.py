#!/usr/bin/env python3
"""Recount strict advance coverage from the JSON currently served by the site."""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "runs" / "live_coverage_report.json"


def load(name: str):
    return json.loads((DATA / name).read_text())


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def key(row: dict) -> tuple[str, str]:
    return str(row.get("team") or ""), norm(str(row.get("name") or ""))


def side_has(side: dict | None, field: str) -> bool:
    return bool(side and sum(int(value or 0) for value in (side.get(field) or [])) > 0)


def hitter_heat_complete(record: dict | None) -> bool:
    return bool(
        record
        and side_has(record.get("vs_r"), "ab")
        and side_has(record.get("vs_r"), "swings")
        and side_has(record.get("vs_l"), "ab")
        and side_has(record.get("vs_l"), "swings")
    )


def pitcher_heat_complete(record: dict | None) -> bool:
    return bool(
        record
        and side_has(record.get("vs_r"), "ab")
        and side_has(record.get("vs_l"), "ab")
    )


def hitter_advance_complete(row: dict | None) -> bool:
    return bool(
        row
        and int((row.get("overall") or {}).get("pitches") or 0)
        and row.get("vs_r_by_pitch_type")
        and row.get("vs_l_by_pitch_type")
    )


def pitcher_advance_complete(row: dict | None) -> bool:
    arsenal = (row or {}).get("arsenal") or []
    return bool(
        arsenal
        and all(
            pitch.get("ivb") is not None
            and (pitch.get("hb_hitter") is not None or pitch.get("hb") is not None)
            for pitch in arsenal
        )
        and all(pitch.get("whiff") is not None for pitch in arsenal)
    )


def main() -> None:
    rosters = load("lbprc_2025_rosters.json")
    hitters = load("advance_opposing_hitters.json").get("hitters") or []
    pitchers = load("advance_opposing_pitchers.json").get("pitchers") or []
    arsenals = load("pregame_pitcher_arsenals.json").get("pitchers") or []
    heat = load("advance_heatmaps.json")

    roster = []
    for team in rosters.values():
        if team.get("teamAbbrev") == "SAN":
            continue
        for player in team.get("players") or []:
            roster.append({
                "team": team.get("teamAbbrev"),
                "name": player.get("name"),
                "id": int(player.get("id") or 0),
                "role": "pitcher" if player.get("position") == "P" else "hitter",
            })

    hitter_by = {key(row): row for row in hitters}
    pitcher_by = {key(row): row for row in pitchers}
    arsenal_by = {key(row): row for row in arsenals}
    result = {}

    for role in ("hitter", "pitcher"):
        players = [player for player in roster if player["role"] == role]
        stat_bar = heat_map = all_data = 0
        for player in players:
            player_key = key(player)
            row = (hitter_by if role == "hitter" else pitcher_by).get(player_key)
            has_line = bool(row and row.get("summer_line"))
            if role == "hitter":
                heat_record = (heat.get("hitters") or {}).get(str((row or {}).get("synergy_id") or ""))
                has_heat = hitter_heat_complete(heat_record)
                has_advance = hitter_advance_complete(row)
            else:
                heat_records = heat.get("pitchers") or {}
                candidates = [player["id"], (row or {}).get("mlb_id"), (row or {}).get("synergy_id")]
                heat_record = next(
                    (heat_records.get(str(candidate)) for candidate in candidates if heat_records.get(str(candidate))),
                    None,
                )
                has_heat = pitcher_heat_complete(heat_record)
                has_advance = pitcher_advance_complete(arsenal_by.get(player_key))
            stat_bar += int(has_line)
            heat_map += int(has_heat)
            all_data += int(has_line and has_heat and has_advance)
        result[role + "s"] = {
            "roster": len(players),
            "stat_bar": stat_bar,
            "heat_map": heat_map,
            "all_data": all_data,
        }

    seasons = {"2026 summer": 0, "2025 summer": 0, "winter": 0, "empty": 0}
    empty_names = []
    for pitcher in pitchers:
        line = pitcher.get("summer_line") or {}
        season = str(line.get("season") or "").lower()
        if "2026" in season and "summer" in season:
            seasons["2026 summer"] += 1
        elif "2025" in season and "summer" in season:
            seasons["2025 summer"] += 1
        elif season:
            seasons["winter"] += 1
        else:
            seasons["empty"] += 1
            empty_names.append(pitcher.get("name"))

    report = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "roster_source": "data/lbprc_2025_rosters.json, opposing teams CAG/CAR/MAY/PON/SJU",
        "definitions": {
            "hitter_heat_map": "All four maps have samples: vs-R SLG/Whiff and vs-L SLG/Whiff",
            "pitcher_heat_map": "Both maps have samples: SLG vs R and SLG vs L",
            "hitter_all_data": "Season line + four heat maps + overall and vs-R/vs-L Synergy tables",
            "pitcher_all_data": "Season line + two heat maps + movement numbers for every arsenal row + miss rate for every arsenal row",
        },
        "coverage": result,
        "pitcher_site_packets": {
            "total": len(pitchers),
            "season_counts": seasons,
            "still_empty": empty_names,
        },
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

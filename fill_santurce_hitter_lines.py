#!/usr/bin/env python3
"""Fill Santurce hitter season bars.

Order is 2026 summer, then 2025 LBPRC winter, then 2025 summer, then older summers.
A later season is used only when the earlier one has no plate appearances.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import build_advance_synergy as bas

ROOT = Path(__file__).resolve().parent
HITTERS = ROOT / "data" / "advance_opposing_hitters.json"
ROSTER = ROOT / "data" / "lbprc_2025_rosters.json"
WINTER = ROOT / "data" / "lbprc_2025_hitting.json"
SUMMER_YEARS = (2026, 2025, 2024, 2023, 2022)


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    text = text.replace("carrero", "carrier")
    return re.sub(r"[^a-z0-9]", "", text)


def winter_line(row: dict) -> dict:
    return {
        "team": row.get("teamAbbrev") or "SAN",
        "team_name": row.get("teamName") or "Cangrejeros de Santurce",
        "season": "2025 LBPRC winter",
        "league": "LBPRC",
        "circuit": "LBPRC",
        "league_name": "Liga de Béisbol Profesional Roberto Clemente",
        "level": None,
        "pa": int(row.get("plateAppearances") or 0),
        "ab": int(row.get("atBats") or 0),
        "r": int(row.get("runs") or 0),
        "h": int(row.get("hits") or 0),
        "doubles": int(row.get("doubles") or 0),
        "triples": int(row.get("triples") or 0),
        "hr": int(row.get("homeRuns") or 0),
        "rbi": int(row.get("rbi") or 0),
        "sb": int(row.get("stolenBases") or 0),
        "cs": int(row.get("caughtStealing") or 0),
        "bb": int(row.get("baseOnBalls") or 0),
        "so": int(row.get("strikeOuts") or 0),
        "ba": bas._fmt_slash_rate(row.get("avg")),
        "obp": bas._fmt_slash_rate(row.get("obp")),
        "slg": bas._fmt_slash_rate(row.get("slg")),
        "ops": bas._fmt_slash_rate(row.get("ops")),
        "games": int(row.get("gamesPlayed") or 0),
        "source": "LBPRC 2025 winter box scores",
    }


def best_winter(rows: list[dict]) -> dict | None:
    usable = [row for row in rows if int(row.get("plateAppearances") or 0) > 0]
    if not usable:
        return None
    usable.sort(key=lambda row: int(row.get("plateAppearances") or 0), reverse=True)
    return winter_line(usable[0])


def main() -> None:
    roster = json.loads(ROSTER.read_text())
    san = next(team for team in roster.values() if team.get("teamAbbrev") == "SAN")
    players = [p for p in san.get("players") or [] if p.get("position") != "P"]
    winter_rows = json.loads(WINTER.read_text())
    by_winter_id: dict[int, list[dict]] = {}
    by_winter_name: dict[str, list[dict]] = {}
    for row in winter_rows:
        pid = int(row.get("playerId") or 0)
        if pid > 0:
            by_winter_id.setdefault(pid, []).append(row)
        by_winter_name.setdefault(norm(row.get("playerFullName") or row.get("playerName") or ""), []).append(row)

    doc = json.loads(HITTERS.read_text())
    hitters = doc.setdefault("hitters", [])
    by_id = {int(h.get("mlb_id") or 0): h for h in hitters if int(h.get("mlb_id") or 0) > 0}
    by_name = {}
    for h in hitters:
        if h.get("team") == "SAN":
            by_name[norm(h.get("name") or "")] = h

    ids = sorted({int(p.get("id") or 0) for p in players if int(p.get("id") or 0) > 0})
    summer: dict[int, dict[int, dict]] = {}
    for year in SUMMER_YEARS:
        print(f"fetch summer {year}", flush=True)
        summer[year] = bas.fetch_2026_summer_lines(ids, season=year)
        print(f"  lines {len(summer[year])}", flush=True)

    filled = 0
    added = 0
    blank = []
    for player in players:
        pid = int(player.get("id") or 0)
        name = player.get("name") or ""
        row = by_name.get(norm(name))
        if row is None and pid > 0:
            found = by_id.get(pid)
            if found and found.get("team") == "SAN":
                row = found
            elif found:
                row = {
                    "name": name,
                    "team": "SAN",
                    "mlb_id": pid,
                    "synergy_id": found.get("synergy_id"),
                    "bats": found.get("bats"),
                    "source": "Santurce roster hitter",
                }
                for key, value in found.items():
                    if key not in row and key != "team":
                        row[key] = value
                row["team"] = "SAN"
                row["name"] = name
                hitters.append(row)
                added += 1
                by_name[norm(name)] = row
        line = None
        if pid > 0 and summer[2026].get(pid):
            line = summer[2026][pid]
        if line is None:
            winter = by_winter_id.get(pid) if pid > 0 else None
            if not winter:
                winter = by_winter_name.get(norm(name))
            if winter:
                line = best_winter(winter)
        if line is None and pid > 0:
            for year in (2025, 2024, 2023, 2022):
                if summer[year].get(pid):
                    line = summer[year][pid]
                    break
        if row is None:
            row = {
                "name": name,
                "team": "SAN",
                "mlb_id": pid if pid > 0 else None,
                "synergy_id": None,
                "bats": None,
                "source": "Santurce roster hitter",
            }
            hitters.append(row)
            added += 1
            if pid > 0:
                by_id[pid] = row
            by_name[norm(name)] = row
        if not line:
            blank.append(name)
            continue
        row["summer_line"] = line
        filled += 1
        print(f"  {name}: {line.get('season')} {line.get('team')} PA {line.get('pa')}", flush=True)

    HITTERS.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"filled {filled} added {added} blank {blank}", flush=True)


if __name__ == "__main__":
    main()

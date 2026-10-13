#!/usr/bin/env python3
"""Fill missing pitcher stat bars from 2025 summer, then LBPRC winter.

Existing 2026 summer lines are never replaced. The summer lookup uses MLB's
Stats API and chooses the real team split with the most innings.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

import build_advance_synergy as bas
import fill_pitcher_summer_supplement as supplement
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PITCHERS_PATH = DATA / "advance_opposing_pitchers.json"
LBPRC_PATH = DATA / "lbprc_2025_pitching.json"
REPORT_PATH = ROOT / "runs" / "pitcher_prior_season_fill_report.json"
FIP_CONSTANT = 3.10


def integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def rate(value: Any, places: int) -> str | None:
    if value in (None, "", ".---", "-.--", "---", "-"):
        return None
    try:
        return f"{float(value):.{places}f}"
    except (TypeError, ValueError):
        return None


def outs_from_ip(value: Any) -> int:
    whole, dot, fraction = str(value or "").partition(".")
    try:
        return int(whole) * 3 + (int(fraction[:1]) if dot else 0)
    except ValueError:
        return 0


def fip(stat: dict, outs: int) -> str | None:
    values = [integer(stat.get(k)) for k in ("homeRuns", "baseOnBalls", "hitByPitch", "strikeOuts")]
    if outs <= 0 or any(v is None for v in values):
        return None
    hr, bb, hbp, strikeouts = (int(v) for v in values)
    value = (13 * hr + 3 * (bb + hbp) - 2 * strikeouts) / (outs / 3) + FIP_CONSTANT
    return f"{value:.2f}"


def get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Santurce2026/prior-season-line"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def summer_2025_lines(ids: list[int]) -> dict[int, dict]:
    ids = sorted({int(player_id) for player_id in ids if int(player_id) > 0})
    team_abbrev: dict[int, str] = {}
    rows_by_player: dict[int, list[dict]] = defaultdict(list)

    for sport_id, family in bas.SUMMER_LINE_SPORTS:
        teams = get_json(f"https://statsapi.mlb.com/api/v1/teams?sportId={sport_id}&season=2025")
        for team in teams.get("teams") or []:
            if team.get("id"):
                team_abbrev[int(team["id"])] = (
                    team.get("abbreviation") or team.get("teamCode") or team.get("name") or ""
                )
        for start in range(0, len(ids), 40):
            query = urllib.parse.urlencode({
                "personIds": ",".join(str(value) for value in ids[start:start + 40]),
                "hydrate": f"stats(group=[pitching],type=[season],season=2025,sportId={sport_id})",
            })
            payload = get_json(f"https://statsapi.mlb.com/api/v1/people?{query}")
            for person in payload.get("people") or []:
                for group in person.get("stats") or []:
                    for split in group.get("splits") or []:
                        if str(split.get("season") or "") != "2025":
                            continue
                        if split.get("gameType") not in (None, "", "R"):
                            continue
                        stat = split.get("stat") or {}
                        outs = integer(stat.get("outs")) or outs_from_ip(stat.get("inningsPitched"))
                        team = split.get("team") or {}
                        if outs <= 0 or not team.get("id"):
                            continue
                        league = split.get("league") or {}
                        sport = split.get("sport") or {}
                        rows_by_player[int(person["id"])].append({
                            "family": family,
                            "sport_id": sport_id,
                            "level": sport.get("abbreviation") or "",
                            "outs": outs,
                            "team_id": int(team["id"]),
                            "team_name": team.get("name") or "",
                            "league_name": league.get("name") or "",
                            "stat": stat,
                        })
            time.sleep(0.05)

    result = {}
    for player_id, rows in rows_by_player.items():
        rows.sort(
            key=lambda row: (
                row["outs"],
                integer(row["stat"].get("gamesPlayed")) or 0,
                integer(row["stat"].get("strikeOuts")) or 0,
            ),
            reverse=True,
        )
        row = rows[0]
        stat = row["stat"]
        team = team_abbrev.get(row["team_id"]) or row["team_name"]
        result[player_id] = {
            "team": team,
            "team_name": row["team_name"] or team,
            "season": "2025 summer",
            "league": bas._league_code(row["league_name"], row["family"]),
            "circuit": row["family"],
            "league_name": row["league_name"] or None,
            "level": row["level"] or None,
            "ip": str(stat.get("inningsPitched") or "") or None,
            "outs": row["outs"],
            "g": integer(stat.get("gamesPlayed") or stat.get("gamesPitched")),
            "gs": integer(stat.get("gamesStarted")),
            "k": integer(stat.get("strikeOuts")),
            "bb": integer(stat.get("baseOnBalls")),
            "k9": rate(stat.get("strikeoutsPer9Inn"), 1),
            "bb9": rate(stat.get("walksPer9Inn"), 1),
            "era": rate(stat.get("era"), 2),
            "fip": fip(stat, row["outs"]),
            "whip": rate(stat.get("whip"), 2),
            "h": integer(stat.get("hits")),
            "hr": integer(stat.get("homeRuns")),
            "sb_allowed": integer(stat.get("stolenBases")),
            "cs_allowed": integer(stat.get("caughtStealing")),
            "sport_id": row["sport_id"],
            "assignments": len(rows),
            "source": "MLB Stats API 2025 regular-season pitching team split",
        }
    return result


def bref_summer_2025_line(name: str) -> dict | None:
    """Return the most-IP non-winter 2025 Baseball Reference register row."""
    search_url = (
        "https://www.baseball-reference.com/search/search.fcgi?search="
        + urllib.parse.quote_plus(name)
    )
    page, final_url = supplement._fetch(search_url)
    candidates = supplement._register_candidates(page, name)
    parsed = urllib.parse.urlparse(final_url)
    direct_id = urllib.parse.parse_qs(parsed.query).get("id", [])
    if "register/player.fcgi" in parsed.path and direct_id:
        candidates.insert(0, direct_id[0])
    if not candidates:
        return None

    source_url = f"https://www.baseball-reference.com/register/player.fcgi?id={candidates[0]}"
    if source_url != final_url:
        page, final_url = supplement._fetch(source_url)
    table = BeautifulSoup(page, "html.parser").find("table", id="standard_pitching")
    if table is None:
        return None

    rows = []
    for tr in table.select("tbody tr"):
        cells = {
            cell.get("data-stat"): cell
            for cell in tr.find_all(["th", "td"])
            if cell.get("data-stat")
        }
        if cells.get("year_ID") is None or cells["year_ID"].get_text(strip=True) != "2025":
            continue
        team_cell, league_cell = cells.get("team_ID"), cells.get("lg_ID")
        team = team_cell.get_text(" ", strip=True) if team_cell else ""
        league = league_cell.get_text(" ", strip=True) if league_cell else ""
        team_link = team_cell.find("a") if team_cell else None
        league_link = league_cell.find("a") if league_cell else None
        team_name = (team_link.get("title") if team_link else "") or team
        league_name = (league_link.get("title") if league_link else "") or league
        context = f"{team} {team_name} {league} {league_name}".lower()
        if not team or any(marker in context for marker in supplement.WINTER_MARKERS):
            continue
        if supplement.re.search(r"\b\d+\s+teams?\b", team.lower()):
            continue
        values = {
            key: (cell.get_text(" ", strip=True) or None)
            for key, cell in cells.items()
        }
        outs = outs_from_ip(values.get("IP"))
        if outs > 0:
            rows.append({
                "values": values,
                "outs": outs,
                "team": team,
                "team_name": team_name,
                "league": league,
                "league_name": league_name,
                "level": values.get("level"),
            })
    if not rows:
        return None
    rows.sort(key=lambda row: row["outs"], reverse=True)
    line = supplement._bref_line(rows[0], final_url, len(rows))
    line["season"] = "2025 summer"
    line["source"] = "Baseball Reference Register 2025 regular-season pitching"
    return line


def winter_line(row: dict) -> dict:
    outs = integer(row.get("outs")) or outs_from_ip(row.get("inningsPitched"))
    return {
        "team": row.get("teamAbbrev"),
        "team_name": row.get("teamName"),
        "season": "2025 LBPRC winter",
        "league": "LBPRC",
        "circuit": "LBPRC",
        "league_name": "Liga de Béisbol Profesional Roberto Clemente",
        "level": "Winter",
        "ip": row.get("inningsPitched"),
        "outs": outs,
        "g": integer(row.get("gamesPlayed") or row.get("gamesPitched")),
        "gs": integer(row.get("gamesStarted")),
        "k": integer(row.get("strikeOuts")),
        "bb": integer(row.get("baseOnBalls")),
        "k9": rate(row.get("strikeoutsPer9Inn"), 1),
        "bb9": rate(row.get("walksPer9Inn"), 1),
        "era": rate(row.get("era"), 2),
        "fip": fip(row, outs),
        "whip": rate(row.get("whip"), 2),
        "h": integer(row.get("hits")),
        "hr": integer(row.get("homeRuns")),
        "sb_allowed": integer(row.get("stolenBases")),
        "cs_allowed": integer(row.get("caughtStealing")),
        "assignments": 1,
        "source": "Local LBPRC 2025 season pitching file",
    }


def main() -> None:
    document = json.loads(PITCHERS_PATH.read_text())
    pitchers = document.get("pitchers") or []
    missing = [row for row in pitchers if not row.get("summer_line")]
    summer = summer_2025_lines([row.get("mlb_id") or 0 for row in missing])

    filled_summer = []
    for pitcher in missing:
        line = summer.get(int(pitcher.get("mlb_id") or 0))
        if line:
            pitcher["summer_line"] = line
            filled_summer.append(pitcher["name"])

    filled_bref = []
    bref_errors = {}
    for pitcher in pitchers:
        season = str((pitcher.get("summer_line") or {}).get("season") or "").lower()
        if "2026" in season or season == "2025 summer":
            continue
        try:
            line = bref_summer_2025_line(pitcher["name"])
        except Exception as exc:  # Preserve verified local lines if the register rate-limits.
            bref_errors[pitcher["name"]] = f"{type(exc).__name__}: {exc}"
            continue
        if line:
            pitcher["summer_line"] = line
            filled_bref.append(pitcher["name"])

    winter_rows = json.loads(LBPRC_PATH.read_text())
    winter_by_id = {
        int(row["playerId"]): row
        for row in winter_rows
        if row.get("playerId") and (integer(row.get("outs")) or outs_from_ip(row.get("inningsPitched"))) > 0
    }
    filled_winter = []
    for pitcher in pitchers:
        if pitcher.get("summer_line"):
            continue
        row = winter_by_id.get(int(pitcher.get("mlb_id") or 0))
        if row:
            pitcher["summer_line"] = winter_line(row)
            filled_winter.append(pitcher["name"])

    document["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    document["prior_season_line_source"] = {
        "priority": ["2026 summer", "2025 summer", "2025 LBPRC winter"],
        "selection": "Most innings among real 2025 MLB/MiLB/LMB team splits; LBPRC fallback",
    }
    PITCHERS_PATH.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")

    still_empty = [row["name"] for row in pitchers if not row.get("summer_line")]
    season_counts = {"2026 summer": 0, "2025 summer": 0, "winter": 0, "empty": len(still_empty)}
    for pitcher in pitchers:
        season = str((pitcher.get("summer_line") or {}).get("season") or "").lower()
        if "2026" in season and "summer" in season:
            season_counts["2026 summer"] += 1
        elif "2025" in season and "summer" in season:
            season_counts["2025 summer"] += 1
        elif season:
            season_counts["winter"] += 1
    report = {
        "updated": document["updated"],
        "pitchers": len(pitchers),
        "season_counts": season_counts,
        "filled_2025_summer": filled_summer,
        "filled_2025_summer_bref": filled_bref,
        "baseball_reference_errors": bref_errors,
        "filled_winter": filled_winter,
        "still_empty": still_empty,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

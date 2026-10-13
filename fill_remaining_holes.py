#!/usr/bin/env python3
"""Fill remaining advance holes without a full roster rebuild.

Hitter and pitcher season bars: 2026 summer, else 2025 summer, else 2025-26
or 2025 LBPRC winter. Existing lines stay. Movement plots are added only for
opposing pitchers who pitched MLB, AAA (sport 11), or MLB spring training in
2024–2026 and do not already have an XY arsenal. Horizontal break is stored
the way the page already reads it: arm-side-positive hb_pitcher, so hitter
view mirrors RHP once and leaves LHP alone.
"""

from __future__ import annotations

import json
import math
import re
import signal
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

import build_advance_synergy as bas
import build_pregame_arsenal as bpa
import fill_pitcher_summer_supplement as supplement
import fill_synergy_blanks as blanks

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
CHECKPOINT = RUNS / "remaining_holes_checkpoint.json"
REPORT = RUNS / "remaining_holes_report.json"
UA = {"User-Agent": "Santurce2026/remaining-holes"}
HIDDEN = {
    "KN", "EP", "UN", "UNKNOWN", "UNK", "EEPHUS", "KNUCKLEBALL", "KNUCKLE",
    "KC", "KNUCKLECURVE", "KNUCKLECURVEBALL",
}
CODE_ALIAS = {"FA": "FF", "4S": "FF", "FT": "SI", "2S": "SI"}
PR_MARKERS = ("puerto rican", "roberto clemente", "lbprc", "prwl")
REGULAR_RANGES = (
    ("2026", "2026-03-28", "2026-10-01"),
    ("2025", "2025-03-27", "2025-10-05"),
    ("2024", "2024-03-28", "2024-10-05"),
)
SPRING_RANGES = (
    ("2026", "2026-02-15", "2026-03-27"),
    ("2025", "2025-02-15", "2025-03-26"),
    ("2024", "2024-02-15", "2024-03-27"),
)


class RequestTimeout(Exception):
    pass


def _alarm(_signum, _frame):
    raise RequestTimeout("timed out")


signal.signal(signal.SIGALRM, _alarm)


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def last_name(value: str) -> str:
    parts = [part for part in re.split(r"\s+", (value or "").strip()) if part]
    while parts and re.fullmatch(r"(?i)(jr\.?|sr\.?|ii|iii|iv|v)", parts[-1] or ""):
        parts.pop()
    return norm(parts[-1]) if parts else ""


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


def dump(path: Path, doc: Any) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5 + attempt)
    raise RuntimeError(f"{type(last).__name__}: {last}")


def roster_players() -> tuple[list[dict], list[dict]]:
    pitchers, hitters = [], []
    for team in load("lbprc_2025_rosters.json").values():
        if team.get("teamAbbrev") == "SAN":
            continue
        for player in team.get("players") or []:
            row = {
                "team": team.get("teamAbbrev"),
                "name": player.get("name"),
                "id": int(player.get("id") or 0),
            }
            (pitchers if player.get("position") == "P" else hitters).append(row)
    return pitchers, hitters


def player_key(row: dict) -> str:
    return f"{row.get('team')}|{norm(str(row.get('name') or ''))}"


def chart_code(pitch: dict) -> str:
    code = str(pitch.get("type") or pitch.get("code") or "").upper()
    code = CODE_ALIAS.get(code, code)
    return "" if code in HIDDEN else code


def finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def has_movement(row: dict | None) -> bool:
    for pitch in (row or {}).get("arsenal") or []:
        if not chart_code(pitch):
            continue
        if finite(pitch.get("ivb")) and (
            finite(pitch.get("hb_pitcher")) or finite(pitch.get("hb_hitter")) or finite(pitch.get("hb"))
        ):
            return True
    return False


def side_sum(side: dict | None, field: str) -> int:
    if not side:
        return 0
    return sum(int(value or 0) for value in (side.get(field) or []))


def index_rows(rows: list[dict]) -> dict[str, dict]:
    return {player_key(row): row for row in rows}


def save_checkpoint(checkpoint: dict) -> None:
    checkpoint["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dump(CHECKPOINT, checkpoint)


def people_by_ids(ids: list[int]) -> dict[int, dict]:
    found: dict[int, dict] = {}
    clean = sorted({int(value) for value in ids if int(value) > 0})
    for start in range(0, len(clean), 40):
        chunk = clean[start:start + 40]
        url = "https://statsapi.mlb.com/api/v1/people?personIds=" + ",".join(str(value) for value in chunk)
        try:
            payload = get_json(url)
        except Exception as exc:  # noqa: BLE001
            print(f"people fail: {exc}", flush=True)
            continue
        for person in payload.get("people") or []:
            if person.get("id"):
                found[int(person["id"])] = person
        time.sleep(0.05)
    return found


def search_person(name: str, *, pitcher: bool) -> dict | None:
    url = "https://statsapi.mlb.com/api/v1/people/search?names=" + urllib.parse.quote(name)
    try:
        payload = get_json(url)
    except Exception as exc:  # noqa: BLE001
        print(f"search fail {name}: {exc}", flush=True)
        return None
    people = payload.get("people") or []
    exact = [person for person in people if norm(person.get("fullName") or "") == norm(name)]
    if len(exact) == 1:
        return exact[0]
    if pitcher and len(exact) > 1:
        arms = [
            person for person in exact
            if str((person.get("primaryPosition") or {}).get("abbreviation") or "").upper() == "P"
            or str((person.get("primaryPosition") or {}).get("code") or "") == "1"
        ]
        if len(arms) == 1:
            return arms[0]
    return None


def resolve_ids(players: list[dict], checkpoint: dict, *, pitcher: bool) -> None:
    resolved = checkpoint.setdefault("resolved_ids", {})
    people_cache = checkpoint.setdefault("people", {})
    known = [player["id"] for player in players if player["id"] > 0 and player_key(player) not in resolved]
    if known:
        for person_id, person in people_by_ids(known).items():
            people_cache[str(person_id)] = {
                "fullName": person.get("fullName"),
                "throws": ((person.get("pitchHand") or {}).get("code") or ""),
                "bats": ((person.get("batSide") or {}).get("code") or ""),
            }
    for player in players:
        key = player_key(player)
        if key in resolved:
            continue
        if player["id"] > 0:
            person = people_cache.get(str(player["id"])) or {}
            api_last = last_name(str(person.get("fullName") or ""))
            if person and api_last and api_last != last_name(player["name"]):
                resolved[key] = {
                    "mlb_id": None,
                    "reason": f"roster id {player['id']} is Stats API {person.get('fullName')}",
                }
            else:
                resolved[key] = {
                    "mlb_id": player["id"] if person else player["id"],
                    "throws": person.get("throws") or "",
                    "source": "roster id",
                }
            continue
        person = search_person(player["name"], pitcher=pitcher)
        time.sleep(0.05)
        if not person or not person.get("id"):
            resolved[key] = {"mlb_id": None, "reason": "no unique Stats API name match"}
            continue
        people_cache[str(person["id"])] = {
            "fullName": person.get("fullName"),
            "throws": ((person.get("pitchHand") or {}).get("code") or ""),
            "bats": ((person.get("batSide") or {}).get("code") or ""),
        }
        resolved[key] = {
            "mlb_id": int(person["id"]),
            "throws": ((person.get("pitchHand") or {}).get("code") or ""),
            "source": "Stats API name search",
            "fullName": person.get("fullName"),
        }
        print(f"resolved {player['team']} {player['name']} -> {person.get('id')} {person.get('fullName')}", flush=True)
    save_checkpoint(checkpoint)


def mlb_id_for(player: dict, checkpoint: dict) -> int | None:
    row = (checkpoint.get("resolved_ids") or {}).get(player_key(player)) or {}
    try:
        value = int(row.get("mlb_id") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def throws_for(player: dict, checkpoint: dict) -> str:
    row = (checkpoint.get("resolved_ids") or {}).get(player_key(player)) or {}
    if row.get("throws"):
        return str(row["throws"])
    person = (checkpoint.get("people") or {}).get(str(row.get("mlb_id") or "")) or {}
    return str(person.get("throws") or "")


def appearance_bucket(ids: list[int], season: str, sport_id: int, game_type: str | None) -> tuple[dict[int, dict], set[int]]:
    found: dict[int, dict] = {}
    failed: set[int] = set()
    hydrate = f"stats(group=[pitching],type=[season],season={season},sportId={sport_id}"
    if game_type:
        hydrate += f",gameType={game_type}"
    hydrate += ")"
    for start in range(0, len(ids), 30):
        chunk = ids[start:start + 30]
        url = (
            "https://statsapi.mlb.com/api/v1/people?personIds="
            + ",".join(str(value) for value in chunk)
            + "&hydrate=" + hydrate
        )
        try:
            payload = get_json(url)
        except Exception as exc:  # noqa: BLE001
            print(f"appearance fail {season} sport {sport_id} {game_type}: {exc}", flush=True)
            failed.update(chunk)
            continue
        for person in payload.get("people") or []:
            person_id = int(person.get("id") or 0)
            for group in person.get("stats") or []:
                for split in group.get("splits") or []:
                    if str(split.get("season") or "") != str(season):
                        continue
                    actual = split.get("gameType") or game_type or "R"
                    if game_type and actual != game_type:
                        continue
                    if not game_type and actual not in (None, "", "R"):
                        continue
                    stat = split.get("stat") or {}
                    games = int(stat.get("gamesPlayed") or stat.get("gamesPitched") or 0)
                    outs = int(stat.get("outs") or 0)
                    if games <= 0 and outs <= 0:
                        continue
                    found[person_id] = {
                        "games": games,
                        "ip": stat.get("inningsPitched"),
                        "team": (split.get("team") or {}).get("name"),
                    }
        time.sleep(0.05)
    return found, failed


def classify_appearances(pitchers: list[dict], checkpoint: dict) -> None:
    appearances = checkpoint.setdefault("appearances", {})
    ids = sorted({mlb_id_for(player, checkpoint) or 0 for player in pitchers} - {0})
    pending = [value for value in ids if str(value) not in appearances]
    if not pending:
        return
    print(f"classifying appearances for {len(pending)} pitchers", flush=True)
    levels = {value: {"mlb": [], "aaa": [], "spring": [], "detail": {}} for value in pending}
    failed_ids: set[int] = set()
    for season in ("2026", "2025", "2024"):
        queries = (
            ("mlb", 1, None),
            ("aaa", 11, None),
            ("spring", 1, "S"),
        )
        for label, sport_id, game_type in queries:
            found, failed = appearance_bucket(pending, season, sport_id, game_type)
            failed_ids.update(failed)
            for person_id, detail in found.items():
                levels[person_id][label].append(season)
                levels[person_id]["detail"][f"{label}:{season}"] = detail
            print(f"  {season} {label}: {len(found)}", flush=True)
    for person_id, row in levels.items():
        if person_id in failed_ids:
            continue
        appearances[str(person_id)] = row
    save_checkpoint(checkpoint)


def appearance_for(player: dict, checkpoint: dict) -> dict:
    person_id = mlb_id_for(player, checkpoint)
    if not person_id:
        return {"mlb": [], "aaa": [], "spring": [], "detail": {}}
    return (checkpoint.get("appearances") or {}).get(str(person_id)) or {
        "mlb": [], "aaa": [], "spring": [], "detail": {},
    }


def qualified(levels: dict) -> bool:
    return bool(levels.get("mlb") or levels.get("aaa") or levels.get("spring"))


def team_abbrevs(season: str) -> dict[int, str]:
    found: dict[int, str] = {}
    for sport_id, _family in bas.SUMMER_LINE_SPORTS:
        try:
            payload = get_json(f"https://statsapi.mlb.com/api/v1/teams?sportId={sport_id}&season={season}")
        except Exception as exc:  # noqa: BLE001
            print(f"teams fail {season} {sport_id}: {exc}", flush=True)
            continue
        for team in payload.get("teams") or []:
            if team.get("id"):
                found[int(team["id"])] = team.get("abbreviation") or team.get("teamCode") or team.get("name") or ""
        time.sleep(0.05)
    return found


def hitting_lines(ids: list[int], season: str) -> dict[int, dict]:
    ids = sorted({int(value) for value in ids if int(value) > 0})
    if not ids:
        return {}
    abbrev = team_abbrevs(season)
    by_player: dict[int, list[dict]] = defaultdict(list)
    for sport_id, family in bas.SUMMER_LINE_SPORTS:
        for start in range(0, len(ids), 30):
            chunk = ids[start:start + 30]
            hydrate = (
                f"stats(group=[hitting],type=[season],season={season},sportId={sport_id})"
            )
            url = (
                "https://statsapi.mlb.com/api/v1/people?personIds="
                + ",".join(str(value) for value in chunk)
                + "&hydrate=" + hydrate
            )
            try:
                payload = get_json(url)
            except Exception as exc:  # noqa: BLE001
                print(f"hitting fail {season} sport {sport_id}: {exc}", flush=True)
                continue
            for person in payload.get("people") or []:
                person_id = int(person.get("id") or 0)
                for group in person.get("stats") or []:
                    for split in group.get("splits") or []:
                        if str(split.get("season") or "") != season:
                            continue
                        if split.get("gameType") not in (None, "", "R"):
                            continue
                        stat = split.get("stat") or {}
                        plate_appearances = bas._int_stat(stat, "plateAppearances") or 0
                        at_bats = bas._int_stat(stat, "atBats") or 0
                        team = split.get("team") or {}
                        if (plate_appearances <= 0 and at_bats <= 0) or not team.get("id"):
                            continue
                        league = split.get("league") or {}
                        sport = split.get("sport") or {}
                        by_player[person_id].append({
                            "family": family,
                            "sport_id": sport_id,
                            "level": sport.get("abbreviation") or "",
                            "pa": plate_appearances,
                            "ab": at_bats,
                            "g": bas._int_stat(stat, "gamesPlayed") or 0,
                            "team_id": int(team["id"]),
                            "team_name": team.get("name") or "",
                            "league_name": league.get("name") or "",
                            "stat": stat,
                        })
            time.sleep(0.05)
    label = "Summer 2026" if season == "2026" else "Summer 2025"
    result = {}
    for person_id, rows in by_player.items():
        rows.sort(key=lambda row: (row["pa"], row["ab"], row["g"]), reverse=True)
        best = rows[0]
        stat = best["stat"]
        team = abbrev.get(best["team_id"]) or best["team_name"]
        result[person_id] = {
            "team": team,
            "team_name": best["team_name"] or team,
            "season": label,
            "league": bas._league_code(best["league_name"], best["family"]),
            "circuit": best["family"],
            "league_name": best["league_name"] or None,
            "level": best["level"] or None,
            "pa": best["pa"],
            "ab": best["ab"],
            "r": bas._int_stat(stat, "runs"),
            "h": bas._int_stat(stat, "hits"),
            "doubles": bas._int_stat(stat, "doubles"),
            "triples": bas._int_stat(stat, "triples"),
            "hr": bas._int_stat(stat, "homeRuns"),
            "rbi": bas._int_stat(stat, "rbi"),
            "sb": bas._int_stat(stat, "stolenBases"),
            "cs": bas._int_stat(stat, "caughtStealing"),
            "bb": bas._int_stat(stat, "baseOnBalls"),
            "so": bas._int_stat(stat, "strikeOuts"),
            "ba": bas._fmt_slash_rate(stat.get("avg")),
            "obp": bas._fmt_slash_rate(stat.get("obp")),
            "slg": bas._fmt_slash_rate(stat.get("slg")),
            "ops": bas._fmt_slash_rate(stat.get("ops")),
            "games": best["g"],
            "sport_id": best["sport_id"],
            "assignments": len(rows),
            "source": f"MLB Stats API {season} regular-season hitting team split",
        }
    return result


def local_hitting_index() -> tuple[dict[int, dict], dict[str, list[dict]]]:
    by_id: dict[int, dict] = {}
    by_name: dict[str, list[dict]] = defaultdict(list)
    rows = json.loads((DATA / "lbprc_2025_hitting.json").read_text())
    for row in rows:
        plate_appearances = int(row.get("plateAppearances") or 0)
        at_bats = int(row.get("atBats") or 0)
        if plate_appearances <= 0 and at_bats <= 0:
            continue
        person_id = int(row.get("playerId") or 0)
        if person_id > 0:
            current = by_id.get(person_id)
            if not current or plate_appearances > int(current.get("plateAppearances") or 0):
                by_id[person_id] = row
        name = norm(row.get("playerFullName") or row.get("playerName") or "")
        if name:
            by_name[name].append(row)
    return by_id, by_name


def local_hitting_line(row: dict) -> dict:
    return {
        "team": row.get("teamAbbrev"),
        "team_name": row.get("teamName"),
        "season": "2025 LBPRC winter",
        "league": "LBPRC",
        "circuit": "LBPRC",
        "league_name": "Liga de Béisbol Profesional Roberto Clemente",
        "level": "Winter",
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
        "assignments": 1,
        "source": "Local LBPRC 2025 hitting file",
    }


def local_hitting_for(player: dict, checkpoint: dict, by_id: dict, by_name: dict) -> dict | None:
    person_id = mlb_id_for(player, checkpoint) or (player["id"] if player["id"] > 0 else 0)
    row = by_id.get(person_id or 0)
    if row:
        return local_hitting_line(row)
    matches = by_name.get(norm(player["name"])) or []
    identities = {int(item.get("playerId") or 0) for item in matches}
    if len(matches) == 1 or (matches and len(identities) == 1):
        best = max(matches, key=lambda item: int(item.get("plateAppearances") or 0))
        return local_hitting_line(best)
    return None


def register_page(name: str) -> tuple[str, str | None]:
    search_url = (
        "https://www.baseball-reference.com/search/search.fcgi?search="
        + urllib.parse.quote_plus(name)
    )
    page, final_url = supplement._fetch(search_url)
    candidates = supplement._register_candidates(page, name)
    parsed = urllib.parse.urlparse(final_url)
    direct = urllib.parse.parse_qs(parsed.query).get("id", [])
    if "register/player.fcgi" in parsed.path and direct:
        candidates.insert(0, direct[0])
    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        return page, None
    source_url = f"https://www.baseball-reference.com/register/player.fcgi?id={unique[0]}"
    if source_url != final_url:
        page, final_url = supplement._fetch(source_url)
    return page, final_url


def table_rows(page: str, table_id: str) -> list[dict]:
    from bs4 import BeautifulSoup

    table = BeautifulSoup(page, "html.parser").find("table", id=table_id)
    if table is None:
        return []
    rows = []
    for tr in table.select("tbody tr"):
        cells = {
            cell.get("data-stat"): cell
            for cell in tr.find_all(["th", "td"])
            if cell.get("data-stat")
        }
        year = cells.get("year_ID")
        if year is None:
            continue
        team_cell, league_cell = cells.get("team_ID"), cells.get("lg_ID")
        team = team_cell.get_text(" ", strip=True) if team_cell else ""
        league = league_cell.get_text(" ", strip=True) if league_cell else ""
        if not team or re.search(r"\b\d+\s+teams?\b", team.lower()):
            continue
        team_link = team_cell.find("a") if team_cell else None
        league_link = league_cell.find("a") if league_cell else None
        values = {key: (cell.get_text(" ", strip=True) or None) for key, cell in cells.items()}
        context = " ".join((
            team,
            (team_link.get("title") if team_link else "") or "",
            league,
            (league_link.get("title") if league_link else "") or "",
        )).lower()
        rows.append({
            "year": year.get_text(strip=True),
            "team": team,
            "team_name": (team_link.get("title") if team_link else "") or team,
            "league": league,
            "league_name": (league_link.get("title") if league_link else "") or league,
            "context": context,
            "values": values,
            "winter": any(marker in context for marker in supplement.WINTER_MARKERS),
            "puerto_rico": any(marker in context for marker in PR_MARKERS),
        })
    return rows


def cell_value(row: dict, *keys: str) -> str | None:
    values = row["values"]
    for key in keys:
        if values.get(key) not in (None, ""):
            return values.get(key)
    return None


def bref_hitting_choice(page: str, source_url: str) -> dict | None:
    parsed = []
    for row in table_rows(page, "standard_batting"):
        plate_appearances = supplement._int(cell_value(row, "PA", "b_pa"))
        if not plate_appearances:
            continue
        parsed.append((row, plate_appearances))
    if not parsed:
        return None

    def choose(predicate):
        rows = [(row, plate_appearances) for row, plate_appearances in parsed if predicate(row)]
        if not rows:
            return None
        rows.sort(key=lambda item: item[1], reverse=True)
        return rows[0]

    chosen = choose(lambda row: row["year"] == "2026" and not row["winter"])
    season = "Summer 2026"
    source = "Baseball Reference Register 2026 standard batting"
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2025" and not row["winter"])
        season = "Summer 2025"
        source = "Baseball Reference Register 2025 standard batting"
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2026" and row["puerto_rico"])
        season = "2025-26 LBPRC winter"
        source = "Baseball Reference Register 2026 Puerto Rican winter batting"
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2025" and row["puerto_rico"])
        season = "2025 LBPRC winter"
        source = "Baseball Reference Register 2025 Puerto Rican winter batting"
    if not chosen:
        return None
    row, plate_appearances = chosen
    team = row["team"] if len(row["team"]) <= 5 else supplement._team_abbrev(row["team"])
    return {
        "team": team,
        "team_name": row["team_name"],
        "season": season,
        "league": row["league"] or "LBPRC",
        "circuit": "LBPRC" if row["puerto_rico"] else "Baseball Reference",
        "league_name": row["league_name"] or row["league"],
        "level": cell_value(row, "level", "level_ID"),
        "pa": plate_appearances,
        "ab": supplement._int(cell_value(row, "AB", "b_ab")),
        "r": supplement._int(cell_value(row, "R", "b_r")),
        "h": supplement._int(cell_value(row, "H", "b_h")),
        "doubles": supplement._int(cell_value(row, "2B", "b_2b")),
        "triples": supplement._int(cell_value(row, "3B", "b_3b")),
        "hr": supplement._int(cell_value(row, "HR", "b_hr")),
        "rbi": supplement._int(cell_value(row, "RBI", "b_rbi")),
        "sb": supplement._int(cell_value(row, "SB", "b_sb")),
        "cs": supplement._int(cell_value(row, "CS", "b_cs")),
        "bb": supplement._int(cell_value(row, "BB", "b_bb")),
        "so": supplement._int(cell_value(row, "SO", "b_so")),
        "ba": cell_value(row, "batting_avg", "BA", "b_batting_avg"),
        "obp": cell_value(row, "onbase_perc", "OBP", "b_onbase_perc"),
        "slg": cell_value(row, "slugging_perc", "SLG", "b_slugging_perc"),
        "ops": cell_value(row, "onbase_plus_slugging", "OPS", "b_onbase_plus_slugging"),
        "assignments": 1,
        "source": source,
        "source_url": source_url,
    }


def bref_pitching_choice(page: str, source_url: str) -> dict | None:
    import backfill_pitcher_prior_seasons as prior

    parsed = []
    for row in table_rows(page, "standard_pitching"):
        outs = prior.outs_from_ip(cell_value(row, "IP"))
        if outs <= 0:
            continue
        parsed.append((row, outs))
    if not parsed:
        return None

    def choose(predicate):
        rows = [(row, outs) for row, outs in parsed if predicate(row)]
        if not rows:
            return None
        rows.sort(key=lambda item: item[1], reverse=True)
        return rows[0]

    chosen = choose(lambda row: row["year"] == "2026" and not row["winter"])
    season = "Summer 2026"
    source = "Baseball Reference Register 2026 standard pitching"
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2025" and not row["winter"])
        season = "2025 summer"
        source = "Baseball Reference Register 2025 standard pitching"
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2026" and row["puerto_rico"])
        season = "2025-26 LBPRC winter"
        source = "Baseball Reference Register 2026 Puerto Rican winter pitching"
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2025" and row["puerto_rico"])
        season = "2025 LBPRC winter"
        source = "Baseball Reference Register 2025 Puerto Rican winter pitching"
    if not chosen:
        return None
    row, outs = chosen
    values = row["values"]
    innings = outs / 3
    strikeouts = supplement._int(cell_value(row, "SO"))
    walks = supplement._int(cell_value(row, "BB"))
    team = row["team"] if len(row["team"]) <= 5 else supplement._team_abbrev(row["team"])
    return {
        "team": team,
        "team_name": row["team_name"],
        "season": season,
        "league": row["league"] or "LBPRC",
        "circuit": "LBPRC" if row["puerto_rico"] else "Baseball Reference",
        "league_name": row["league_name"] or row["league"],
        "level": cell_value(row, "level", "level_ID"),
        "ip": cell_value(row, "IP"),
        "outs": outs,
        "g": supplement._int(cell_value(row, "G")),
        "gs": supplement._int(cell_value(row, "GS")),
        "k": strikeouts,
        "bb": walks,
        "k9": f"{(strikeouts or 0) * 9 / innings:.1f}",
        "bb9": f"{(walks or 0) * 9 / innings:.1f}",
        "era": supplement._rate(cell_value(row, "earned_run_avg"), 2),
        "fip": prior.fip(
            {
                "homeRuns": supplement._int(cell_value(row, "HR")),
                "baseOnBalls": walks,
                "hitByPitch": supplement._int(cell_value(row, "HBP")),
                "strikeOuts": strikeouts,
            },
            outs,
        ),
        "whip": supplement._rate(cell_value(row, "whip"), 2),
        "h": supplement._int(cell_value(row, "H")),
        "hr": supplement._int(cell_value(row, "HR")),
        "sb_allowed": None,
        "cs_allowed": None,
        "assignments": 1,
        "source": source,
        "source_url": source_url,
        "field_sources": {"team_through_hr": source},
    }


def local_pitching_index() -> tuple[dict[int, dict], dict[str, list[dict]]]:
    import backfill_pitcher_prior_seasons as prior

    by_id: dict[int, dict] = {}
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in json.loads((DATA / "lbprc_2025_pitching.json").read_text()):
        outs = prior.integer(row.get("outs")) or prior.outs_from_ip(row.get("inningsPitched"))
        if outs <= 0:
            continue
        person_id = int(row.get("playerId") or 0)
        if person_id > 0:
            current = by_id.get(person_id)
            current_outs = 0 if not current else (
                prior.integer(current.get("outs")) or prior.outs_from_ip(current.get("inningsPitched"))
            )
            if not current or outs > current_outs:
                by_id[person_id] = row
        name = norm(row.get("playerFullName") or row.get("playerName") or "")
        if name:
            by_name[name].append(row)
    return by_id, by_name


def pack_ps(pid: int) -> tuple[list[dict], int | None]:
    for season in (2026, 2025, 2024):
        try:
            rows = bpa.http_json(f"{bpa.PS_BASE}/stuff/{pid}/{season}", timeout=20)
        except Exception as exc:  # noqa: BLE001
            print(f"  prospect savant fail {pid} {season}: {exc}", flush=True)
            time.sleep(0.2)
            continue
        pitches, year, _source = _pack_ps_rows(rows, season) if isinstance(rows, list) else ([], season, "")
        ready = [
            pitch for pitch in pitches
            if chart_code(pitch) and finite(pitch.get("ivb")) and (
                finite(pitch.get("hb_pitcher")) or finite(pitch.get("hb_hitter"))
            )
        ]
        if ready:
            return ready, year
        time.sleep(0.08)
    return [], None


def _pack_ps_rows(rows: list, season: int) -> tuple[list[dict], int, str]:
    pitches = []
    for row in rows:
        ptype = str(row.get("pitch_type") or "").strip().upper()
        if not ptype:
            continue
        pfx_x = bpa.to_float(row.get("pfx_x"))
        pfx_z = bpa.to_float(row.get("pfx_z"))
        pitches.append({
            "type": ptype,
            "name": row.get("pitch_name") or ptype,
            "velo": round(bpa.to_float(row.get("release_speed")) or 0, 1),
            "spin": int(round(bpa.to_float(row.get("release_spin_rate")) or 0)),
            "ivb": bpa.ivb_in(pfx_z),
            "hb_pitcher": bpa.pitcher_view_hb_in(pfx_x),
            "hb_hitter": bpa.hitter_view_hb_in(pfx_x),
            "usage": round(bpa.to_float(row.get("usage")) or 0, 1),
            "pitches": int(row.get("pitches") or 0),
            "ps_stuff": round(bpa.to_float(row.get("psStuff")) or 0, 1),
            "whiff": bpa.to_float(row.get("swing_miss_percent")),
            "xwoba": bpa.to_float(row.get("xwoba")),
        })
    return pitches, season, "Prospect Savant"


def pack_statcast(pid: int, years: list[str], kind: str) -> tuple[list[dict], dict]:
    try:
        from pybaseball import cache, statcast_pitcher
        import pandas as pd
        cache.enable()
    except Exception as exc:  # noqa: BLE001
        return [], {"error": f"pybaseball unavailable: {exc}"}
    frames = []
    used = []
    ranges = SPRING_RANGES if kind == "spring" else REGULAR_RANGES
    for year, start, end in ranges:
        if year not in years:
            continue
        signal.alarm(25)
        try:
            frame = statcast_pitcher(start, end, pid)
        except Exception as exc:  # noqa: BLE001
            print(f"  statcast fail {pid} {year} {kind}: {exc}", flush=True)
            frame = None
        finally:
            signal.alarm(0)
        if frame is None or getattr(frame, "empty", True):
            continue
        if "game_type" in frame.columns:
            want = "S" if kind == "spring" else "R"
            frame = frame[frame["game_type"] == want]
        if frame is None or frame.empty:
            continue
        frames.append(frame)
        used.append(year)
        time.sleep(0.1)
    if not frames:
        return [], {}
    packed = bpa.aggregate_statcast(pd.concat(frames, ignore_index=True))
    overall = [
        pitch for pitch in (packed.get("overall") or [])
        if chart_code(pitch) and finite(pitch.get("ivb")) and finite(pitch.get("hb_pitcher"))
    ]
    packed["seasons"] = used
    return overall, packed


def merge_movement(row: dict, pitches: list[dict], *, source: str, season, throws: str, packed: dict | None) -> bool:
    if has_movement(row):
        return False
    incoming = []
    for pitch in pitches:
        code = chart_code(pitch)
        if not code or not finite(pitch.get("ivb")):
            continue
        item = dict(pitch)
        item["type"] = code
        item.pop("hb", None)
        if not finite(item.get("hb_pitcher")) and finite(item.get("hb_hitter")):
            item["hb_pitcher"] = round(-float(item["hb_hitter"]), 2)
        if not finite(item.get("hb_pitcher")):
            continue
        incoming.append(item)
    if not incoming:
        return False
    by_code = {chart_code(pitch): pitch for pitch in incoming}
    arsenal = list(row.get("arsenal") or [])
    matched = set()
    for pitch in arsenal:
        code = chart_code(pitch)
        source_pitch = by_code.get(code)
        if not source_pitch:
            continue
        matched.add(code)
        for key in ("ivb", "hb_pitcher", "hb_hitter", "spin"):
            if not finite(pitch.get(key)) and finite(source_pitch.get(key)):
                pitch[key] = source_pitch[key]
        if pitch.get("velo") in (None, "", 0, 0.0) and source_pitch.get("velo"):
            pitch["velo"] = source_pitch["velo"]
        if pitch.get("whiff") is None and source_pitch.get("whiff") is not None:
            pitch["whiff"] = source_pitch["whiff"]
        pitch.pop("hb", None)
    for code, source_pitch in by_code.items():
        if code not in matched:
            arsenal.append(source_pitch)
    row["arsenal"] = arsenal
    if throws and not row.get("throws"):
        row["throws"] = throws
    row["view"] = "hitter"
    if str(source).startswith("Prospect Savant"):
        row["ps_season"] = season
        row["ps_source"] = "Prospect Savant"
    else:
        row["statcast_source"] = source
        row["statcast_seasons"] = (packed or {}).get("seasons") or []
        row["statcast_pitches"] = (packed or {}).get("statcast_pitches")
    if packed and packed.get("usage_by_count"):
        fill_count_gaps(row, packed["usage_by_count"])
    return has_movement(row)


def fill_count_gaps(row: dict, usage_by_count: dict) -> bool:
    current = dict(row.get("count_mix") or {})
    changed = False

    def rows_for(bucket: str) -> list[dict]:
        return [
            {"type": pitch.get("type"), "usage": pitch.get("usage"), "pitches": pitch.get("pitches")}
            for pitch in (usage_by_count.get(bucket) or [])
            if chart_code(pitch)
        ]

    if not current.get("0-0") and rows_for("0-0"):
        current["0-0"] = rows_for("0-0")
        changed = True
    if not current.get("2_strikes"):
        totals: dict[str, int] = defaultdict(int)
        for bucket in ("0-2", "1-2", "2-2", "3-2"):
            for pitch in usage_by_count.get(bucket) or []:
                code = chart_code(pitch)
                if code:
                    totals[code] += int(pitch.get("pitches") or 0)
        total = sum(totals.values())
        if total:
            current["2_strikes"] = [
                {"type": code, "pitches": pitches, "usage": round(100.0 * pitches / total, 1)}
                for code, pitches in sorted(totals.items(), key=lambda item: -item[1])
            ]
            changed = True
    if not changed:
        return False
    row["count_mix"] = current
    usage = dict(row.get("usage_by_count") or {})
    for bucket in ("0-0", "2_strikes"):
        if current.get(bucket) and not usage.get(bucket):
            usage[bucket] = current[bucket]
    row["usage_by_count"] = usage
    if not row.get("statcast_source"):
        row["count_source"] = "MLB Statcast count buckets; existing mixes kept"
    return True


def ensure_arsenal_row(doc: dict, player: dict, checkpoint: dict) -> dict:
    by_key = index_rows(doc.get("pitchers") or [])
    row = by_key.get(player_key(player))
    if row:
        return row
    person_id = mlb_id_for(player, checkpoint) or player["id"]
    row = {
        "name": player["name"],
        "player_id": person_id,
        "team": player["team"],
        "throws": throws_for(player, checkpoint) or None,
        "arsenal": [],
        "view": "hitter",
    }
    doc.setdefault("pitchers", []).append(row)
    return row


def reapply_synergy(hitter_doc: dict, arsenal_doc: dict, heat_doc: dict) -> dict:
    path = RUNS / "synergy_blank_fill_checkpoint.json"
    if not path.is_file():
        return {"hitters": 0, "pitchers": 0}
    checkpoint = json.loads(path.read_text())
    hitters = {str(row.get("synergy_id") or ""): row for row in hitter_doc.get("hitters") or []}
    pitchers = {str(row.get("synergy_id") or ""): row for row in arsenal_doc.get("pitchers") or []}
    heat_h = heat_doc.setdefault("hitters", {})
    heat_p = heat_doc.setdefault("pitchers", {})
    changed_h = changed_p = 0
    for sid, packed in (checkpoint.get("hitters") or {}).items():
        row = hitters.get(str(sid))
        if not row:
            continue
        filled = blanks.apply_hitter(row, packed, heat_h)
        if filled["tables"] or filled["overall"] or filled["heat"]:
            changed_h += 1
    roster = {player_key(player): player for player in roster_players()[0]}
    for sid, packed in (checkpoint.get("pitchers") or {}).items():
        row = pitchers.get(str(sid))
        if not row:
            continue
        player = roster.get(player_key(row)) or {
            "team": row.get("team"),
            "name": row.get("name"),
            "id": int(row.get("player_id") or 0),
        }
        filled = blanks.apply_pitcher(row, player, packed, heat_p)
        if any(filled.values()):
            changed_p += 1
    return {"hitters": changed_h, "pitchers": changed_p}


def recount(hitters: list[dict], pitchers: list[dict], hitter_by, pitcher_by, arsenal_by, heat) -> dict:
    def heat_four(record):
        return bool(record and all(
            side_sum(record.get(side), field) > 0
            for side in ("vs_r", "vs_l") for field in ("ab", "swings")
        ))

    def heat_ab(record):
        return bool(record and side_sum(record.get("vs_r"), "ab") > 0 and side_sum(record.get("vs_l"), "ab") > 0)

    hitter_counts = {"roster": len(hitters), "season_bar": 0, "heat": 0, "advance_whiff": 0, "count_splits": 0, "all": 0}
    dashed = []
    for player in hitters:
        row = hitter_by.get(player_key(player)) or {}
        record = (heat.get("hitters") or {}).get(str(row.get("synergy_id") or ""))
        has_line = bool(row.get("summer_line"))
        has_heat = heat_four(record)
        has_whiff = bool(row.get("vs_r_by_pitch_type") and row.get("vs_l_by_pitch_type"))
        has_count = all(row.get(key) for key in (
            "pre2k_vs_r_by_pitch_type", "pre2k_vs_l_by_pitch_type",
            "twok_vs_r_by_pitch_type", "twok_vs_l_by_pitch_type",
        ))
        hitter_counts["season_bar"] += int(has_line)
        hitter_counts["heat"] += int(has_heat)
        hitter_counts["advance_whiff"] += int(has_whiff)
        hitter_counts["count_splits"] += int(has_count)
        hitter_counts["all"] += int(has_line and has_heat and has_whiff and has_count)
        if not has_line:
            dashed.append(f"{player['team']} {player['name']}")

    pitcher_counts = {
        "roster": len(pitchers), "movement_plot": 0, "whiff": 0, "counts": 0,
        "heat": 0, "season_bar": 0, "all": 0,
    }
    for player in pitchers:
        arsenal = arsenal_by.get(player_key(player)) or {}
        row = pitcher_by.get(player_key(player)) or {}
        plotted = has_movement(arsenal)
        whiff = any(pitch.get("whiff") is not None for pitch in (arsenal.get("arsenal") or []))
        mix = arsenal.get("count_mix") or {}
        usage = arsenal.get("usage_by_count") or {}
        two = mix.get("2_strikes") or [
            pitch for bucket in ("0-2", "1-2", "2-2", "3-2") for pitch in (usage.get(bucket) or [])
        ]
        counted = bool((mix.get("0-0") or usage.get("0-0")) and two)
        record = None
        bag = heat.get("pitchers") or {}
        for candidate in (player["id"], arsenal.get("player_id"), row.get("mlb_id"), arsenal.get("synergy_id")):
            if candidate is not None and str(candidate) in bag:
                record = bag[str(candidate)]
                break
        heated = heat_ab(record)
        lined = bool(row.get("summer_line"))
        pitcher_counts["movement_plot"] += int(plotted)
        pitcher_counts["whiff"] += int(whiff)
        pitcher_counts["counts"] += int(counted)
        pitcher_counts["heat"] += int(heated)
        pitcher_counts["season_bar"] += int(lined)
        pitcher_counts["all"] += int(plotted and whiff and counted and heated and lined)
    return {"hitters": hitter_counts, "pitchers": pitcher_counts, "dashed_hitters": dashed}


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    checkpoint = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.is_file() else {}
    pitchers, hitters = roster_players()
    print("resolving ids", flush=True)
    resolve_ids(hitters, checkpoint, pitcher=False)
    resolve_ids(pitchers, checkpoint, pitcher=True)
    classify_appearances(pitchers, checkpoint)

    hitter_doc = load("advance_opposing_hitters.json")
    pitcher_doc = load("advance_opposing_pitchers.json")
    arsenal_doc = load("pregame_pitcher_arsenals.json")
    heat_doc = load("advance_heatmaps.json")
    synergy_filled = reapply_synergy(hitter_doc, arsenal_doc, heat_doc)
    print(f"reapplied synergy checkpoint {synergy_filled}", flush=True)

    hitter_by = index_rows(hitter_doc.get("hitters") or [])
    pitcher_by = index_rows(pitcher_doc.get("pitchers") or [])
    missing_hitters = [
        player for player in hitters
        if not (hitter_by.get(player_key(player)) or {}).get("summer_line")
    ]
    print(f"hitter bars still dashed: {len(missing_hitters)}", flush=True)
    hitter_cache = checkpoint.setdefault("hitter_lines", {})
    need_ids = [
        mlb_id_for(player, checkpoint) or 0
        for player in missing_hitters
        if player_key(player) not in hitter_cache
    ]
    summer_2026 = hitting_lines(need_ids, "2026") if any(need_ids) else {}
    still = [value for value in need_ids if value and value not in summer_2026]
    summer_2025 = hitting_lines(still, "2025") if still else {}
    local_by_id, local_by_name = local_hitting_index()
    for player in missing_hitters:
        key = player_key(player)
        if key in hitter_cache:
            continue
        person_id = mlb_id_for(player, checkpoint) or 0
        line = summer_2026.get(person_id) or summer_2025.get(person_id)
        source = "statsapi" if line else None
        if not line:
            line = local_hitting_for(player, checkpoint, local_by_id, local_by_name)
            source = "local-lbprc" if line else None
        if not line:
            try:
                page, url = register_page(player["name"])
                line = bref_hitting_choice(page, url or "") if url else None
                source = "baseball-reference" if line else "no-season"
                if not url:
                    source = "ambiguous-register-name"
            except Exception as exc:  # noqa: BLE001
                source = "error"
                line = None
                hitter_cache[key] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                save_checkpoint(checkpoint)
                print(f"  bref error {player['name']}: {exc}", flush=True)
                continue
        hitter_cache[key] = {"status": "line" if line else source, "source": source, "line": line}
        save_checkpoint(checkpoint)
        print(f"  hitter {player['team']} {player['name']}: {source} {(line or {}).get('season')}", flush=True)
    for player in missing_hitters:
        cached = hitter_cache.get(player_key(player)) or {}
        row = hitter_by.get(player_key(player))
        if row and cached.get("line") and not row.get("summer_line"):
            row["summer_line"] = cached["line"]

    import backfill_pitcher_prior_seasons as prior

    missing_pitchers = [
        player for player in pitchers
        if not (pitcher_by.get(player_key(player)) or {}).get("summer_line")
    ]
    print(f"pitcher bars still empty: {len(missing_pitchers)}", flush=True)
    pitcher_cache = checkpoint.setdefault("pitcher_lines", {})
    need_pitcher_ids = [
        mlb_id_for(player, checkpoint) or 0
        for player in missing_pitchers
        if player_key(player) not in pitcher_cache
    ]
    try:
        pitcher_2026 = bas.fetch_2026_pitcher_summer_lines([value for value in need_pitcher_ids if value])
    except Exception as exc:  # noqa: BLE001
        print(f"2026 pitcher lines failed: {exc}", flush=True)
        pitcher_2026 = {}
    try:
        pitcher_2025 = prior.summer_2025_lines([
            value for value in need_pitcher_ids if value and value not in pitcher_2026
        ])
    except Exception as exc:  # noqa: BLE001
        print(f"2025 pitcher lines failed: {exc}", flush=True)
        pitcher_2025 = {}
    pitch_by_id, pitch_by_name = local_pitching_index()
    for player in missing_pitchers:
        key = player_key(player)
        if key in pitcher_cache:
            continue
        person_id = mlb_id_for(player, checkpoint) or (player["id"] if player["id"] > 0 else 0)
        line = pitcher_2026.get(person_id) or pitcher_2025.get(person_id)
        source = "statsapi" if line else None
        if not line:
            local = pitch_by_id.get(person_id)
            if not local:
                matches = pitch_by_name.get(norm(player["name"])) or []
                identities = {int(item.get("playerId") or 0) for item in matches}
                if matches and (len(matches) == 1 or len(identities) == 1):
                    local = max(matches, key=lambda item: prior.outs_from_ip(item.get("inningsPitched")))
            if local:
                line = prior.winter_line(local)
                source = "local-lbprc"
        if not line:
            try:
                page, url = register_page(player["name"])
                line = bref_pitching_choice(page, url or "") if url else None
                source = "baseball-reference" if line else ("ambiguous-register-name" if not url else "no-season")
            except Exception as exc:  # noqa: BLE001
                pitcher_cache[key] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                save_checkpoint(checkpoint)
                print(f"  pitcher bref error {player['name']}: {exc}", flush=True)
                continue
        pitcher_cache[key] = {"status": "line" if line else source, "source": source, "line": line}
        save_checkpoint(checkpoint)
        print(f"  pitcher {player['team']} {player['name']}: {source} {(line or {}).get('season')}", flush=True)
    for player in missing_pitchers:
        cached = pitcher_cache.get(player_key(player)) or {}
        row = pitcher_by.get(player_key(player))
        if row and cached.get("line") and not row.get("summer_line"):
            row["summer_line"] = cached["line"]

    movement_cache = checkpoint.setdefault("movement", {})
    arsenal_by = index_rows(arsenal_doc.get("pitchers") or [])
    for player in pitchers:
        key = player_key(player)
        row = arsenal_by.get(key) or ensure_arsenal_row(arsenal_doc, player, checkpoint)
        arsenal_by[key] = row
        if has_movement(row):
            continue
        levels = appearance_for(player, checkpoint)
        if not qualified(levels):
            reason = (checkpoint.get("resolved_ids") or {}).get(key, {}).get("reason")
            movement_cache[key] = {
                "status": "not-qualified",
                "reason": reason or "no MLB, AAA, or MLB spring appearance in 2024-2026",
                "levels": {name: levels.get(name) or [] for name in ("mlb", "aaa", "spring")},
            }
            continue
        if key in movement_cache and movement_cache[key].get("status") in {"filled", "no-tracked-movement"}:
            cached = movement_cache[key]
            if cached.get("status") == "filled" and cached.get("pitches"):
                merge_movement(
                    row, cached["pitches"], source=cached.get("source") or "",
                    season=cached.get("season"), throws=throws_for(player, checkpoint),
                    packed=cached.get("packed"),
                )
            continue
        person_id = mlb_id_for(player, checkpoint)
        print(
            f"movement {player['team']} {player['name']} id={person_id} "
            f"mlb={levels.get('mlb')} aaa={levels.get('aaa')} spring={levels.get('spring')}",
            flush=True,
        )
        pitches, season = pack_ps(person_id or 0)
        source = f"Prospect Savant {season}" if pitches else ""
        packed = None
        if not pitches and (levels.get("mlb") or levels.get("spring")):
            regular_years = levels.get("mlb") or ["2024", "2025", "2026"]
            regular, packed = pack_statcast(person_id or 0, regular_years, "regular")
            if regular:
                pitches, source = regular, "MLB Statcast regular " + ",".join(packed.get("seasons") or regular_years)
            elif levels.get("spring"):
                spring, spring_packed = pack_statcast(person_id or 0, levels.get("spring") or [], "spring")
                if spring:
                    pitches, packed = spring, spring_packed
                    source = "MLB Statcast spring " + ",".join(spring_packed.get("seasons") or levels.get("spring") or [])
        if pitches and merge_movement(
            row, pitches, source=source, season=season,
            throws=throws_for(player, checkpoint), packed=packed,
        ):
            movement_cache[key] = {
                "status": "filled",
                "source": source,
                "season": season,
                "pitches": pitches,
                "packed": {"usage_by_count": (packed or {}).get("usage_by_count") or {}, "seasons": (packed or {}).get("seasons") or []},
                "levels": {name: levels.get(name) or [] for name in ("mlb", "aaa", "spring")},
            }
            print(f"  filled via {source}", flush=True)
        else:
            if levels.get("mlb"):
                why = "pitched MLB but Prospect Savant and regular-season Statcast have no IVB/HB"
            elif levels.get("aaa"):
                why = "pitched AAA but Prospect Savant has no IVB/HB for 2024-2026"
            else:
                why = "MLB spring only, and neither spring Statcast nor regular-season/AAA movement was tracked"
            movement_cache[key] = {
                "status": "no-tracked-movement",
                "reason": why,
                "levels": {name: levels.get(name) or [] for name in ("mlb", "aaa", "spring")},
            }
            print(f"  no plot: {why}", flush=True)
        save_checkpoint(checkpoint)

    hitter_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pitcher_doc["updated"] = hitter_doc["updated"]
    arsenal_doc["updated"] = hitter_doc["updated"]
    dump(DATA / "advance_opposing_hitters.json", hitter_doc)
    dump(DATA / "advance_opposing_pitchers.json", pitcher_doc)
    dump(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
    if synergy_filled["hitters"] or synergy_filled["pitchers"]:
        heat_path = DATA / "advance_heatmaps.json"
        raw = heat_path.read_bytes()
        compact = json.dumps(heat_doc, separators=(",", ":"), ensure_ascii=False)
        text = compact if raw[:1] != b"{" or b"\n" not in raw[:200] else json.dumps(heat_doc, indent=2, ensure_ascii=False)
        if not text.endswith("\n") and raw.endswith(b"\n"):
            text += "\n"
        heat_path.write_text(text)

    hitter_by = index_rows(hitter_doc.get("hitters") or [])
    pitcher_by = index_rows(pitcher_doc.get("pitchers") or [])
    arsenal_by = index_rows(arsenal_doc.get("pitchers") or [])
    coverage = recount(hitters, pitchers, hitter_by, pitcher_by, arsenal_by, heat_doc)
    level_counts = {"mlb": 0, "aaa": 0, "spring": 0, "any": 0}
    still_missing = []
    for player in pitchers:
        levels = appearance_for(player, checkpoint)
        flags = {name: bool(levels.get(name)) for name in ("mlb", "aaa", "spring")}
        for name, flag in flags.items():
            level_counts[name] += int(flag)
        level_counts["any"] += int(any(flags.values()))
        row = arsenal_by.get(player_key(player)) or {}
        if any(flags.values()) and not has_movement(row):
            cached = movement_cache.get(player_key(player)) or {}
            still_missing.append({
                "team": player["team"],
                "name": player["name"],
                "mlb_id": mlb_id_for(player, checkpoint),
                "levels": {name: levels.get(name) or [] for name in ("mlb", "aaa", "spring")},
                "why": cached.get("reason") or "movement pull did not produce IVB/HB",
            })
    report = {
        "updated": hitter_doc["updated"],
        "answer": (
            "33/94 was how many pitcher pages drew an XY movement plot, not how many "
            "pitched in MLB, AAA, or big-league spring training."
        ),
        "appearances_2024_2026": level_counts,
        "qualified_still_without_plot": still_missing,
        "coverage": {key: value for key, value in coverage.items() if key != "dashed_hitters"},
        "dashed_hitter_season_bars": coverage["dashed_hitters"],
        "synergy_reapplied": synergy_filled,
        "bbref_requests": supplement.BREF_REQUESTS,
    }
    dump(REPORT, report)
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Research and attach missing opposing season stat bars from Baseball Reference."""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import unicodedata
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

import fill_remaining_holes as fr
import fill_pitcher_summer_supplement as supplement

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
REPORT_PATH = RUNS / "season_bar_gap_research.json"

BREF_MIN = 2.1
_LAST_BREF = 0.0

TARGETS = [
    ("hitter", "CAG", "Jean Correa"),
    ("hitter", "CAG", "Edison Mora"),
    ("hitter", "CAG", "Brett Rodriguez"),
    ("hitter", "CAG", "Hector Santiago"),
    ("hitter", "CAR", "Slater DeBrun"),
    ("hitter", "CAR", "Mervyl Melendez"),
    ("hitter", "MAY", "Cameron Maldonado"),
    ("hitter", "SJU", "Emmanuel Garcia"),
    ("hitter", "SJU", "Ivan Houellemont"),
    ("hitter", "SJU", "Ruben Ramirez"),
    ("pitcher", "CAG", "Sebastian Rodriguez"),
    ("pitcher", "MAY", "Luis Cruz"),
    ("pitcher", "MAY", "Nelvin Fuentes"),
    ("pitcher", "MAY", "Devin Rivera"),
    ("pitcher", "MAY", "Antonio Velez"),
    ("pitcher", "PON", "Hector Quinones"),
    ("pitcher", "SJU", "Alexuan Vega"),
]

FORCED_URL: dict[tuple[str, str], str] = {
    ("SJU", "Emmanuel Garcia"): "https://www.baseball-reference.com/register/player.fcgi?id=garcia020man",
    ("CAG", "Sebastian Rodriguez"): "https://www.baseball-reference.com/register/player.fcgi?id=rodrig001seb",
    ("MAY", "Devin Rivera"): "https://www.baseball-reference.com/register/player.fcgi?id=rivera002dev",
    ("MAY", "Antonio Velez"): "https://www.baseball-reference.com/register/player.fcgi?id=velez-001ant",
    ("MAY", "Nelvin Fuentes"): "https://www.baseball-reference.com/register/player.fcgi?id=fuente002nel",
    ("PON", "Hector Quinones"): "https://www.baseball-reference.com/register/player.fcgi?id=quinon002hec",
    ("SJU", "Alexuan Vega"): "https://www.baseball-reference.com/register/player.fcgi?id=vega--000ale",
}

REGISTER_CANDIDATES: dict[str, list[str]] = {
    "Jean Correa": ["correa000jea"],
    "Edison Mora": ["mora--002edi"],
    "Brett Rodriguez": ["rodrig001bre"],
    "Hector Santiago": ["santia002hec"],
    "Emmanuel Garcia": ["garcia020man"],
    "Ivan Houellemont": ["houell000iva"],
    "Ruben Ramirez": ["ramire003rub", "ramire000rub"],
    "Slater DeBrun": ["debru001sla", "debru000sla", "debru002sla", "debrun001sla"],
    "Cameron Maldonado": [],
    "Sebastian Rodriguez": ["rodrig001seb"],
    "Devin Rivera": ["rivera002dev"],
    "Nelvin Fuentes": ["fuente002nel"],
    "Antonio Velez": ["velez-001ant", "velez-000ant"],
    "Hector Quinones": ["quinon001hec", "quinon002hec"],
    "Alexuan Vega": ["vega--000ale", "vega--001ale", "vega--002ale"],
    "Luis Cruz": ["cruz--001lui", "cruz--000lui", "cruz--002lui", "cruz--003lui"],
}


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def is_pr(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        "puerto rico" in lowered
        or ", pr" in lowered
        or " pr," in lowered
        or lowered.endswith(" pr")
        or "bayamón" in lowered
        or "bayamon" in lowered
        or "caguas" in lowered
        or "ponce" in lowered
        or "carolina" in lowered
        or "mayagüez" in lowered
        or "mayaguez" in lowered
        or "santurce" in lowered
    )


def bref_fetch(url: str) -> tuple[str, str]:
    global _LAST_BREF
    import urllib.request

    delay = BREF_MIN - (time.monotonic() - _LAST_BREF)
    if delay > 0:
        time.sleep(delay)
    for attempt in range(5):
        req = urllib.request.Request(url, headers={"User-Agent": supplement.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=supplement.REQUEST_TIMEOUT) as response:
                page = response.read().decode("utf-8", "replace")
                _LAST_BREF = time.monotonic()
                return page, response.geturl()
        except Exception as exc:  # noqa: BLE001
            if "429" in str(exc) and attempt < 4:
                time.sleep(45 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def register_ids_from_search(page: str) -> list[str]:
    ids = re.findall(r"register/player\.fcgi\?id=([^\"&]+)", page)
    cleaned = []
    for player_id in ids:
        player_id = urllib.parse.unquote(player_id).strip()
        if re.fullmatch(r"[a-z0-9\-]+", player_id):
            cleaned.append(player_id)
    return list(dict.fromkeys(cleaned))


def page_meta(page: str) -> str:
    soup = BeautifulSoup(page, "html.parser")
    meta = soup.find("div", id="meta")
    return meta.get_text("\n", strip=True) if meta else ""


def season_label(row: dict, *, pitching: bool) -> tuple[str, str]:
    year = str(row["year"])
    level = fr.cell_value(row, "level", "level_ID") or ""
    ncaa = level == "NCAA" or "ncaa" in row["context"]
    if row["puerto_rico"] and row["winter"]:
        if year == "2026":
            return "2025-26 LBPRC winter", f"Baseball Reference Register {year} Puerto Rican winter"
        return "2025 LBPRC winter", f"Baseball Reference Register {year} Puerto Rican winter"
    if ncaa:
        return f"College {year}", f"Baseball Reference Register {year} NCAA"
    if year == "2026":
        return "Summer 2026", f"Baseball Reference Register {year} standard"
    if year == "2025":
        return "Summer 2025", f"Baseball Reference Register {year} standard"
    return f"Summer {year}", f"Baseball Reference Register {year} standard"


def extended_hitting_choice(page: str, source_url: str) -> dict | None:
    parsed = []
    for row in fr.table_rows(page, "standard_batting"):
        plate_appearances = supplement._int(fr.cell_value(row, "PA", "b_pa"))
        if plate_appearances:
            parsed.append((row, plate_appearances))
    if not parsed:
        return None

    def choose(predicate):
        rows = [(row, pa) for row, pa in parsed if predicate(row)]
        if not rows:
            return None
        rows.sort(key=lambda item: item[1], reverse=True)
        return rows[0]

    chosen = choose(lambda row: row["year"] == "2026" and not row["winter"])
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2025" and not row["winter"])
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2026" and row["puerto_rico"])
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2025" and row["puerto_rico"])
    if not chosen:
        non_winter = [(row, pa) for row, pa in parsed if not row["winter"]]
        if non_winter:
            non_winter.sort(key=lambda item: (int(item[0]["year"]), item[1]), reverse=True)
            chosen = non_winter[0]
    if not chosen:
        return None

    row, plate_appearances = chosen
    season, source = season_label(row, pitching=False)
    team = row["team"] if len(row["team"]) <= 5 else supplement._team_abbrev(row["team"])
    return {
        "team": team,
        "team_name": row["team_name"],
        "season": season,
        "league": row["league"] or ("LBPRC" if row["puerto_rico"] else ""),
        "circuit": "LBPRC" if row["puerto_rico"] else "Baseball Reference",
        "league_name": row["league_name"] or row["league"],
        "level": fr.cell_value(row, "level", "level_ID"),
        "pa": plate_appearances,
        "ab": supplement._int(fr.cell_value(row, "AB", "b_ab")),
        "r": supplement._int(fr.cell_value(row, "R", "b_r")),
        "h": supplement._int(fr.cell_value(row, "H", "b_h")),
        "doubles": supplement._int(fr.cell_value(row, "2B", "b_2b")),
        "triples": supplement._int(fr.cell_value(row, "3B", "b_3b")),
        "hr": supplement._int(fr.cell_value(row, "HR", "b_hr")),
        "rbi": supplement._int(fr.cell_value(row, "RBI", "b_rbi")),
        "sb": supplement._int(fr.cell_value(row, "SB", "b_sb")),
        "cs": supplement._int(fr.cell_value(row, "CS", "b_cs")),
        "bb": supplement._int(fr.cell_value(row, "BB", "b_bb")),
        "so": supplement._int(fr.cell_value(row, "SO", "b_so")),
        "ba": fr.cell_value(row, "batting_avg", "BA", "b_batting_avg"),
        "obp": fr.cell_value(row, "onbase_perc", "OBP", "b_onbase_perc"),
        "slg": fr.cell_value(row, "slugging_perc", "SLG", "b_slugging_perc"),
        "ops": fr.cell_value(row, "onbase_plus_slugging", "OPS", "b_onbase_plus_slugging"),
        "assignments": 1,
        "source": source + " batting",
        "source_url": source_url,
    }


def extended_pitching_choice(page: str, source_url: str) -> dict | None:
    import backfill_pitcher_prior_seasons as prior

    parsed = []
    for row in fr.table_rows(page, "standard_pitching"):
        outs = prior.outs_from_ip(fr.cell_value(row, "IP"))
        if outs > 0:
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
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2025" and not row["winter"])
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2026" and row["puerto_rico"])
    if not chosen:
        chosen = choose(lambda row: row["year"] == "2025" and row["puerto_rico"])
    if not chosen:
        non_winter = [(row, outs) for row, outs in parsed if not row["winter"]]
        if non_winter:
            non_winter.sort(key=lambda item: (int(item[0]["year"]), item[1]), reverse=True)
            chosen = non_winter[0]
    if not chosen:
        return None

    row, outs = chosen
    season, source = season_label(row, pitching=True)
    values = row["values"]
    innings = outs / 3
    strikeouts = supplement._int(fr.cell_value(row, "SO"))
    walks = supplement._int(fr.cell_value(row, "BB"))
    team = row["team"] if len(row["team"]) <= 5 else supplement._team_abbrev(row["team"])
    return {
        "team": team,
        "team_name": row["team_name"],
        "season": season,
        "league": row["league"] or ("LBPRC" if row["puerto_rico"] else ""),
        "circuit": "LBPRC" if row["puerto_rico"] else "Baseball Reference",
        "league_name": row["league_name"] or row["league"],
        "level": fr.cell_value(row, "level", "level_ID"),
        "ip": fr.cell_value(row, "IP"),
        "outs": outs,
        "g": supplement._int(fr.cell_value(row, "G")),
        "gs": supplement._int(fr.cell_value(row, "GS")),
        "k": strikeouts,
        "bb": walks,
        "k9": f"{(strikeouts or 0) * 9 / innings:.1f}",
        "bb9": f"{(walks or 0) * 9 / innings:.1f}",
        "era": supplement._rate(fr.cell_value(row, "earned_run_avg"), 2),
        "fip": prior.fip(
            {
                "homeRuns": supplement._int(fr.cell_value(row, "HR")),
                "baseOnBalls": walks,
                "hitByPitch": supplement._int(fr.cell_value(row, "HBP")),
                "strikeOuts": strikeouts,
            },
            outs,
        ),
        "whip": supplement._rate(fr.cell_value(row, "whip"), 2),
        "h": supplement._int(fr.cell_value(row, "H")),
        "hr": supplement._int(fr.cell_value(row, "HR")),
        "sb_allowed": None,
        "cs_allowed": None,
        "assignments": 1,
        "source": source + " pitching",
        "source_url": source_url,
        "field_sources": {"team_through_hr": source + " pitching"},
    }


def score_candidate(name: str, meta: str) -> int:
    score = 0
    if is_pr(meta):
        score += 50
    first = name.split()[0].lower() if name.split() else ""
    if first and first in meta.lower():
        score += 5
    return score


def candidate_urls(name: str, team: str = "") -> tuple[list[str], list[dict[str, Any]]]:
    log: list[dict[str, Any]] = []
    forced = FORCED_URL.get((team, name))
    if forced:
        return [forced], [{"url": forced, "reason": "confirmed identity"}]
    ids = list(REGISTER_CANDIDATES.get(name) or [])
    if ids:
        return [
            f"https://www.baseball-reference.com/register/player.fcgi?id={player_id}"
            for player_id in ids
        ], log
    search_url = (
        "https://www.baseball-reference.com/search/search.fcgi?search="
        + urllib.parse.quote_plus(name)
    )
    page, final = bref_fetch(search_url)
    if "register/player.fcgi" in final:
        return [final], [{"url": final, "reason": "search redirect"}]
    found = register_ids_from_search(page)
    log.append({"search_candidates": found})
    return [
        f"https://www.baseball-reference.com/register/player.fcgi?id={player_id}"
        for player_id in found[:8]
    ], log


def research_player(role: str, team: str, name: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "team": team,
        "name": name,
        "role": role,
        "accepted_url": None,
        "season": None,
        "line": None,
        "reason": None,
        "log": [],
    }
    try:
        urls, log = candidate_urls(name, team)
        result["log"] = log
        if not urls:
            result["reason"] = "no matching Baseball Reference register page"
            return result
        best: tuple[int, dict, str] | None = None
        for check_url in urls:
            page, final = bref_fetch(check_url)
            line = (
                extended_pitching_choice(page, final)
                if role == "pitcher"
                else extended_hitting_choice(page, final)
            )
            meta_text = page_meta(page)
            score = score_candidate(name, meta_text)
            title = BeautifulSoup(page, "html.parser").find("h1")
            title_text = title.get_text(" ", strip=True) if title else ""
            if norm(re.sub(r"\([^)]*\)", "", title_text)) == norm(name):
                score += 100
            if name == "Emmanuel Garcia" and "garcia020man" in final and is_pr(meta_text):
                score = 200
            if name == "Sebastian Rodriguez" and "rodrig001seb" in final and is_pr(meta_text):
                score = 200
            if "melend000mj-" in final and not is_pr(meta_text):
                score -= 80
            metric = (line or {}).get("pa") or (line or {}).get("outs") or 0
            if score < 100 and (team, name) not in FORCED_URL:
                log.append({"url": final, "score": score, "skipped": True})
                continue
            if (team, name) not in FORCED_URL and not is_pr(meta_text):
                log.append({"url": final, "score": score, "skipped": True, "reason": "not Puerto Rico birth"})
                continue
            log.append({"url": final, "score": score, "pr": is_pr(meta_text), "line": bool(line)})
            if line and (
                best is None
                or score > best[0]
                or (score == best[0] and metric > (best[1].get("pa") or best[1].get("outs") or 0))
            ):
                best = (score, line, final)
            elif not line and not result["accepted_url"]:
                result["accepted_url"] = final
        if not best:
            result["reason"] = "register page found but no countable season row"
            return result
        _, line, final = best
        result["accepted_url"] = final
        result["season"] = line.get("season")
        result["line"] = line
        return result
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result


def attach_lines(report: dict[str, Any]) -> int:
    attached = 0
    hitter_doc = json.loads((DATA / "advance_opposing_hitters.json").read_text())
    pitcher_doc = json.loads((DATA / "advance_opposing_pitchers.json").read_text())
    hitters = hitter_doc.get("hitters") or []
    pitchers = pitcher_doc.get("pitchers") or []

    for role, team, name in TARGETS:
        entry = report["players"].get(name) or {}
        line = entry.get("line")
        if not line:
            continue
        for row in hitters if role == "hitter" else pitchers:
            if row.get("team") == team and norm(str(row.get("name") or "")) == norm(name):
                if not row.get("summer_line"):
                    row["summer_line"] = line
                    attached += 1
                break

    if attached:
        (DATA / "advance_opposing_hitters.json").write_text(
            json.dumps(hitter_doc, ensure_ascii=False, indent=2) + "\n"
        )
        (DATA / "advance_opposing_pitchers.json").write_text(
            json.dumps(pitcher_doc, ensure_ascii=False, indent=2) + "\n"
        )
    return attached


def row_has_line(role: str, team: str, name: str) -> bool:
    doc = json.loads(
        (DATA / ("advance_opposing_pitchers.json" if role == "pitcher" else "advance_opposing_hitters.json")).read_text()
    )
    rows = doc.get("pitchers" if role == "pitcher" else "hitters") or []
    for row in rows:
        if row.get("team") == team and norm(str(row.get("name") or "")) == norm(name):
            return bool(row.get("summer_line"))
    return False


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    if REPORT_PATH.is_file():
        report = json.loads(REPORT_PATH.read_text())
    else:
        report = {
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "players": {},
            "attached": 0,
        }
    attached_total = int(report.get("attached") or 0)
    for role, team, name in TARGETS:
        if row_has_line(role, team, name):
            print(f"skip {team} {name} (already has line)", flush=True)
            continue
        print(f"research {team} {name} ({role})", flush=True)
        report["players"][name] = research_player(role, team, name)
        entry = report["players"][name]
        print(
            f"  -> {entry.get('accepted_url') or 'NONE'} "
            f"season={entry.get('season') or entry.get('reason')}",
            flush=True,
        )
        report["attached"] = attach_lines(report)
        attached_total = report["attached"]
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    report["attached"] = attached_total
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"attached={report['attached']}", flush=True)


if __name__ == "__main__":
    main()

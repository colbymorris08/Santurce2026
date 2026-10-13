#!/usr/bin/env python3
"""Fill empty 2026 pitcher summer lines from MiLB.com and Baseball Reference.

The primary MLB Stats API lines are preserved. This pass only:
1. adds a line when summer_line is empty, choosing the team with the most IP; or
2. fills an empty FIP/SB/CS cell on an existing line when the source has inputs.

Requests use a 20-second timeout. A per-player checkpoint is written after every
source attempt so interrupted runs resume without re-fetching completed players.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "advance_opposing_pitchers.json"
CHECKPOINT_PATH = ROOT / "data" / "pitcher_summer_supplement.json"
WEB_SOURCES_PATH = ROOT / "data" / "pitcher_summer_web_sources.json"
EMPTY_AUDIT_PATH = ROOT / "data" / "pitcher_summer_empty_audit.json"
REQUEST_TIMEOUT = 20
FIP_CONSTANT = 3.10
USER_AGENT = "Mozilla/5.0 (compatible; Santurce2026/1.0; pitcher-stat-research)"

WINTER_MARKERS = (
    "puerto rican",
    "roberto clemente",
    "dominican winter",
    "venezuelan winter",
    "mexican pacific",
    "caribbean series",
    "australian baseball",
    "lbprc",
    "lidom",
    "lvbp",
    "lmp",
    "prwl",
)


def _fetch(url: str) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        return response.read().decode("utf-8", "replace"), response.geturl()


def _slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _norm_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    text = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", text.lower())
    return re.sub(r"[^a-z0-9]+", "", text)


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rate(value: Any, places: int) -> str | None:
    if value is None or value == "":
        return None
    try:
        return f"{float(value):.{places}f}"
    except (TypeError, ValueError):
        return None


def _outs_from_ip(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    whole, dot, frac = text.partition(".")
    try:
        return int(whole) * 3 + (int(frac[:1]) if dot else 0)
    except ValueError:
        return 0


def _fip(hr: Any, bb: Any, hbp: Any, strikeouts: Any, outs: int) -> str | None:
    values = [_int(hr), _int(bb), _int(hbp), _int(strikeouts)]
    if outs <= 0 or any(v is None for v in values):
        return None
    home_runs, walks, hit_batters, ks = (int(v) for v in values)
    innings = outs / 3
    result = (
        13 * home_runs + 3 * (walks + hit_batters) - 2 * ks
    ) / innings + FIP_CONSTANT
    return f"{result:.2f}"


def _team_abbrev(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:3].upper()
    initials = "".join(word[0] for word in words)
    return initials[:4].upper()


def _checkpoint() -> dict:
    if CHECKPOINT_PATH.is_file():
        try:
            return json.loads(CHECKPOINT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "updated": None,
        "request_timeout_seconds": REQUEST_TIMEOUT,
        "fip_formula": "(13*HR + 3*(BB+HBP) - 2*K) / IP + 3.10",
        "players": {},
    }


def _save_checkpoint(checkpoint: dict) -> None:
    checkpoint["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    CHECKPOINT_PATH.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False))


def _milb_rows(name: str, mlb_id: int) -> tuple[list[dict], str]:
    url = f"https://www.milb.com/player/{_slug(name)}-{mlb_id}"
    page, final_url = _fetch(url)
    marker = "mlbStatsData:"
    marker_at = page.find(marker)
    if marker_at < 0:
        return [], final_url
    array_at = page.find("[", marker_at)
    if array_at < 0:
        return [], final_url
    payload, _ = json.JSONDecoder().raw_decode(page[array_at:])
    rows = []
    for row in payload:
        if (
            str(row.get("season") or "") != "2026"
            or row.get("group") != "pitching"
            or row.get("gameType") != "R"
            or row.get("hasMultiTeams")
        ):
            continue
        outs = _int(row.get("outs")) or _outs_from_ip(row.get("inningsPitched"))
        if outs <= 0 or not row.get("teamNameFull"):
            continue
        rows.append(dict(row, _outs=outs))
    return rows, final_url


def _milb_line(row: dict, source_url: str) -> dict:
    outs = int(row["_outs"])
    fip = _fip(
        row.get("homeRuns"),
        row.get("baseOnBalls"),
        row.get("hitBatsmen"),
        row.get("strikeOuts"),
        outs,
    )
    league_name = str(row.get("league") or row.get("leagueAbbrev") or "MiLB")
    return {
        "team": row.get("team") or _team_abbrev(str(row.get("teamNameFull") or "")),
        "team_name": row.get("teamNameFull"),
        "season": "Summer 2026",
        "league": row.get("leagueAbbrev") or league_name,
        "circuit": "MiLB.com",
        "league_name": league_name,
        "level": row.get("sportAbbreviation") or row.get("level"),
        "ip": row.get("inningsPitched"),
        "outs": outs,
        "g": _int(row.get("gamesPlayed") or row.get("gamesPitched")),
        "gs": _int(row.get("gamesStarted")),
        "k": _int(row.get("strikeOuts")),
        "bb": _int(row.get("baseOnBalls")),
        "k9": _rate(row.get("strikeoutsPer9Inn"), 1),
        "bb9": _rate(row.get("walksPer9Inn"), 1),
        "era": _rate(row.get("era"), 2),
        "fip": fip,
        "whip": _rate(row.get("whip"), 2),
        "h": _int(row.get("hits")),
        "hr": _int(row.get("homeRuns")),
        "sb_allowed": _int(row.get("stolenBases")),
        "cs_allowed": _int(row.get("caughtStealing")),
        "assignments": 1,
        "source": "MiLB.com 2026 regular-season year-by-year pitching",
        "source_url": source_url,
        "field_sources": {
            "team_through_hr": "MiLB.com year-by-year pitching",
            "sb_allowed": "MiLB.com pitching stolenBases",
            "cs_allowed": "MiLB.com pitching caughtStealing",
            "fip": (
                "Computed from MiLB.com HR/BB/HBP/K/IP with constant 3.10"
                if fip is not None else None
            ),
        },
    }


def _statsapi_lmb_fip(mlb_ids: list[int]) -> dict[int, dict]:
    """Return FIP inputs for 2026 LMB team splits; no counting line is replaced."""
    if not mlb_ids:
        return {}
    url = (
        "https://statsapi.mlb.com/api/v1/people?personIds="
        + ",".join(str(player_id) for player_id in sorted(set(mlb_ids)))
        + "&hydrate=stats(group=[pitching],type=[season],season=2026,sportId=23)"
    )
    page, _ = _fetch(url)
    payload = json.loads(page)
    result: dict[int, dict] = {}
    for person in payload.get("people") or []:
        rows = []
        for group in person.get("stats") or []:
            for split in group.get("splits") or []:
                if (
                    str(split.get("season") or "") != "2026"
                    or split.get("gameType") not in (None, "", "R")
                    or not (split.get("team") or {}).get("id")
                ):
                    continue
                stat = split.get("stat") or {}
                outs = _int(stat.get("outs")) or _outs_from_ip(stat.get("inningsPitched"))
                if outs > 0:
                    rows.append((outs, stat))
        if not rows:
            continue
        rows.sort(key=lambda pair: pair[0], reverse=True)
        outs, stat = rows[0]
        result[int(person["id"])] = {
            "outs": outs,
            "fip": _fip(
                stat.get("homeRuns"),
                stat.get("baseOnBalls"),
                stat.get("hitBatsmen"),
                stat.get("strikeOuts"),
                outs,
            ),
        }
    return result


def _register_candidates(page: str, name: str) -> list[str]:
    target = _norm_name(name)
    candidates: dict[str, int] = {}
    for match in re.finditer(
        r'<a[^>]+href="[^"]*register/player\.fcgi\?id=([^"&]+)[^"]*"[^>]*>(.*?)</a>',
        page,
        re.I | re.S,
    ):
        player_id = urllib.parse.unquote(match.group(1))
        label = html.unescape(re.sub(r"<[^>]+>", "", match.group(2)))
        clean_label = re.sub(r"\s*\([^)]*\)\s*$", "", label)
        score = (100 if _norm_name(clean_label) == target else 0) + (30 if "2026" in label else 0)
        candidates[player_id] = max(score, candidates.get(player_id, -1))
    return [
        player_id
        for player_id, score in sorted(candidates.items(), key=lambda item: item[1], reverse=True)
        if score >= 100
    ]


def _bref_rows(name: str) -> tuple[list[dict], str | None, list[str]]:
    search_url = (
        "https://www.baseball-reference.com/search/search.fcgi?search="
        + urllib.parse.quote_plus(name)
    )
    page, final_url = _fetch(search_url)
    candidates = _register_candidates(page, name)
    # A search can redirect to a register page; recover its canonical id.
    parsed = urllib.parse.urlparse(final_url)
    direct_id = urllib.parse.parse_qs(parsed.query).get("id", [])
    if "register/player.fcgi" in parsed.path and direct_id:
        candidates.insert(0, direct_id[0])
    if not candidates:
        return [], None, []

    player_id = candidates[0]
    source_url = f"https://www.baseball-reference.com/register/player.fcgi?id={player_id}"
    if source_url != final_url:
        page, final_url = _fetch(source_url)
    soup = BeautifulSoup(page, "html.parser")
    table = soup.find("table", id="standard_pitching")
    if table is None:
        return [], final_url, candidates

    rows = []
    for tr in table.select("tbody tr"):
        cells = {
            cell.get("data-stat"): cell
            for cell in tr.find_all(["th", "td"])
            if cell.get("data-stat")
        }
        if cells.get("year_ID") is None or cells["year_ID"].get_text(strip=True) != "2026":
            continue
        team_cell = cells.get("team_ID")
        league_cell = cells.get("lg_ID")
        team = team_cell.get_text(" ", strip=True) if team_cell else ""
        league = league_cell.get_text(" ", strip=True) if league_cell else ""
        league_name = ""
        if league_cell:
            link = league_cell.find("a")
            league_name = (link.get("title") if link else "") or league
        team_name = team
        if team_cell:
            link = team_cell.find("a")
            team_name = (link.get("title") if link else "") or team
        context = f"{team} {team_name} {league} {league_name}".lower()
        if not team or re.search(r"\b\d+\s+teams?\b", team.lower()):
            continue
        if any(marker in context for marker in WINTER_MARKERS):
            continue

        values = {
            key: (cell.get_text(" ", strip=True) or None)
            for key, cell in cells.items()
        }
        outs = _outs_from_ip(values.get("IP"))
        if outs <= 0:
            continue
        rows.append({
            "values": values,
            "outs": outs,
            "team": team,
            "team_name": team_name,
            "league": league,
            "league_name": league_name,
            "level": values.get("level"),
        })
    return rows, final_url, candidates


def _bref_line(row: dict, source_url: str, assignments: int) -> dict:
    values = row["values"]
    outs = row["outs"]
    fip = _fip(values.get("HR"), values.get("BB"), values.get("HBP"), values.get("SO"), outs)
    k9 = _rate(values.get("strikeouts_per_nine"), 1)
    bb9 = _rate(values.get("bases_on_balls_per_nine"), 1)
    if k9 is None and _int(values.get("SO")) is not None:
        k9 = f"{(_int(values['SO']) * 27 / outs):.1f}"
    if bb9 is None and _int(values.get("BB")) is not None:
        bb9 = f"{(_int(values['BB']) * 27 / outs):.1f}"
    level = row.get("level")
    circuit = "Baseball Reference"
    if level == "Ind":
        circuit = "Independent"
    elif level == "Fgn":
        circuit = "Foreign"
    return {
        "team": _team_abbrev(row["team"]),
        "team_name": row["team_name"],
        "season": "Summer 2026",
        "league": row["league"] or circuit,
        "circuit": circuit,
        "league_name": row["league_name"] or row["league"] or circuit,
        "level": level,
        "ip": values.get("IP"),
        "outs": outs,
        "g": _int(values.get("G")),
        "gs": _int(values.get("GS")),
        "k": _int(values.get("SO")),
        "bb": _int(values.get("BB")),
        "k9": k9,
        "bb9": bb9,
        "era": _rate(values.get("earned_run_avg"), 2),
        "fip": fip,
        "whip": _rate(values.get("whip"), 2),
        "h": _int(values.get("H")),
        "hr": _int(values.get("HR")),
        "sb_allowed": None,
        "cs_allowed": None,
        "assignments": assignments,
        "source": "Baseball Reference Register 2026 regular-season pitching",
        "source_url": source_url,
        "field_sources": {
            "team_through_hr": "Baseball Reference Register standard pitching",
            "sb_allowed": None,
            "cs_allowed": None,
            "fip": (
                "Computed from Baseball Reference HR/BB/HBP/K/IP with constant 3.10"
                if fip is not None else None
            ),
        },
    }


def _verified_web_line(row: dict) -> dict:
    outs = _outs_from_ip(row.get("ip"))
    fip = _fip(row.get("hr"), row.get("bb"), row.get("hbp"), row.get("k"), outs)
    innings = outs / 3
    return {
        "team": row.get("team"),
        "team_name": row.get("team_name"),
        "season": "Summer 2026",
        "league": row.get("league"),
        "circuit": "Independent" if row.get("level") == "Ind" else "Foreign",
        "league_name": row.get("league_name"),
        "level": row.get("level"),
        "ip": row.get("ip"),
        "outs": outs,
        "g": _int(row.get("g")),
        "gs": _int(row.get("gs")),
        "k": _int(row.get("k")),
        "bb": _int(row.get("bb")),
        "k9": f"{(_int(row.get('k')) * 9 / innings):.1f}",
        "bb9": f"{(_int(row.get('bb')) * 9 / innings):.1f}",
        "era": _rate(row.get("era"), 2),
        "fip": fip,
        "whip": _rate(row.get("whip"), 2),
        "h": _int(row.get("h")),
        "hr": _int(row.get("hr")),
        "sb_allowed": None,
        "cs_allowed": None,
        "assignments": 1,
        "source": "Baseball Reference Register 2026 regular-season pitching",
        "source_url": row.get("source_url"),
        "field_sources": {
            "team_through_hr": "Baseball Reference Register standard pitching",
            "sb_allowed": None,
            "cs_allowed": None,
            "fip": "Computed from Baseball Reference HR/BB/HBP/K/IP with constant 3.10",
        },
    }


def _merge_enrichment(current: dict, candidate: dict) -> bool:
    changed = False
    if current.get("fip") in (None, "") and candidate.get("fip") not in (None, ""):
        current["fip"] = candidate["fip"]
        current.setdefault("field_sources", {})["fip"] = candidate["field_sources"]["fip"]
        changed = True
    for key in ("sb_allowed", "cs_allowed"):
        if current.get(key) is None and candidate.get(key) is not None:
            current[key] = candidate[key]
            current.setdefault("field_sources", {})[key] = candidate["field_sources"][key]
            changed = True
    return changed


def main() -> None:
    document = json.loads(DATA_PATH.read_text())
    pitchers = document.get("pitchers") or []
    checkpoint = _checkpoint()
    saved = checkpoint.setdefault("players", {})

    # MLBAM ids are duplicated for Eduardo Rivera; fetch each unique person once.
    by_id: dict[int, list[dict]] = {}
    for pitcher in pitchers:
        mlb_id = _int(pitcher.get("mlb_id"))
        if mlb_id:
            by_id.setdefault(mlb_id, []).append(pitcher)

    # Rows verified through WebFetch after Baseball Reference rate-limited the
    # resumable scraper. These are keyed by roster MLBAM id and only fill blanks.
    verified = json.loads(WEB_SOURCES_PATH.read_text()).get("rows") or []
    for row in verified:
        mlb_id = _int(row.get("mlb_id"))
        records = by_id.get(mlb_id or -1) or []
        if not records or any(record.get("name") != row.get("name") for record in records):
            raise ValueError(f"Verified row does not match roster identity: {row.get('name')} ({mlb_id})")
        candidate = _verified_web_line(row)
        for record in records:
            if not record.get("summer_line"):
                record["summer_line"] = dict(candidate)
        entry = saved.setdefault(str(mlb_id), {"name": row.get("name")})
        entry["verified_web"] = {
            "status": "line",
            "source_url": row.get("source_url"),
            "line": candidate,
        }
        _save_checkpoint(checkpoint)

    # MiLB.com first: fill genuinely empty lines or enrich only empty FIP/SB/CS.
    for mlb_id, records in by_id.items():
        needs_line = any(not record.get("summer_line") for record in records)
        needs_enrichment = any(
            record.get("summer_line")
            and any(record["summer_line"].get(key) is None for key in ("fip", "sb_allowed", "cs_allowed"))
            for record in records
        )
        if not needs_line and not needs_enrichment:
            continue
        entry = saved.setdefault(str(mlb_id), {"name": records[0].get("name")})
        if "milb" not in entry:
            try:
                rows, source_url = _milb_rows(records[0]["name"], mlb_id)
                rows.sort(key=lambda row: row["_outs"], reverse=True)
                entry["milb"] = {
                    "status": "line" if rows else "no_2026_pitching",
                    "source_url": source_url,
                    "rows": len(rows),
                    "line": _milb_line(rows[0], source_url) if rows else None,
                }
            except Exception as exc:  # noqa: BLE001
                entry["milb"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            _save_checkpoint(checkpoint)
            time.sleep(0.05)

        milb_line = (entry.get("milb") or {}).get("line")
        if milb_line:
            for record in records:
                current = record.get("summer_line")
                if not current:
                    record["summer_line"] = dict(milb_line)
                elif current.get("outs") == milb_line.get("outs"):
                    _merge_enrichment(current, milb_line)

    # Existing LMB counting lines are already authoritative. Query only the
    # omitted HBP input and compute their empty FIP cell; never replace the line.
    lmb_fip_ids = [
        mlb_id
        for mlb_id, records in by_id.items()
        if any(
            record.get("summer_line")
            and record["summer_line"].get("circuit") == "LMB"
            and record["summer_line"].get("fip") in (None, "")
            for record in records
        )
    ]
    if lmb_fip_ids:
        try:
            lmb_fips = _statsapi_lmb_fip(lmb_fip_ids)
            for mlb_id in lmb_fip_ids:
                enrichment = lmb_fips.get(mlb_id) or {}
                for record in by_id[mlb_id]:
                    current = record.get("summer_line") or {}
                    if (
                        current.get("circuit") == "LMB"
                        and current.get("fip") in (None, "")
                        and current.get("outs") == enrichment.get("outs")
                        and enrichment.get("fip") is not None
                    ):
                        current["fip"] = enrichment["fip"]
                        current.setdefault("field_sources", {})["fip"] = (
                            "Computed from MLB Stats API LMB HR/BB/HBP/K/IP with constant 3.10"
                        )
                entry = saved.setdefault(str(mlb_id), {"name": by_id[mlb_id][0].get("name")})
                entry["statsapi_lmb_fip"] = {
                    "status": "filled" if enrichment.get("fip") is not None else "unavailable",
                    "fip": enrichment.get("fip"),
                }
                _save_checkpoint(checkpoint)
        except Exception as exc:  # noqa: BLE001
            print(f"LMB FIP enrichment failed: {type(exc).__name__}: {exc}")

    # Baseball Reference only for rows still lacking a line. Never redo a good line.
    for mlb_id, records in by_id.items():
        if os.environ.get("SKIP_BREF"):
            break
        if not any(not record.get("summer_line") for record in records):
            continue
        entry = saved.setdefault(str(mlb_id), {"name": records[0].get("name")})
        if (
            "baseball_reference" not in entry
            or (entry.get("baseball_reference") or {}).get("status") == "error"
        ):
            try:
                rows, source_url, candidates = _bref_rows(records[0]["name"])
                rows.sort(key=lambda row: row["outs"], reverse=True)
                entry["baseball_reference"] = {
                    "status": "line" if rows else "no_2026_summer_pitching",
                    "source_url": source_url,
                    "candidates": candidates,
                    "rows": len(rows),
                    "line": (
                        _bref_line(rows[0], source_url or "", len(rows))
                        if rows and source_url else None
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                entry["baseball_reference"] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            _save_checkpoint(checkpoint)
            time.sleep(0.2)

        bref_line = (entry.get("baseball_reference") or {}).get("line")
        if bref_line:
            for record in records:
                if not record.get("summer_line"):
                    record["summer_line"] = dict(bref_line)

    empty_audit = json.loads(EMPTY_AUDIT_PATH.read_text())
    audit_by_name = {row["name"]: row for row in empty_audit.get("rows") or []}
    missing_names = {record.get("name") for record in pitchers if not record.get("summer_line")}
    if missing_names != set(audit_by_name):
        raise ValueError(
            "Empty-row audit does not match generated data: "
            f"missing_from_audit={sorted(missing_names - set(audit_by_name))}, "
            f"stale_audit={sorted(set(audit_by_name) - missing_names)}"
        )
    for mlb_id, records in by_id.items():
        audit = audit_by_name.get(records[0].get("name"))
        if audit:
            entry = saved.setdefault(str(mlb_id), {"name": records[0].get("name")})
            entry["verified_empty_audit"] = audit
            _save_checkpoint(checkpoint)

    document["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    document["summer_line_supplement"] = {
        "sources": [
            "MiLB.com embedded year-by-year pitching",
            "Baseball Reference Register standard pitching",
            "Baseball Reference register rows verified with Cursor WebFetch",
        ],
        "selection": "2026 regular-season team row with most innings; combined team totals ignored",
        "fip_formula": checkpoint["fip_formula"],
        "checkpoint": "data/pitcher_summer_supplement.json",
        "empty_row_audit": "data/pitcher_summer_empty_audit.json",
    }
    DATA_PATH.write_text(json.dumps(document, indent=2, ensure_ascii=False))
    _save_checkpoint(checkpoint)

    filled = [p for p in pitchers if p.get("summer_line")]
    missing = [p.get("name") for p in pitchers if not p.get("summer_line")]
    fip_count = sum((p.get("summer_line") or {}).get("fip") is not None for p in pitchers)
    print(
        f"coverage={len(filled)}/{len(pitchers)} missing={len(missing)} "
        f"fip={fip_count} checkpoint={CHECKPOINT_PATH.name}"
    )
    if missing:
        print("missing:", ", ".join(str(name) for name in missing))


if __name__ == "__main__":
    main()

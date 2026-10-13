#!/usr/bin/env python3
"""Finish holes left after the first remaining-holes run.

Does not repeat a lookup that already produced a season line or a movement
plot. Baseball Reference stays at or under 30 requests per minute.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path

import fill_pitcher_summer_supplement as supplement
import fill_remaining_holes as holes

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def uncomment(page: str) -> str:
    return page.replace("<!--", " ").replace("-->", " ")


def attach_pitcher_line(document: dict, arsenal_by: dict, player: dict, line: dict) -> None:
    key = holes.player_key(player)
    by_name = holes.index_rows(document.get("pitchers") or [])
    row = by_name.get(key)
    if row:
        if not row.get("summer_line"):
            row["summer_line"] = line
        return
    arsenal = arsenal_by.get(key) or {}
    document.setdefault("pitchers", []).append({
        "synergy_id": arsenal.get("synergy_id"),
        "name": player["name"],
        "team": player["team"],
        "mlb_id": arsenal.get("player_id") if arsenal.get("player_id") is not None else player["id"],
        "throws": arsenal.get("throws"),
        "summer_line": line,
        "source": "Season line attached for a roster arm that has no Synergy packet",
    })


def bref_mlb_page(mlb_id: int) -> tuple[str, str]:
    url = f"https://www.baseball-reference.com/redirect.fcgi?player=1&mlb_ID={int(mlb_id)}"
    page, final_url = supplement._fetch(url)
    return uncomment(page), final_url


def bref_name_line(name: str, choose):
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
    ordered = list(dict.fromkeys(candidates))[:4]
    if not ordered:
        return None, "no-register-page"
    pages = []
    if "register/player.fcgi" in parsed.path and direct and direct[0] == ordered[0]:
        pages.append((uncomment(page), final_url))
    for player_id in ordered:
        source_url = f"https://www.baseball-reference.com/register/player.fcgi?id={player_id}"
        if pages and pages[-1][1] == source_url:
            continue
        fetched, fetched_url = supplement._fetch(source_url)
        pages.append((uncomment(fetched), fetched_url))
    found = []
    for html, url in pages:
        line = choose(html, url)
        if line:
            found.append(line)
    if len(found) == 1:
        return found[0], "baseball-reference"
    if len(found) > 1:
        return None, "ambiguous-register-name"
    return None, "no-season"


def lookup_line(player: dict, checkpoint: dict, *, pitcher: bool):
    key = holes.player_key(player)
    cache = checkpoint["pitcher_lines" if pitcher else "hitter_lines"]
    cached = cache.get(key) or {}
    if cached.get("line"):
        return cached["line"], cached.get("source")
    if pitcher:
        pitch_by_id, pitch_by_name = holes.local_pitching_index()
        local = holes.local_pitching_for(player, checkpoint, pitch_by_id, pitch_by_name)
        if local:
            cache[key] = {"status": "line", "source": "local-lbprc", "line": local}
            holes.save_checkpoint(checkpoint)
            return local, "local-lbprc"
    else:
        hit_by_id, hit_by_name = holes.local_hitting_index()
        local = holes.local_hitting_for(player, checkpoint, hit_by_id, hit_by_name)
        if local:
            cache[key] = {"status": "line", "source": "local-lbprc", "line": local}
            holes.save_checkpoint(checkpoint)
            return local, "local-lbprc"
    person_id = holes.mlb_id_for(player, checkpoint)
    choose = holes.bref_pitching_choice if pitcher else holes.bref_hitting_choice
    try:
        if person_id:
            page, url = bref_mlb_page(person_id)
            line = choose(page, url)
            status = "baseball-reference" if line else "no-season"
        else:
            line, status = bref_name_line(player["name"], choose)
    except Exception as exc:  # noqa: BLE001
        cache[key] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        holes.save_checkpoint(checkpoint)
        print(f"  bref error {player['name']}: {exc}", flush=True)
        return None, "error"
    cache[key] = {"status": "line" if line else status, "source": status, "line": line}
    holes.save_checkpoint(checkpoint)
    print(f"  {player['team']} {player['name']}: {status} {(line or {}).get('season')}", flush=True)
    return line, status


def retry_movement(pitchers: list[dict], checkpoint: dict, arsenal_doc: dict) -> None:
    arsenal_by = holes.index_rows(arsenal_doc.get("pitchers") or [])
    targets = {"PON|michaelmorales", "MAY|daviangarcia"}
    for player in pitchers:
        key = holes.player_key(player)
        if key not in targets:
            continue
        row = arsenal_by.get(key)
        if not row or holes.has_movement(row):
            continue
        checkpoint.get("movement", {}).pop(key, None)
        person_id = holes.mlb_id_for(player, checkpoint) or 0
        levels = holes.appearance_for(player, checkpoint)
        print(f"retry movement {player['team']} {player['name']} {person_id}", flush=True)
        pitches, season, source, packed = [], None, "", None
        if key == "PON|michaelmorales":
            pitches, season = holes.pack_ps(person_id)
            if pitches:
                source = f"Prospect Savant {season}"
        if not pitches:
            if levels.get("mlb") or key == "PON|michaelmorales":
                regular_years = levels.get("mlb") or ["2024", "2025", "2026"]
                regular, packed = holes.pack_statcast(person_id, regular_years, "regular")
                if regular:
                    pitches = regular
                    source = "MLB Statcast regular " + ",".join((packed or {}).get("seasons") or regular_years)
            if not pitches and levels.get("spring"):
                spring, spring_packed = holes.pack_statcast(person_id, levels.get("spring") or [], "spring")
                if spring:
                    pitches, packed = spring, spring_packed
                    source = "MLB Statcast spring " + ",".join((spring_packed or {}).get("seasons") or [])
        cache = checkpoint.setdefault("movement", {})
        if pitches and holes.merge_movement(
            row, pitches, source=source, season=season,
            throws=holes.throws_for(player, checkpoint), packed=packed,
        ):
            cache[key] = {
                "status": "filled",
                "source": source,
                "season": season,
                "levels": {name: levels.get(name) or [] for name in ("mlb", "aaa", "spring")},
            }
            print(f"  filled via {source}", flush=True)
        else:
            why = (
                "MLB spring only, and a retry of Prospect Savant plus Statcast still has no IVB/HB"
                if key == "PON|michaelmorales"
                else "MLB spring only; regular-season Statcast was empty and the spring retry has no IVB/HB"
            )
            cache[key] = {
                "status": "no-tracked-movement",
                "reason": why,
                "levels": {name: levels.get(name) or [] for name in ("mlb", "aaa", "spring")},
            }
            print(f"  no plot: {why}", flush=True)
        holes.save_checkpoint(checkpoint)


def main() -> None:
    checkpoint = json.loads(holes.CHECKPOINT.read_text())
    pitchers, hitters = holes.roster_players()
    hitter_doc = holes.load("advance_opposing_hitters.json")
    pitcher_doc = holes.load("advance_opposing_pitchers.json")
    arsenal_doc = holes.load("pregame_pitcher_arsenals.json")
    heat_doc = holes.load("advance_heatmaps.json")
    hitter_by = holes.index_rows(hitter_doc.get("hitters") or [])
    arsenal_by = holes.index_rows(arsenal_doc.get("pitchers") or [])

    for player in hitters:
        cached = (checkpoint.get("hitter_lines") or {}).get(holes.player_key(player)) or {}
        row = hitter_by.get(holes.player_key(player))
        if row and cached.get("line") and not row.get("summer_line"):
            row["summer_line"] = cached["line"]
    for player in pitchers:
        cached = (checkpoint.get("pitcher_lines") or {}).get(holes.player_key(player)) or {}
        if cached.get("line"):
            attach_pitcher_line(pitcher_doc, arsenal_by, player, cached["line"])

    import backfill_pitcher_prior_seasons as prior

    pitcher_by = holes.index_rows(pitcher_doc.get("pitchers") or [])
    still_pitchers = [
        player for player in pitchers
        if not (pitcher_by.get(holes.player_key(player)) or {}).get("summer_line")
    ]
    retry_ids = [holes.mlb_id_for(player, checkpoint) or 0 for player in still_pitchers]
    try:
        summer_2025 = prior.summer_2025_lines([value for value in retry_ids if value])
    except Exception as exc:  # noqa: BLE001
        print(f"2025 pitcher retry failed: {exc}", flush=True)
        summer_2025 = {}
    for player in still_pitchers:
        person_id = holes.mlb_id_for(player, checkpoint) or 0
        line = summer_2025.get(person_id)
        if not line:
            continue
        key = holes.player_key(player)
        checkpoint.setdefault("pitcher_lines", {})[key] = {
            "status": "line", "source": "statsapi", "line": line,
        }
        attach_pitcher_line(pitcher_doc, arsenal_by, player, line)
        print(f"  pitcher {player['name']}: statsapi {line.get('season')}", flush=True)
    holes.save_checkpoint(checkpoint)

    hitter_by = holes.index_rows(hitter_doc.get("hitters") or [])
    pitcher_by = holes.index_rows(pitcher_doc.get("pitchers") or [])
    for player in hitters:
        row = hitter_by.get(holes.player_key(player))
        if row and row.get("summer_line"):
            continue
        line, _status = lookup_line(player, checkpoint, pitcher=False)
        if row and line:
            row["summer_line"] = line
    for player in pitchers:
        row = pitcher_by.get(holes.player_key(player))
        if row and row.get("summer_line"):
            continue
        line, _status = lookup_line(player, checkpoint, pitcher=True)
        if line:
            attach_pitcher_line(pitcher_doc, arsenal_by, player, line)

    retry_movement(pitchers, checkpoint, arsenal_doc)

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    hitter_doc["updated"] = stamp
    pitcher_doc["updated"] = stamp
    arsenal_doc["updated"] = stamp
    holes.dump(DATA / "advance_opposing_hitters.json", hitter_doc)
    holes.dump(DATA / "advance_opposing_pitchers.json", pitcher_doc)
    holes.dump(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)

    hitter_by = holes.index_rows(hitter_doc.get("hitters") or [])
    pitcher_by = holes.index_rows(pitcher_doc.get("pitchers") or [])
    arsenal_by = holes.index_rows(arsenal_doc.get("pitchers") or [])
    coverage = holes.recount(hitters, pitchers, hitter_by, pitcher_by, arsenal_by, heat_doc)
    level_counts = {"mlb": 0, "aaa": 0, "spring": 0, "any": 0}
    still_missing = []
    for player in pitchers:
        levels = holes.appearance_for(player, checkpoint)
        flags = {name: bool(levels.get(name)) for name in ("mlb", "aaa", "spring")}
        for name, flag in flags.items():
            level_counts[name] += int(flag)
        level_counts["any"] += int(any(flags.values()))
        row = arsenal_by.get(holes.player_key(player)) or {}
        if any(flags.values()) and not holes.has_movement(row):
            cached = (checkpoint.get("movement") or {}).get(holes.player_key(player)) or {}
            still_missing.append({
                "team": player["team"],
                "name": player["name"],
                "mlb_id": holes.mlb_id_for(player, checkpoint),
                "levels": {name: levels.get(name) or [] for name in ("mlb", "aaa", "spring")},
                "why": cached.get("reason") or "movement pull did not produce IVB/HB",
            })
    report = {
        "updated": stamp,
        "answer": (
            "33/94 was how many pitcher pages drew an XY movement plot, not how many "
            "pitched in MLB, AAA, or big-league spring training."
        ),
        "appearances_2024_2026": level_counts,
        "qualified_still_without_plot": still_missing,
        "coverage": {key: value for key, value in coverage.items() if key != "dashed_hitters"},
        "dashed_hitter_season_bars": coverage["dashed_hitters"],
        "bbref_requests": supplement.BREF_REQUESTS,
    }
    holes.dump(holes.REPORT, report)
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

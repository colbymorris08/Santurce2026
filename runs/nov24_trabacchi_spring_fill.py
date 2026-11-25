#!/usr/bin/env python3
"""Fill Nick Trabacchi from his two tracked 2026 MLB spring appearances."""

from __future__ import annotations

import json
import re
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_advance_synergy as bas  # noqa: E402

DATA = ROOT / "data"
RUNS = ROOT / "runs"
MLB_ID = 806713
NAME = "Nick Trabacchi"
GAME_PKS = (831658, 831648)
SOURCE = "MLB Stats API live feed · 2026 Spring Training (MIN)"


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, obj) -> None:
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def avg(values) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def fetch_pitches() -> list[dict]:
    pitches = []
    result_map = {
        "single": "Single",
        "double": "Double",
        "triple": "Triple",
        "home_run": "HomeRun",
        "strikeout": "Strikeout",
        "field_out": "Groundout",
        "force_out": "Forceout",
        "grounded_into_double_play": "DoublePlay",
        "walk": "Walk",
        "hit_by_pitch": "HitByPitch",
    }
    call_map = {
        "Foul": "Foul",
        "In play, out(s)": "InPlay",
        "In play, run(s)": "InPlay",
        "In play, no out": "InPlay",
        "Swinging Strike": "StrikeSwinging",
        "Swinging Strike (Blocked)": "StrikeSwinging",
        "Foul Tip": "FoulTip",
    }
    for game_pk in GAME_PKS:
        url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
        with urllib.request.urlopen(url, timeout=30) as resp:
            doc = json.load(resp)
        game_date = (doc.get("gameData") or {}).get("datetime", {}).get("officialDate")
        for play in doc.get("liveData", {}).get("plays", {}).get("allPlays", []):
            matchup = play.get("matchup") or {}
            if (matchup.get("pitcher") or {}).get("id") != MLB_ID:
                continue
            about = play.get("about") or {}
            pa_result = result_map.get(str((play.get("result") or {}).get("eventType") or ""))
            for event in play.get("playEvents") or []:
                if not event.get("isPitch"):
                    continue
                details = event.get("details") or {}
                pitch_data = event.get("pitchData") or {}
                coordinates = pitch_data.get("coordinates") or {}
                breaks = pitch_data.get("breaks") or {}
                pitch_type = (details.get("type") or {}).get("code")
                if not pitch_type:
                    continue
                pitches.append(
                    {
                        "game_pk": game_pk,
                        "game_date": game_date,
                        "inning": about.get("inning"),
                        "inning_top": str(about.get("halfInning") or "").lower() == "top",
                        "pa_number": play.get("atBatIndex"),
                        "pitch_number": event.get("pitchNumber"),
                        "batter": (matchup.get("batter") or {}).get("fullName"),
                        "bat_side": (matchup.get("batSide") or {}).get("code"),
                        "type": pitch_type,
                        "name": (details.get("type") or {}).get("description") or pitch_type,
                        "call": (details.get("call") or {}).get("description"),
                        "pitch_result": call_map.get(
                            str((details.get("call") or {}).get("description") or ""),
                            "CalledStrike"
                            if "Strike" in str((details.get("call") or {}).get("description") or "")
                            else "Ball",
                        ),
                        "pa_result": pa_result,
                        "velo": pitch_data.get("startSpeed"),
                        "spin": breaks.get("spinRate"),
                        "ivb": breaks.get("breakVerticalInduced"),
                        # Stats API breakHorizontal is pitcher-oriented here:
                        # RHP FF is positive (arm side), SL/FC negative (glove side).
                        "hb_pitcher": breaks.get("breakHorizontal"),
                        "px": coordinates.get("pX"),
                        "pz": coordinates.get("pZ"),
                        "zone_top": pitch_data.get("strikeZoneTop"),
                        "zone_bottom": pitch_data.get("strikeZoneBottom"),
                    }
                )
    return pitches


def movement_rows(pitches: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for pitch in pitches:
        grouped[pitch["type"]].append(pitch)
    rows = []
    total = len(pitches)
    for code, group in grouped.items():
        hb = avg(p["hb_pitcher"] for p in group)
        rows.append(
            {
                "type": code,
                "name": group[0]["name"],
                "movement_pitches": len(group),
                "movement_usage": round(100 * len(group) / total, 1),
                "velo": round(avg(p["velo"] for p in group) or 0, 1),
                "spin": int(round(avg(p["spin"] for p in group) or 0)),
                "ivb": round(avg(p["ivb"] for p in group) or 0, 1),
                "hb_pitcher": round(hb or 0, 1),
                "hb_hitter": round(-(hb or 0), 1),
                "movement_source": SOURCE,
            }
        )
    rows.sort(key=lambda row: -row["movement_pitches"])
    return rows


def merge_arsenal(existing: list[dict], movement: list[dict]) -> list[dict]:
    by_type = {str(row.get("type") or row.get("pitch_type")): row for row in existing}
    for tracked in movement:
        code = tracked["type"]
        row = by_type.get(code)
        if row is None:
            row = {
                "type": code,
                "name": tracked["name"],
                "usage": None,
                "pitches": tracked["movement_pitches"],
                "whiff": None,
                "xwoba": None,
                "sample_context": "2026 MLB Spring Training only",
            }
            existing.append(row)
            by_type[code] = row
        for key in (
            "velo",
            "spin",
            "ivb",
            "hb_pitcher",
            "hb_hitter",
            "movement_pitches",
            "movement_usage",
            "movement_source",
        ):
            row[key] = tracked[key]
    return existing


def heat_record(pitches: list[dict]) -> dict:
    events = []
    for p in pitches:
        events.append(
            {
                "eventType": "Pitch",
                "inning": p["inning"],
                "inningTop": p["inning_top"],
                "inningPlateAppearanceNumber": p["pa_number"],
                "plateAppearanceResult": p["pa_result"],
                "batterInfo": {"battingSide": p["bat_side"]},
                "game": {"id": p["game_pk"], "season": 2026},
                "pitch": {
                    "px": p["px"],
                    "pz": p["pz"],
                    "pitchResult": p["pitch_result"],
                    "strikeZoneInchesTop": float(p["zone_top"]) * 12 if p["zone_top"] is not None else None,
                    "strikeZoneInchesBottom": float(p["zone_bottom"]) * 12 if p["zone_bottom"] is not None else None,
                },
            }
        )
    heat = bas.aggregate_location_heat(events, split="batter_hand")
    heat.update(
        {
            "name": NAME,
            "team": "PON",
            "mlb_id": MLB_ID,
            "window": "2026 MLB Spring Training",
            "raw": len(pitches),
            "kept": len(pitches),
            "sources": {"MLB ST 2026": len(pitches)},
            "window_used": "2026 MLB Spring Training",
            "game_pks": list(GAME_PKS),
        }
    )
    return heat


def find_named(rows: list[dict]) -> dict:
    return next(row for row in rows if row.get("name") == NAME)


def update_site(pitches: list[dict], movement: list[dict]) -> str:
    note = (
        "Movement/location: 13 tracked pitches from two Minnesota Twins 2026 Spring Training "
        "appearances (Feb 23 and Feb 27; MLB Stats API). Usage/whiff still from the larger "
        "2026 High Point Synergy PDF sample."
    )

    arsenals = load("pregame_pitcher_arsenals.json")
    arm = find_named(arsenals["pitchers"])
    arm["player_id"] = MLB_ID
    arm["mlb_id"] = MLB_ID
    arm["throws"] = "R"
    arm["arsenal"] = merge_arsenal(arm.get("arsenal") or [], movement)
    arm["movement_source"] = SOURCE
    arm["movement_pitches"] = len(pitches)
    arm["movement_game_pks"] = list(GAME_PKS)
    arm["note"] = note
    dump("pregame_pitcher_arsenals.json", arsenals)

    advance = load("advance_opposing_pitchers.json")
    adv = find_named(advance["pitchers"])
    adv["mlb_id"] = MLB_ID
    adv["player_id"] = MLB_ID
    adv["throws"] = "R"
    adv["arsenal"] = merge_arsenal(adv.get("arsenal") or [], movement)
    adv["movement_source"] = SOURCE
    adv["movement_pitches"] = len(pitches)
    adv["movement_game_pks"] = list(GAME_PKS)
    adv["note"] = note
    dump("advance_opposing_pitchers.json", advance)

    one_page = load("pitcher_one_page.json")
    one = find_named(one_page["teams"]["PON"]["pitchers"])
    one["mlb_id"] = MLB_ID
    one["player_id"] = MLB_ID
    one["throws"] = "R"
    one["velo"].update({"FB": "91-93", "SL": "83-86", "CT": "86-86"})
    one["movement"] = movement
    one["movement_source"] = SOURCE
    one["movement_pitches"] = len(pitches)
    one["notes"] = note
    dump("pitcher_one_page.json", one_page)

    stuff = load("pregame_opposing_stuff.json")
    stuff_row = find_named(stuff)
    stuff_row["player_id"] = MLB_ID
    stuff_row["movement"] = movement
    stuff_row["movement_source"] = SOURCE
    stuff_row["movement_pitches"] = len(pitches)
    dump("pregame_opposing_stuff.json", stuff)

    # Correct the synthetic roster ID anywhere it identifies this same player.
    for name in ("official_active_pitchers.json", "lbprc_2025_rosters.json", "pregame_card_stats.json"):
        doc = load(name)

        def walk(value):
            if isinstance(value, dict):
                if value.get("name") == NAME:
                    for key in ("id", "mlb_id", "player_id"):
                        if key in value:
                            value[key] = MLB_ID
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(doc)
        dump(name, doc)

    heats = load("advance_heatmaps.json")
    heats.setdefault("pitchers", {})[str(MLB_ID)] = heat_record(pitches)
    old_key = "-154132952"
    if old_key in heats["pitchers"]:
        del heats["pitchers"][old_key]
    all_records = list((heats.get("hitters") or {}).values()) + list(heats["pitchers"].values())
    heats["coverage"]["pitchers_with_maps"] = sum(
        1 for row in heats["pitchers"].values() if int(row.get("located") or 0) > 0
    )
    heats["coverage"]["located_by_source"] = bas._source_rollup(all_records)
    heats["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dump("advance_heatmaps.json", heats)

    html = (ROOT / "pregame.html").read_text()
    match = re.search(r"const ASSET_V = 'san(\d+)'", html)
    if not match:
        raise RuntimeError("ASSET_V not found")
    old = f"san{match.group(1)}"
    new = f"san{int(match.group(1)) + 1}"
    (ROOT / "pregame.html").write_text(html.replace(old, new))
    return new


def main() -> None:
    pitches = fetch_pitches()
    if len(pitches) != 13 or any(p.get("px") is None or p.get("pz") is None for p in pitches):
        raise RuntimeError(f"Expected 13 located pitches, got {len(pitches)}")
    movement = movement_rows(pitches)
    asset = update_site(pitches, movement)
    report = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "name": NAME,
        "mlb_id": MLB_ID,
        "pr_2026_27": False,
        "mlb_spring_training_2025": False,
        "mlb_spring_training_2026": True,
        "games": list(GAME_PKS),
        "pitches": len(pitches),
        "located": sum(p.get("px") is not None and p.get("pz") is not None for p in pitches),
        "movement": movement,
        "asset_v": asset,
        "source": SOURCE,
    }
    (RUNS / "nov24_trabacchi_spring_fill_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

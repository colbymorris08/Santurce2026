#!/usr/bin/env python3
"""Replace wrong MLB 688014 RHP data with Caguas LHP BR sanche004edw."""

from __future__ import annotations

import json
from pathlib import Path

from update_2026_27_rosters import stable_id

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

OLD = 688014
NEW = stable_id("CAG", "Edwin Sanchez")
BR_URL = "https://www.baseball-reference.com/register/player.fcgi?id=sanche004edw"
BR_ID = "sanche004edw"
CHADWICK = "344a9357"
WRONG_SYN = "5fd12a915fe05a0e9fc1144f"

SUMMER = {
    "team": "LE",
    "team_name": "Lake Erie Crushers (Avon, OH)",
    "season": "Summer 2026",
    "league": "FRON",
    "circuit": "Independent",
    "league_name": "Frontier League",
    "level": "Ind",
    "ip": "78.2",
    "outs": 236,
    "g": 20,
    "gs": 13,
    "k": 62,
    "bb": 57,
    "k9": "7.1",
    "bb9": "6.5",
    "era": "6.29",
    "fip": "5.08",
    "whip": "1.67",
    "h": 74,
    "hr": 7,
    "sb_allowed": None,
    "cs_allowed": None,
    "assignments": 2,
    "source": "Baseball Reference Register 2026 regular-season pitching",
    "source_url": BR_URL,
    "field_sources": {
        "team_through_hr": "Baseball Reference Register standard pitching",
        "sb_allowed": None,
        "cs_allowed": None,
        "fip": "Computed from Baseball Reference HR/BB/HBP/K/IP with constant 3.10",
    },
    "br_id": BR_ID,
    "chadwick_id": CHADWICK,
    "throws": "L",
    "bats": "L",
    "birth_date": "2004-01-20",
    "birth_place": "Guayama, Puerto Rico",
}

COLLEGE_2026 = {
    "team": "B-CU",
    "team_name": "Bethune-Cookman",
    "season": "College 2026",
    "league": "SWAC",
    "league_name": "Southwestern Athletic Conference",
    "level": "NCAA",
    "ip": "76.2",
    "g": 15,
    "gs": 15,
    "k": 72,
    "bb": 33,
    "k9": "8.5",
    "bb9": "3.9",
    "era": "3.17",
    "fip": None,
    "whip": "1.23",
    "h": 61,
    "hr": 3,
    "source": "Baseball Reference Register 2026 NCAA pitching",
    "source_url": BR_URL,
}


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc, *, compact: bool = False) -> None:
    path = DATA / name
    if compact:
        path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def walk_replace_ids(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            nk = str(NEW) if k == str(OLD) else k
            out[nk] = walk_replace_ids(v)
        return out
    if isinstance(obj, list):
        return [walk_replace_ids(x) for x in obj]
    if obj == OLD:
        return NEW
    if obj == str(OLD):
        return str(NEW)
    return obj


def clear_pitch_shape(p: dict) -> None:
    p["arsenal"] = []
    p["count_mix"] = {}
    p["count_hand_mix"] = {}
    p["usage_by_count"] = {}
    p["platoon"] = {}
    p["hand_n"] = {}
    p["pitches"] = 0
    p["leagues"] = {}
    p["years"] = []
    p["window_used"] = None
    p["arm_slot_image"] = None
    p["arsenal_image"] = None
    p["synergy_id"] = None
    for k in list(p.keys()):
        if k.startswith("synergy_"):
            p[k] = {} if isinstance(p.get(k), dict) else ([] if isinstance(p.get(k), list) else None)


def main() -> None:
    assert NEW == -217783280, NEW
    print(f"remap {OLD} -> {NEW} ({BR_ID})")

    rost = load("lbprc_2025_rosters.json")
    for team in rost.values():
        if team.get("teamAbbrev") != "CAG":
            continue
        for p in team.get("players") or []:
            if p.get("id") == OLD or (p.get("name") or "") == "Edwin Sanchez":
                p["id"] = NEW
                p["throws"] = "L"
                p["status"] = "Active"
                p["position"] = "P"
                p["jersey"] = p.get("jersey") or "68"
                p["br_id"] = BR_ID
                print("roster", p["name"], p["id"])
    dump("lbprc_2025_rosters.json", rost)

    off = load("official_active_pitchers.json")
    for p in off.get("teams", {}).get("CAG") or []:
        if p.get("id") == OLD or p.get("name") == "Edwin Sanchez":
            p["id"] = NEW
            p["throws"] = "L"
            p["br_id"] = BR_ID
            print("official", p)
    dump("official_active_pitchers.json", off)

    adv = load("advance_opposing_pitchers.json")
    for p in adv["pitchers"]:
        if p.get("mlb_id") == OLD or p.get("player_id") == OLD or (
            p.get("team") == "CAG" and (p.get("name") or "") == "Edwin Sanchez"
        ):
            clear_pitch_shape(p)
            p["name"] = "Edwin Sanchez"
            p["team"] = "CAG"
            p["throws"] = "L"
            p["mlb_id"] = None
            p["player_id"] = NEW
            p["br_id"] = BR_ID
            p["chadwick_id"] = CHADWICK
            p["summer_line"] = dict(SUMMER)
            p["college_line"] = dict(COLLEGE_2026)
            p["lbprc_line"] = None
            p["winter_line"] = None
            p["source"] = "identity corrected · BR sanche004edw (cleared MLB 688014 RHP Synergy)"
            p["identity_note"] = (
                "Removed wrong Edwin Sanchez MLB/MiLB 688014 (RHP, b.2000-11-03 Caguas). "
                "This is Bethune-Cookman / Lake Erie LHP sanche004edw (b.2004-01-20 Guayama)."
            )
            print("advance remapped")
    dump("advance_opposing_pitchers.json", adv)

    ars = load("pregame_pitcher_arsenals.json")
    for p in ars["pitchers"]:
        if p.get("player_id") == OLD or (
            p.get("team") == "CAG" and (p.get("name") or "") == "Edwin Sanchez"
        ):
            clear_pitch_shape(p)
            p["player_id"] = NEW
            p["mlb_id"] = None
            p["throws"] = "L"
            p["br_id"] = BR_ID
            p["summer_line"] = dict(SUMMER)
            print("arsenal remapped")
    dump("pregame_pitcher_arsenals.json", ars)

    heat = load("advance_heatmaps.json")
    removed = []
    pitchers = heat.get("pitchers")
    if isinstance(pitchers, dict):
        for key in list(pitchers.keys()):
            rec = pitchers[key]
            drop = key in {str(OLD), WRONG_SYN} or key == OLD
            if isinstance(rec, dict) and rec.get("name") == "Edwin Sanchez":
                if str(rec.get("player_id")) == str(OLD) or rec.get("synergy_id") == WRONG_SYN:
                    drop = True
            if drop:
                del pitchers[key]
                removed.append(str(key))
    print("heat removed", removed)
    dump("advance_heatmaps.json", heat, compact=True)

    op = load("pitcher_one_page.json")
    for abbr, t in (op.get("teams") or {}).items():
        for p in t.get("pitchers") or []:
            if p.get("mlb_id") == OLD or p.get("name") == "Edwin Sanchez":
                p["mlb_id"] = NEW
                p["throws"] = "L"
                p["velo"] = {k: "" for k in ["FB", "CB", "SL", "CH", "CT", "SPL"]}
                p["freq_r"] = ""
                p["freq_l"] = ""
                p["freq_html"] = ""
                p["needs_data"] = True
                p["br_id"] = BR_ID
                p["summer_line"] = dict(SUMMER)
                print("onepage", abbr)
    dump("pitcher_one_page.json", op)

    cs = load("pregame_card_stats.json")
    for p in cs.get("players") or []:
        if p.get("id") == OLD or (p.get("name") == "Edwin Sanchez" and p.get("team") == "CAG"):
            p["id"] = NEW
            p["throws"] = "L"
            p["pitcher"] = {
                "bf": None,
                "k": None,
                "gb": None,
                "bb": None,
                "ops": None,
                "avg": None,
                "wh": None,
                "wh_l": None,
                "wh_r": None,
                "gs": SUMMER["gs"],
                "hand_split": False,
                "src": "Summer 2026 Lake Erie (BR sanche004edw)",
                "throws": "L",
            }
            print("card", p["id"])
    dump("pregame_card_stats.json", cs)

    g = walk_replace_ids(load("game_day_state.json"))
    pen = g["strategy"]["teams"]["CAG"]["pen"]
    print("game_day pen NEW", NEW in pen, "OLD", OLD in pen)
    dump("game_day_state.json", g)

    for name in ("card_whiff.json", "pregame_tth_pop.json", "pregame_opposing_stuff.json"):
        doc = load(name)
        changed = 0

        def fix(o):
            nonlocal changed
            if isinstance(o, dict):
                if o.get("id") == OLD:
                    o["id"] = NEW
                    changed += 1
                if o.get("player_id") == OLD:
                    o["player_id"] = NEW
                    changed += 1
                if (o.get("name") == "Edwin Sanchez") and (
                    o.get("id") == NEW or o.get("player_id") == NEW
                ):
                    for k in ("wh", "wh_l", "wh_r", "swings", "stuff", "ps_stuff", "synergy_id"):
                        if k in o:
                            o[k] = None
                for v in o.values():
                    fix(v)
            elif isinstance(o, list):
                for x in o:
                    fix(x)

        fix(doc)
        dump(name, doc)
        print(name, "changed", changed)

    # summer supplement keys vary
    sup = load("pitcher_summer_supplement.json")
    bag = sup.get("pitchers") if isinstance(sup.get("pitchers"), dict) else sup
    if str(OLD) in bag:
        rec = bag.pop(str(OLD))
        rec["name"] = "Edwin Sanchez"
        rec["milb"] = {
            "status": "not_this_player",
            "note": "MLB/MiLB 688014 is a different RHP (b.2000). Caguas LHP has no MLBAM id.",
            "source_url": None,
            "rows": 0,
            "line": None,
        }
        rec["baseball_reference"] = {
            "status": "line",
            "source_url": BR_URL,
            "candidates": [BR_ID],
            "rows": 3,
            "line": dict(SUMMER),
            "college_2026": dict(COLLEGE_2026),
        }
        bag[str(NEW)] = rec
        print("supplement remapped")
        dump("pitcher_summer_supplement.json", sup)

    # card_season_api may key by id
    api = load("card_season_api.json")
    if str(OLD) in api:
        api.pop(str(OLD), None)
        print("removed card_season_api", OLD)
        dump("card_season_api.json", api, compact=True)

    print("done", NEW)


if __name__ == "__main__":
    main()

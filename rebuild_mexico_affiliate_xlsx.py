#!/usr/bin/env python3
"""Rebuild Mexico_or_affiliate_pitchers.xlsx for pitchers missing movement plots."""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

BASE = Path(__file__).resolve().parent
OUT = BASE / "Mexico_or_affiliate_pitchers.xlsx"
DOWNLOADS = Path.home() / "Downloads" / "Mexico_or_affiliate_pitchers.xlsx"

TEAM_NAME = {
    "CAG": "Caguas",
    "CAR": "Carolina",
    "MAY": "Mayaguez",
    "PON": "Ponce",
    "SJU": "San Juan",
    "SAN": "Santurce",
}

# Last known Mexico / AA-and-below club for pitchers without a usable MLB id.
# Used only when Stats API has no positive id; never invents AAA/MLB careers.
PRIOR_LINES = {
    "jarrette bonet": ("Wisconsin Timber Rattlers", 2026, "A"),
    "adiel melendez": ("Augusta GreenJackets", 2026, "A"),
    "johzan oquendo": ("Knoxville Smokies", 2025, "AA"),
    "eddy demurias": ("Algodoneros de Union Laguna", 2026, "MEX"),
    "jonathan haab": ("Tecos de los Dos Laredos", 2026, "MEX"),
    "charlee soto": ("Fort Myers Mighty Mussels", 2026, "A"),
    "mario zabala": ("Lake Elsinore Storm", 2025, "A+"),
    "tyler gough": ("Great Lakes Loons", 2026, "A+"),
    "randy ramnarace": ("Columbia Fireflies", 2026, "A"),
    "alexuan vega": ("Lake Elsinore Storm", 2022, "A+"),
    "harold cortijo": ("Pericos de Puebla", 2025, "MEX"),
    "eric garcia": ("FCL Phillies", 2023, "Rk"),
    "brady tedesco": ("Tigres de Quintana Roo", 2026, "MEX"),
    "bryant salgado": ("Saraperos de Saltillo", 2026, "MEX"),
    "derek west": ("Leones de Yucatan", 2026, "MEX"),
    "joe riley": ("Sultanes de Monterrey", 2026, "MEX"),
}

# Highest affiliated level rank (lower = higher level).
LEVEL_RANK = {
    "MLB": 1,
    "AAA": 2,
    "AA": 3,
    "A+": 4,
    "A": 5,
    "Rk": 6,
    "FRk": 7,
    "MEX": 8,
}

SPORTS = [1, 11, 12, 13, 14, 16, 21, 22, 23]


def strip_accents(s: str) -> str:
    nk = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nk if not unicodedata.combining(c))


def split_name(full: str) -> tuple[str, str]:
    name = re.sub(r"\s+", " ", (full or "").strip())
    if not name:
        return "", ""
    parts = name.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    # Keep Jr/Sr/II with last
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
    if parts[-1].lower().rstrip(".") in {s.rstrip(".") for s in suffixes} and len(parts) >= 3:
        last = " ".join(parts[-2:])
        first = " ".join(parts[:-2])
    else:
        last = parts[-1]
        first = " ".join(parts[:-1])
    return last, first


def has_xy(arsenal: list | None) -> bool:
    for a in arsenal or []:
        if a.get("ivb") is None:
            continue
        if a.get("hb_pitcher") is not None or a.get("hb_hitter") is not None or a.get("hb") is not None:
            return True
    return False


def api_get(url: str) -> dict | list | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Santurce2026/mexico-xlsx"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def classify_level(sport_id: int, league_name: str, team_name: str) -> str | None:
    ln = (league_name or "").lower()
    tn = (team_name or "").lower()
    # League name first — Mexican League is sometimes stored under sportId 11.
    if "mexican pacific" in ln or "liga mexicana del pacifico" in ln:
        return None
    if "mexican" in ln or "liga mexicana" in ln:
        return "MEX"
    if any(
        x in tn
        for x in (
            "oaxaca",
            "chihuahua",
            "monterrey",
            "tijuana",
            "yucatan",
            "saltillo",
            "puebla",
            "durango",
            "quintana",
            "laguna",
            "laredos",
            "leon",
            "aguascalientes",
            "campeche",
            "tabasco",
            "veracruz",
            "diablos",
            "guerreros",
            "sultanes",
            "algodoner",
            "saraperos",
            "pericos",
            "caliente",
            "tecos",
            "bravos de",
            "toros de tijuana",
            "tigres de quintana",
            "dorados de",
        )
    ):
        return "MEX"
    if sport_id == 1:
        return "MLB"
    if sport_id == 11:
        return "AAA"
    if sport_id == 12:
        return "AA"
    if sport_id == 13:
        return "A+"
    if sport_id == 14:
        return "A"
    if sport_id == 16:
        if "dominican" in ln or "dsl" in ln:
            return "FRk"
        return "Rk"
    if any(
        x in ln
        for x in (
            "atlantic",
            "american association",
            "frontier",
            "pioneer",
            "independent",
            "winter",
            "puerto rican",
            "dominican winter",
        )
    ):
        return None
    return None


def org_from_team(team: dict) -> str:
    parent = (team.get("parentOrgName") or "").strip()
    if parent:
        return parent
    return (team.get("name") or team.get("teamName") or "").strip()


def career_rows(mlb_id: int) -> list[dict]:
    rows = []
    for sport in SPORTS:
        url = (
            f"https://statsapi.mlb.com/api/v1/people/{mlb_id}/stats"
            f"?stats=yearByYear&group=pitching&sportId={sport}&gameType=R"
        )
        doc = api_get(url)
        time.sleep(0.12)
        if not isinstance(doc, dict):
            continue
        for block in doc.get("stats") or []:
            for split in block.get("splits") or []:
                season = split.get("season")
                try:
                    year = int(season)
                except (TypeError, ValueError):
                    continue
                team = split.get("team") or {}
                league = split.get("league") or {}
                sport_info = split.get("sport") or {}
                sport_id = int(sport_info.get("id") or sport)
                level = classify_level(sport_id, league.get("name") or "", team.get("name") or "")
                if not level:
                    continue
                rows.append(
                    {
                        "year": year,
                        "level": level,
                        "team": team.get("name") or team.get("teamName") or "",
                        "org": org_from_team(team),
                        "league": league.get("name") or "",
                        "sport_id": sport_id,
                    }
                )
    return rows


def pick_recent_mex_or_aa_below(rows: list[dict]) -> dict | None:
    """Most recent Mexican League or AA-and-below affiliate line."""
    ranked = []
    for r in rows:
        lvl = r["level"]
        if lvl == "MEX" or LEVEL_RANK.get(lvl, 99) >= LEVEL_RANK["AA"]:
            ranked.append(r)
    if not ranked:
        return None
    ranked.sort(key=lambda r: (r["year"], -LEVEL_RANK.get(r["level"], 99)), reverse=True)
    return ranked[0]


def qualifies_aa_below_or_mexico_only(rows: list[dict]) -> bool:
    if not rows:
        return False
    levels = {r["level"] for r in rows}
    if "MLB" in levels or "AAA" in levels:
        return False
    # AA and below, and/or Mexico
    return bool(levels & {"AA", "A+", "A", "Rk", "FRk", "MEX"})


def main() -> None:
    ars = json.loads((BASE / "data/pregame_pitcher_arsenals.json").read_text())
    hm = json.loads((BASE / "data/advance_heatmaps.json").read_text())
    card = json.loads((BASE / "data/pregame_card_stats.json").read_text())
    rod = json.loads((BASE / "data/advance_rodriguez.json").read_text())
    adv = json.loads((BASE / "data/advance_opposing_hitters.json").read_text())

    pitchers = list(ars.get("pitchers") or [])
    if not any(str(r.get("player_id")) == "605446" for r in pitchers):
        pitchers.append(
            {
                "name": rod.get("name") or "Dereck Rodriguez",
                "player_id": 605446,
                "team": rod.get("team") or "CAG",
                "arsenal": rod.get("arsenal") or [],
                "synergy_id": None,
            }
        )

    selectable = [r for r in pitchers if r.get("arsenal")]
    need_plot = [r for r in selectable if not has_xy(r.get("arsenal"))]
    with_plot = [r for r in selectable if has_xy(r.get("arsenal"))]

    hpit = hm.get("pitchers") or {}
    hhit = hm.get("hitters") or {}

    def pitcher_heat(r: dict) -> bool:
        mid = r.get("player_id")
        try:
            if mid is not None and int(mid) > 0:
                rec = hpit.get(str(mid))
                if rec and int(rec.get("located") or 0) > 0:
                    return True
        except (TypeError, ValueError):
            pass
        sid = r.get("synergy_id")
        if sid:
            rec = hpit.get(str(sid))
            if rec and int(rec.get("located") or 0) > 0:
                return True
        return False

    mex_rows = []
    for r in sorted(need_plot, key=lambda x: (x.get("team") or "", x.get("name") or "")):
        name = r.get("name") or ""
        last, first = split_name(name)
        pr = TEAM_NAME.get(r.get("team") or "", r.get("team") or "")
        mid = r.get("player_id")
        try:
            mid_i = int(mid) if mid is not None else 0
        except (TypeError, ValueError):
            mid_i = 0

        level = org = year = None
        if mid_i > 0:
            rows = career_rows(mid_i)
            if not qualifies_aa_below_or_mexico_only(rows):
                continue  # has AAA/MLB, or no mex/aa-below line
            pick = pick_recent_mex_or_aa_below(rows)
            if not pick:
                continue
            level = pick["level"]
            # Org = MLB parent when present; else the club (Mexico teams have no parent).
            if level == "MEX":
                org = pick["team"] or pick["org"]
            elif pick["org"] and pick["org"] != pick["team"]:
                org = pick["org"]
            else:
                org = pick["team"] or pick["org"]
            # Keep club visible in Org when parent is used
            if level != "MEX" and pick["team"] and org != pick["team"]:
                org = f"{org} — {pick['team']}"
            year = pick["year"]
        else:
            prev = PRIOR_LINES.get(strip_accents(name.lower()))
            if not prev:
                continue
            org, year, level = prev

        mex_rows.append(
            {
                "name": name,
                "pr": pr,
                "last": last,
                "first": first,
                "level": level or "",
                "org": org or "",
                "year": year,
            }
        )

    # Coverage summary
    H = [p for p in card["players"] if p.get("hitter")]
    P = [p for p in card["players"] if p.get("pitcher")]

    def filled(rows, key, fields):
        return sum(1 for p in rows if all((p.get(key) or {}).get(f) is not None for f in fields))

    adv_hitters = adv.get("hitters") or []
    by_mlb = {}
    by_name = {}
    for h in adv_hitters:
        mid = h.get("mlb_id") or h.get("player_id")
        try:
            if mid is not None and int(mid) > 0:
                by_mlb[int(mid)] = h
        except (TypeError, ValueError):
            pass
        by_name[(h.get("team"), (h.get("name") or "").lower())] = h

    def hitter_heat(p: dict) -> bool:
        mid = p.get("id")
        h = None
        try:
            if mid is not None and int(mid) > 0:
                h = by_mlb.get(int(mid))
        except (TypeError, ValueError):
            pass
        if not h:
            h = by_name.get((p.get("team"), (p.get("name") or "").lower()))
        sid = (h or {}).get("synergy_id")
        if sid and sid in hhit and int(hhit[sid].get("located") or 0) > 0:
            return True
        return False

    coverage = {
        "selectable_pitchers": len(selectable),
        "movement_plots": len(with_plot),
        "need_movement": len(need_plot),
        "pitcher_heat": sum(1 for r in selectable if pitcher_heat(r)),
        "card_hitters": len(H),
        "card_pitchers": len(P),
        "card_h_full": filled(H, "hitter", ["k", "gb", "wh", "avg", "ops"]),
        "card_p_full": filled(P, "pitcher", ["k", "gb", "wh", "bb", "ops"]),
        "hitter_heat": sum(1 for p in H if hitter_heat(p)),
        "aa_below_mex_need_plot": len(mex_rows),
    }

    wb = Workbook()

    # --- Tab 1: AA/Mexico no-plot ---
    ws = wb.active
    ws.title = "AA below or Mexico"
    ws.append(
        [
            "Pitchers with no movement plot whose career is AA-and-below and/or Mexican League only (no AAA/MLB)",
        ]
    )
    ws.append([f"Count: {len(mex_rows)}"])
    ws.append([])
    ws.append(["Name", "PR team", "Last", "First", "Level", "Org", "Year"])
    for r in mex_rows:
        ws.append([r["name"], r["pr"], r["last"], r["first"], r["level"], r["org"], r["year"]])
    for cell in ws[4]:
        cell.font = Font(bold=True)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 8
    ws.column_dimensions["F"].width = 34
    ws.column_dimensions["G"].width = 8

    # --- Tab 2: Movement plots ---
    ws2 = wb.create_sheet("Movement plots")
    ws2.append(["Pitchers who still need a movement plot uploaded"])
    ws2.append(["Need manual upload", len(need_plot)])
    ws2.append(["Already have a plot", len(with_plot)])
    ws2.append([])
    ws2.append(["Player", "PR team", "Movement plot added"])
    for r in sorted(need_plot, key=lambda x: (x.get("team") or "", x.get("name") or "")):
        ws2.append([r.get("name"), TEAM_NAME.get(r.get("team") or "", r.get("team")), None])
    for cell in ws2[5]:
        cell.font = Font(bold=True)

    # --- Tab 3: Pitcher view ---
    # Only Rodriguez has a committed delivery image in the repo.
    have_picture = {"605446"}
    need_pic = [r for r in selectable if str(r.get("player_id")) not in have_picture]
    ws3 = wb.create_sheet("Pitcher view")
    ws3.append(["Pitcher-view / release pictures for pitchers on the advance sheet"])
    ws3.append(["Still need a picture", len(need_pic)])
    ws3.append(["Have a saved release picture", len(have_picture)])
    ws3.append([])
    ws3.append(["Player", "PR team", "Pitcher view added"])
    for r in sorted(need_pic, key=lambda x: (x.get("team") or "", x.get("name") or "")):
        mark = "✓" if str(r.get("player_id")) in have_picture else None
        ws3.append([r.get("name"), TEAM_NAME.get(r.get("team") or "", r.get("team")), mark])
    for cell in ws3[5]:
        cell.font = Font(bold=True)

    # --- Tab 4: Coverage ---
    ws4 = wb.create_sheet("Coverage")
    ws4.append(["Metric", "Count", "Total", "Note"])
    rows4 = [
        ("Pitcher advance pages (arsenal)", coverage["selectable_pitchers"], coverage["selectable_pitchers"], "Dropdown pitchers"),
        ("Movement plots populated", coverage["movement_plots"], coverage["selectable_pitchers"], "At least one pitch with IVB+HB"),
        ("Need movement plot upload", coverage["need_movement"], coverage["selectable_pitchers"], ""),
        ("Pitcher heat maps", coverage["pitcher_heat"], coverage["selectable_pitchers"], "Located pitches in advance_heatmaps"),
        ("Strategy-card hitters", coverage["card_hitters"], coverage["card_hitters"], ""),
        ("Strategy-card hitters fully populated", coverage["card_h_full"], coverage["card_hitters"], "K, GB, WH, AVG, OPS"),
        ("Hitter heat maps (card hitters)", coverage["hitter_heat"], coverage["card_hitters"], "Synergy heat key"),
        ("Strategy-card pitchers", coverage["card_pitchers"], coverage["card_pitchers"], ""),
        ("Strategy-card pitchers fully populated", coverage["card_p_full"], coverage["card_pitchers"], "K, GB, WH, BB, OPS"),
        ("AA-below / Mexico-only, no plot", coverage["aa_below_mex_need_plot"], coverage["need_movement"], "This workbook tab 1"),
    ]
    for row in rows4:
        ws4.append(list(row))
    for cell in ws4[1]:
        cell.font = Font(bold=True)
    ws4.column_dimensions["A"].width = 40
    ws4.column_dimensions["D"].width = 36

    wb.save(OUT)
    wb.save(DOWNLOADS)
    print(json.dumps(coverage, indent=2))
    print(f"wrote {OUT}")
    print(f"wrote {DOWNLOADS}")
    print("AA-below/Mexico rows:")
    for r in mex_rows:
        print(f"  {r['pr']:10} {r['last']}, {r['first']:16} {r['level']:4} {r['year']}  {r['org']}")


if __name__ == "__main__":
    main()

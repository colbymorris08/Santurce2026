#!/usr/bin/env python3
"""Daily LBPRC refresh from ligapr.com (boxscores + equipo season lines + roster sync).

Writes:
  data/bullpen_usage.json
  data/pitcher_one_page.json
  data/lbprc_2026_27_hitting.json
  data/lbprc_2026_27_pitching.json
  data/roster_change_alert.json
  updates winter_line / lbprc_line on advance packets when season stats exist
  syncs data/lbprc_2025_rosters.json when ligapr lists new/removed players

Cron target: 5:00 AM America/New_York (see .github/workflows/ligapr_daily.yml).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; SanturceAdvance/1.0; +https://github.com/colbymorris08/Santurce2026)",
    "Accept": "application/json,text/html,*/*",
    "X-Requested-With": "XMLHttpRequest",
}

# ligapr team_id → site abbrev / Synergy-style codes used across advance JSON
LIGAPR_TEAMS = {
    1: {"abbr": "CAG", "name": "Criollos de Caguas", "slug": "criollos-de-caguas", "codes": {"CRI", "CAG"}},
    2: {"abbr": "SAN", "name": "Cangrejeros de Santurce", "slug": "cangrejeros-de-santurce", "codes": {"CAN", "SAN"}},
    3: {"abbr": "SJU", "name": "Senadores de San Juan", "slug": "senadores-de-san-juan", "codes": {"SEN", "SJU", "SSJ"}},
    4: {"abbr": "CAR", "name": "Gigantes de Carolina", "slug": "gigantes-de-carolina", "codes": {"GIG", "CAR"}},
    5: {"abbr": "MAY", "name": "Indios de Mayagüez", "slug": "indios-de-mayaguez", "codes": {"IND", "MAY"}},
    6: {"abbr": "PON", "name": "Leones de Ponce", "slug": "leones-de-ponce", "codes": {"LEO", "PON"}},
}
ABBR_TO_ID = {v["abbr"]: k for k, v in LIGAPR_TEAMS.items()}
CODE_TO_ABBR = {}
for meta in LIGAPR_TEAMS.values():
    CODE_TO_ABBR[meta["abbr"]] = meta["abbr"]
    for c in meta["codes"]:
        CODE_TO_ABBR[c] = meta["abbr"]
NAME_TO_ABBR = {meta["name"].lower(): meta["abbr"] for meta in LIGAPR_TEAMS.values()}
NAME_TO_ABBR["indios de mayaguez"] = "MAY"
NAME_TO_ABBR["cangrejeros de santurce"] = "SAN"

# Prefer current winter; fall back only for scraper self-test
CURRENT_LEAGUE_ID = 18  # LBPRC 2026-2027
FALLBACK_LEAGUE_ID = 12  # LBPRC 2025-2026
SEASON_LABEL = "2026-27 LBPRC"
BULLPEN_GAME_LIMIT = 13

PITCH_COL_MAP = {
    "FB": ("FF", "FA", "SI", "FT", "4S"),
    "CB": ("CU", "KC"),
    "SL": ("SL", "ST", "SV"),
    "CH": ("CH",),
    "CT": ("FC",),
    "SPL": ("FS", "FO"),
}
PITCH_LABEL = {
    "FF": "FB",
    "FA": "FB",
    "SI": "SNK",
    "FT": "SNK",
    "4S": "FB",
    "CU": "CB",
    "KC": "CB",
    "SL": "SL",
    "ST": "SW",
    "SV": "SL",
    "CH": "CH",
    "FC": "CUT",
    "FS": "SPL",
    "FO": "SPL",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(name: str, default: Any = None) -> Any:
    path = DATA / name
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(name: str, value: Any, *, compact: bool = False) -> None:
    kwargs: dict[str, Any] = {"ensure_ascii": False}
    if compact:
        kwargs["separators"] = (",", ":")
    else:
        kwargs["indent"] = 2
    text = json.dumps(value, **kwargs)
    if not compact:
        text += "\n"
    (DATA / name).write_text(text)


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"\s*\([^)]*\)\s*", " ", value)
    value = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", value, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", value.lower())


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def http_json_safe(url: str) -> Any | None:
    try:
        return http_json(url)
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: {url} -> {exc}")
        return None


def parse_table(html: str) -> tuple[list[str] | None, list[list[str]]]:
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html or "", re.S)
    header = None
    out: list[list[str]] = []
    for row in rows:
        cells = [
            " ".join(unescape(re.sub(r"<[^>]+>", "", c)).split())
            for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)
        ]
        if not cells or not any(cells):
            continue
        joined = " ".join(cells).lower()
        if "muy pronto" in joined:
            continue
        if cells[0].lower() in {"nombre", "name"} or "ERA" in cells or "BA" in cells or "OBP" in cells:
            header = cells
            continue
        out.append(cells)
    return header, out


def team_abbr_from_name(name: str | None) -> str | None:
    if not name:
        return None
    key = " ".join(name.lower().split())
    if key in NAME_TO_ABBR:
        return NAME_TO_ABBR[key]
    for n, abbr in NAME_TO_ABBR.items():
        if n in key or key in n:
            return abbr
    return None


def resolve_league(prefer: int) -> dict[str, Any]:
    """Pick the active winter league that has usable boxscore/detail HTML."""
    meta = http_json_safe(f"https://ligapr.com/leagues/{prefer}/subleagues") or {}
    sub_id = None
    if meta.get("subleagues"):
        sub_id = meta["subleagues"][0]["id"]
    else:
        sub_id = prefer
    sample = http_json_safe(f"https://ligapr.com/subleagues/{sub_id}/getBoxScores")
    html = (sample or {}).get("html") or ""
    finished = html.count("Finalizado")
    expandable = html.count('class="btn-bs bs"')
    info = {
        "league_id": prefer,
        "subleague_id": sub_id,
        "finished_games": finished,
        "expandable": expandable,
        "label": SEASON_LABEL if prefer == CURRENT_LEAGUE_ID else f"LBPRC league {prefer}",
    }
    if finished or prefer != CURRENT_LEAGUE_ID:
        return info
    # No finished 2026-27 games yet — still bind to current league (empty grids OK)
    return info


def fetch_equipo_stats(league_id: int) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Return {abbr: {hitters: [...], pitchers: [...]}} from /estadisticas/equipo."""
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for tid, meta in LIGAPR_TEAMS.items():
        abbr = meta["abbr"]
        data = http_json_safe(
            f"https://ligapr.com/estadisticas/equipo?league_id={league_id}&team_id={tid}"
        )
        if not data:
            out[abbr] = {"hitters": [], "pitchers": []}
            continue
        _, bats = parse_table(data.get("htmlBatting") or "")
        _, pits = parse_table(data.get("htmlPitching") or "")
        hitters = []
        for cells in bats:
            if not cells:
                continue
            # Nombre Equipo BA AB R H 2B 3B HR RBI TB BB SO OBP SLG OPS
            row = {
                "name": cells[0],
                "team": abbr,
                "ba": cells[2] if len(cells) > 2 else None,
                "ab": _toi(cells[3]) if len(cells) > 3 else None,
                "r": _toi(cells[4]) if len(cells) > 4 else None,
                "h": _toi(cells[5]) if len(cells) > 5 else None,
                "doubles": _toi(cells[6]) if len(cells) > 6 else None,
                "triples": _toi(cells[7]) if len(cells) > 7 else None,
                "hr": _toi(cells[8]) if len(cells) > 8 else None,
                "rbi": _toi(cells[9]) if len(cells) > 9 else None,
                "bb": _toi(cells[11]) if len(cells) > 11 else None,
                "so": _toi(cells[12]) if len(cells) > 12 else None,
                "obp": cells[13] if len(cells) > 13 else None,
                "slg": cells[14] if len(cells) > 14 else None,
                "ops": cells[15] if len(cells) > 15 else None,
            }
            ab = row["ab"] or 0
            h = row["h"] or 0
            bb = row["bb"] or 0
            row["pa"] = ab + bb  # ligapr table lacks SF/HBP; PA ≈ AB+BB
            hitters.append(row)
        pitchers = []
        for cells in pits:
            if not cells:
                continue
            # Nombre Equipo ERA IP W L HA RA SHO SV ER HRA HBP TBB K
            ip = cells[3] if len(cells) > 3 else None
            k = _toi(cells[14]) if len(cells) > 14 else None
            bb = _toi(cells[13]) if len(cells) > 13 else None
            h = _toi(cells[6]) if len(cells) > 6 else None
            hr = _toi(cells[11]) if len(cells) > 11 else None
            er = _toi(cells[10]) if len(cells) > 10 else None
            w = _toi(cells[4]) if len(cells) > 4 else None
            l = _toi(cells[5]) if len(cells) > 5 else None
            sv = _toi(cells[9]) if len(cells) > 9 else None
            ip_outs = ip_to_outs(ip)
            ip_f = outs_to_ip_float(ip_outs) if ip_outs else None
            pitchers.append(
                {
                    "name": cells[0],
                    "team": abbr,
                    "era": cells[2] if len(cells) > 2 else None,
                    "ip": ip,
                    "g": None,
                    "gs": None,
                    "w": w,
                    "l": l,
                    "sv": sv,
                    "h": h,
                    "hr": hr,
                    "er": er,
                    "bb": bb,
                    "k": k,
                    "k9": round(k * 9 / ip_f, 1) if k is not None and ip_f else None,
                    "bb9": round(bb * 9 / ip_f, 1) if bb is not None and ip_f else None,
                    "whip": round(((bb or 0) + (h or 0)) / ip_f, 2) if ip_f else None,
                }
            )
        out[abbr] = {"hitters": hitters, "pitchers": pitchers}
        print(f"  equipo {abbr}: {len(hitters)} H / {len(pitchers)} P")
    return out


def _toi(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _tof(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def ip_to_outs(ip: Any) -> int | None:
    """ligapr publishes decimal IP (1.33≈1.1, 0.67≈0.2); also accepts baseball 1.1."""
    if ip is None or ip == "":
        return None
    s = str(ip).strip()
    if not s:
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    # Baseball thirds notation: frac digit is 0/1/2 outs
    if "." in s:
        whole_s, frac_s = s.split(".", 1)
        if frac_s.isdigit() and len(frac_s) == 1 and int(frac_s) in (0, 1, 2):
            return int(whole_s) * 3 + int(frac_s)
    return int(round(val * 3))


def outs_to_ip_float(outs: int) -> float:
    return outs / 3.0


def format_ip(ip: Any) -> str:
    outs = ip_to_outs(ip)
    if outs is None:
        return ""
    whole, rem = divmod(outs, 3)
    if rem == 0:
        return str(whole)
    return f"{whole}.{rem}"


def parse_boxscore_games(html: str, focus_abbr: str) -> list[dict[str, Any]]:
    """Parse expandable boxscores; keep games involving focus_abbr, newest first."""
    games: list[dict[str, Any]] = []
    for m in re.finditer(r"<h6>Boxscore (.*?)</h6>(.*?)(?=<h6>Boxscore |\Z)", html or "", re.S):
        title = m.group(1).strip()
        body = m.group(2)
        score_m = re.search(
            r"Finalizado</div>\s*<span>(\d+)</span>\s*-\s*<span>(\d+)</span>", body
        )
        if not score_m:
            continue
        left_score, right_score = int(score_m.group(1)), int(score_m.group(2))
        lm = re.search(r'class="last-match"(.*?)(?:btn-bs|$)', body, re.S)
        left = right = None
        if lm:
            names = re.findall(r'class="name">([^<]+)', lm.group(1))
            if len(names) >= 2:
                left, right = names[0].strip(), names[1].strip()
        left_abbr = team_abbr_from_name(left)
        right_abbr = team_abbr_from_name(right)
        if focus_abbr not in {left_abbr, right_abbr}:
            # title codes as backup (e.g. IND - LEO 572)
            codes = re.findall(r"[A-Z]{3}", title.upper())
            abbrs = {CODE_TO_ABBR.get(c) for c in codes}
            if focus_abbr not in abbrs:
                continue
        bs_id_m = re.search(r'data-id="(bs-\d+)"', body)
        detail = ""
        if bs_id_m:
            did = bs_id_m.group(1)
            dm = re.search(
                rf'<div id="{re.escape(did)}"[^>]*>(.*?)(?=<div class="col-md-12 wow|$)',
                html,
                re.S,
            )
            if dm:
                detail = dm.group(1)
        pitchers_by_team = parse_pitching_detail(detail)
        # Map team order in detail to left/right by matching first pitcher table
        # to batting header order (home/away as listed: left then right in card,
        # but detail lists left team first typically).
        team_order = []
        for hdr in re.findall(
            r"header-t[^>]*>[\s\S]*?</svg>\s*([^<]+?)\s*<", detail
        ):
            name = " ".join(unescape(hdr).split())
            abbr = team_abbr_from_name(name)
            if abbr and abbr not in team_order:
                team_order.append(abbr)
        if not team_order:
            team_order = [a for a in (left_abbr, right_abbr) if a]

        by_abbr: dict[str, list[dict[str, Any]]] = {}
        for idx, plist in enumerate(pitchers_by_team):
            abbr = team_order[idx] if idx < len(team_order) else None
            if abbr:
                by_abbr[abbr] = plist

        game_no_m = re.search(r"(\d+)\s*$", title)
        games.append(
            {
                "title": title,
                "game_id": int(game_no_m.group(1)) if game_no_m else None,
                "left": left,
                "right": right,
                "left_abbr": left_abbr,
                "right_abbr": right_abbr,
                "left_score": left_score,
                "right_score": right_score,
                "pitchers": by_abbr,
            }
        )
    return games


def parse_pitching_detail(detail_html: str) -> list[list[dict[str, Any]]]:
    parts = (detail_html or "").split("Lanzadores")
    teams: list[list[dict[str, Any]]] = []
    for part in parts[1:]:
        rows = re.findall(r"<tr>(.*?)</tr>", part, re.S)
        plist: list[dict[str, Any]] = []
        started = False
        for row in rows:
            cells = [
                " ".join(unescape(re.sub(r"<[^>]+>", "", c)).split())
                for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)
            ]
            if not cells:
                continue
            if cells[0] in {"IP", "H"} or (len(cells) > 1 and cells[1] == "IP"):
                started = True
                continue
            if not started:
                # header row with Lanzadores already split away; first data row
                if re.search(r"\d", " ".join(cells[1:2])):
                    started = True
                else:
                    continue
            if cells[0] in {"Bateadores", "AB"} or "Bateadores" in cells[0]:
                break
            if len(cells) < 2:
                continue
            raw_name = cells[0]
            role = None
            rm = re.search(r"\(([^)]*)\)\s*$", raw_name)
            if rm:
                role = rm.group(1).strip()
            # Strip trailing decision and lone P/G role markers ("ORTIZ P", "VALLIMONT G")
            name = re.sub(r"\s*\([^)]*\)\s*$", "", raw_name).strip()
            name = re.sub(r"\s+[PG]\s*$", "", name).strip()
            ip = cells[1]
            plist.append(
                {
                    "name": name,
                    "ip": format_ip(ip),
                    "pitches": None,  # not published on ligapr boxscores
                    "role": role,
                    "starter": False,
                }
            )
        # Boxscore order: first listed arm is the starter
        if plist:
            plist[0]["starter"] = True
            teams.append(plist)
    return teams


def build_bullpen_for_team(abbr: str, games: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape Ponce-PDF style bullpen usage for one team (most recent N games)."""
    recent = games[:BULLPEN_GAME_LIMIT]
    columns = []
    for g in recent:
        focus_home = g.get("right_abbr") == abbr
        focus_score = g["right_score"] if focus_home else g["left_score"]
        opp_score = g["left_score"] if focus_home else g["right_score"]
        opp = g["left_abbr"] if focus_home else g["right_abbr"]
        won = focus_score > opp_score
        starter_name = ""
        starter_ip = ""
        for p in g.get("pitchers", {}).get(abbr, []):
            if p.get("starter"):
                starter_name = p["name"]
                starter_ip = p["ip"]
                break
        if not starter_name:
            plist = g.get("pitchers", {}).get(abbr, [])
            if plist:
                starter_name = plist[0]["name"]
                starter_ip = plist[0]["ip"]
        columns.append(
            {
                "game_id": g.get("game_id"),
                "title": g.get("title"),
                "label": _short_game_label(g, abbr),
                "result": f"{abbr} {focus_score}-{opp_score}" if won else f"{opp or '?'} {opp_score}-{focus_score}",
                "opp": opp,
                "score_for": focus_score,
                "score_against": opp_score,
                "won": won,
                "starter": starter_name,
                "starter_ip": starter_ip,
            }
        )

    # Reliever rows: anyone who pitched for focus team in window, excluding primary starters-only? PDF includes all bullpen arms; starters also appear if they relieved. Include all non-starter appearances + arms who only relieved.
    usage: dict[str, dict[str, Any]] = {}
    for gi, g in enumerate(recent):
        for p in g.get("pitchers", {}).get(abbr, []):
            if p.get("starter"):
                continue
            key = norm_name(p["name"])
            slot = usage.setdefault(
                key,
                {"name": p["name"], "last_app": None, "last_app_game_id": None, "cells": [None] * len(recent)},
            )
            cell = f"{p['ip']} IP" if p.get("ip") else ""
            if p.get("pitches") is not None:
                cell = f"{p['ip']} IP / {p['pitches']}"
            slot["cells"][gi] = cell or None
            if slot["last_app"] is None:
                slot["last_app"] = columns[gi]["label"] if gi < len(columns) else None
                slot["last_app_game_id"] = g.get("game_id")

    # Sort by most recent appearance then name
    relievers = sorted(
        usage.values(),
        key=lambda r: (
            -int(r["last_app_game_id"] or 0),
            r["name"].split()[-1].lower(),
            r["name"].lower(),
        ),
    )
    return {
        "team": abbr,
        "team_name": next(v["name"] for v in LIGAPR_TEAMS.values() if v["abbr"] == abbr),
        "games": columns,
        "relievers": relievers,
        "notes": [],
    }


def _short_game_label(g: dict[str, Any], focus: str) -> str:
    gid = g.get("game_id")
    opp = g["left_abbr"] if g.get("right_abbr") == focus else g.get("right_abbr")
    if gid and opp:
        return f"{opp} #{gid}"
    if gid:
        return f"G{gid}"
    return g.get("title") or "?"


def build_bullpen_usage(league_id: int, sub_id: int) -> dict[str, Any]:
    teams_out = {}
    for tid, meta in LIGAPR_TEAMS.items():
        abbr = meta["abbr"]
        data = http_json_safe(f"https://ligapr.com/subleagues/{sub_id}/{tid}/getBoxScores")
        html = (data or {}).get("html") or ""
        games = parse_boxscore_games(html, abbr)
        print(f"  boxscores {abbr}: {len(games)} finished")
        teams_out[abbr] = build_bullpen_for_team(abbr, games)
    return {
        "updated": now_iso(),
        "source": "https://ligapr.com/estadisticas/boxscores",
        "league_id": league_id,
        "subleague_id": sub_id,
        "season": SEASON_LABEL,
        "game_window": BULLPEN_GAME_LIMIT,
        "note": (
            "Cells are IP from ligapr boxscore pitching lines. "
            "Pitch counts are not published on ligapr; shown as IP only."
        ),
        "teams": teams_out,
    }


def velo_band(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    lo = entry.get("velo_p25")
    hi = entry.get("velo_p75")
    v = entry.get("velo")
    if lo is not None and hi is not None:
        return f"{int(round(lo))}-{int(round(hi))}"
    if v is not None:
        iv = int(round(float(v)))
        return f"{iv}-{iv}"
    return ""


def pick_pitch(arsenal: list[dict[str, Any]], codes: tuple[str, ...]) -> dict[str, Any] | None:
    by = {str(a.get("type") or a.get("code") or a.get("name") or "").upper(): a for a in arsenal}
    best = None
    for c in codes:
        a = by.get(c)
        if not a:
            continue
        if best is None or float(a.get("pitches") or 0) > float(best.get("pitches") or 0):
            best = a
    return best


def freq_line(pitches: list[dict[str, Any]] | None, limit: int = 4) -> str:
    if not pitches:
        return ""
    parts = []
    for p in pitches[:limit]:
        code = str(p.get("type") or "").upper()
        label = PITCH_LABEL.get(code, code)
        usage = p.get("usage")
        if usage is None:
            continue
        parts.append(f"{label} {float(usage):.0f}%")
    return ", ".join(parts)


def build_pitcher_one_page() -> dict[str, Any]:
    rosters = load_json("lbprc_2025_rosters.json", {}) or {}
    arsenals = {
        norm_name(p.get("name") or ""): p
        for p in (load_json("pregame_pitcher_arsenals.json", {}) or {}).get("pitchers", [])
    }
    advance = {
        norm_name(p.get("name") or ""): p
        for p in (load_json("advance_opposing_pitchers.json", {}) or {}).get("pitchers", [])
    }
    notes_file = load_json("pitcher_one_page_notes.json", {}) or {}
    notes_map = notes_file.get("notes") if isinstance(notes_file, dict) else {}
    if not isinstance(notes_map, dict):
        notes_map = {}

    teams: dict[str, Any] = {}
    for team in rosters.values():
        abbr = team.get("teamAbbrev")
        if not abbr:
            continue
        rows = []
        for player in team.get("players") or []:
            if str(player.get("position") or "").upper() not in {"P", "RHP", "LHP", "RHR", "LHR", "RHS", "LHS"}:
                continue
            name = player.get("name") or ""
            key = norm_name(name)
            ar = arsenals.get(key) or {}
            adv = advance.get(key) or {}
            arsenal = ar.get("arsenal") or []
            throws = ar.get("throws") or adv.get("throws") or ""
            velo = {}
            for col, codes in PITCH_COL_MAP.items():
                band = velo_band(pick_pitch(arsenal, codes))
                # Prefer SI band annotation in FB cell when sinker is primary FB family
                if col == "FB":
                    si = pick_pitch(arsenal, ("SI", "FT"))
                    ff = pick_pitch(arsenal, ("FF", "FA", "4S"))
                    if si and (not ff or float(si.get("pitches") or 0) >= float(ff.get("pitches") or 0) * 0.6):
                        sb = velo_band(si)
                        fb = velo_band(ff) if ff else ""
                        if sb and fb and sb != fb:
                            band = f"{fb}\nSNK {sb}" if fb else f"SNK {sb}"
                        elif sb:
                            band = f"{sb}\nSNK"
                velo[col] = band
            platoon = adv.get("platoon") or {}
            vs_r = freq_line(platoon.get("vs_RHB"))
            vs_l = freq_line(platoon.get("vs_LHB"))
            note = notes_map.get(key) or notes_map.get(name) or ""
            l_mark = " (l)" if str(throws).upper().startswith("L") else ""
            rows.append(
                {
                    "jersey": player.get("jersey") or "",
                    "name": name,
                    "name_display": f"{_last_first(name)}{l_mark}",
                    "throws": throws,
                    "mlb_id": player.get("id"),
                    "velo": velo,
                    "freq_r": vs_r,
                    "freq_l": vs_l,
                    "freq_html": _freq_html(vs_r, vs_l),
                    "notes": note,
                    "needs_data": not bool(arsenal),
                }
            )
        rows.sort(key=lambda r: (r["name"].split()[-1].lower(), r["name"].lower()))
        teams[abbr] = {
            "team": abbr,
            "team_name": team.get("teamName"),
            "pitchers": rows,
        }
        print(f"  one-pager {abbr}: {len(rows)} pitchers")

    return {
        "updated": now_iso(),
        "season": SEASON_LABEL,
        "columns": ["FB", "CB", "SL", "CH", "CT", "SPL"],
        "note": (
            "Velo bands from pregame arsenals (p25–p75 when tracked). "
            "Pitch frequency from Synergy platoon splits on advance packets. "
            "Scout notes: edit data/pitcher_one_page_notes.json (keyed by normalized name)."
        ),
        "teams": teams,
    }


def _last_first(name: str) -> str:
    parts = [p for p in str(name).split() if p]
    if len(parts) < 2:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def _freq_html(vs_r: str, vs_l: str) -> str:
    bits = []
    if vs_r:
        bits.append(f'<span class="freq-r">RHH: {vs_r}</span>')
    if vs_l:
        bits.append(f'<span class="freq-l">LHH: {vs_l}</span>')
    return "<br>".join(bits)


def roster_snapshot(rosters: dict) -> dict[str, list[str]]:
    snap = {}
    for team in rosters.values():
        abbr = team.get("teamAbbrev")
        if not abbr:
            continue
        names = sorted(norm_name(p.get("name") or "") for p in team.get("players") or [])
        snap[abbr] = names
    return snap


def sync_rosters_from_equipo(
    equipo: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """When ligapr equipo lists are non-empty, merge into lbprc_2025_rosters.json."""
    rosters = load_json("lbprc_2025_rosters.json", {}) or {}
    before = roster_snapshot(rosters)
    added: list[dict[str, str]] = []
    removed: list[dict[str, str]] = []
    any_players = any(
        equipo.get(abbr, {}).get("hitters") or equipo.get(abbr, {}).get("pitchers")
        for abbr in ABBR_TO_ID
    )
    alert = {
        "updated": now_iso(),
        "season": SEASON_LABEL,
        "source": "https://ligapr.com/estadisticas/equipos",
        "changed": False,
        "ligapr_stats_ready": any_players,
        "added": added,
        "removed": removed,
        "message": "",
        "action_needed": [],
    }
    if not any_players and not force:
        alert["message"] = (
            "LBPRC 2026-27 equipo bateo/lanzadores not published yet on ligapr — "
            "kept existing workbook rosters. Will sync automatically when stats appear."
        )
        write_json("roster_change_alert.json", alert)
        return alert

    # Index existing by team + norm name
    by_team: dict[str, dict[str, dict]] = {}
    team_meta_by_abbr = {}
    for tid, team in rosters.items():
        abbr = team.get("teamAbbrev")
        team_meta_by_abbr[abbr] = (str(tid), team)
        by_team[abbr] = {norm_name(p.get("name") or ""): p for p in team.get("players") or []}

    for abbr, buckets in equipo.items():
        existing = by_team.setdefault(abbr, {})
        seen = set()
        for role, rows in (("P", buckets.get("pitchers") or []), ("H", buckets.get("hitters") or [])):
            for row in rows:
                key = norm_name(row["name"])
                if not key:
                    continue
                seen.add(key)
                if key in existing:
                    # Ensure pitcher flag if appearing on pitching table
                    if role == "P" and str(existing[key].get("position") or "").upper() != "P":
                        existing[key]["position"] = "P"
                    continue
                # New player from ligapr
                stub_id = -abs(int(hashlib.md5(f"{abbr}:{key}".encode()).hexdigest()[:8], 16))
                existing[key] = {
                    "id": stub_id,
                    "name": row["name"],
                    "position": "P" if role == "P" else "IF",
                    "jersey": "",
                    "status": "Active",
                    "source": "ligapr_equipo_sync",
                }
                added.append({"team": abbr, "name": row["name"], "role": role})
        # Removals: only drop players previously tagged from ligapr sync, or when force
        if force:
            for key, player in list(existing.items()):
                if key not in seen:
                    removed.append({"team": abbr, "name": player.get("name") or key, "role": player.get("position")})
                    del existing[key]

    # Write back
    for abbr, players_map in by_team.items():
        if abbr not in team_meta_by_abbr:
            continue
        tid, team = team_meta_by_abbr[abbr]
        team["players"] = list(players_map.values())
        rosters[tid] = team

    after = roster_snapshot(rosters)
    alert["changed"] = before != after or bool(added) or bool(removed)
    if alert["changed"]:
        write_json("lbprc_2025_rosters.json", rosters)
        alert["message"] = (
            f"Roster sync applied from ligapr equipos: +{len(added)} / -{len(removed)}."
        )
        alert["action_needed"] = [
            "Populate Synergy / advance packets for new arms and bats",
            "Add pitcher release (delivery) pictures",
            "Confirm movement charts / arsenal XY uploads",
            "Fill pitcher one-page scout notes where blank",
        ]
    else:
        alert["message"] = "ligapr equipo lists checked — no roster membership changes."
    write_json("roster_change_alert.json", alert)
    return alert


def attach_winter_lines(equipo: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
    """Attach lbprc_line / winter_line onto advance opposing packets."""
    h_by = {}
    p_by = {}
    for abbr, buckets in equipo.items():
        for row in buckets.get("hitters") or []:
            h_by[(abbr, norm_name(row["name"]))] = row
            h_by[norm_name(row["name"])] = row
        for row in buckets.get("pitchers") or []:
            p_by[(abbr, norm_name(row["name"]))] = row
            p_by[norm_name(row["name"])] = row

    # Standalone season files
    write_json(
        "lbprc_2026_27_hitting.json",
        {
            "updated": now_iso(),
            "season": SEASON_LABEL,
            "source": "https://ligapr.com/estadisticas/equipos",
            "teams": {a: b["hitters"] for a, b in equipo.items()},
        },
    )
    write_json(
        "lbprc_2026_27_pitching.json",
        {
            "updated": now_iso(),
            "season": SEASON_LABEL,
            "source": "https://ligapr.com/estadisticas/equipos",
            "teams": {a: b["pitchers"] for a, b in equipo.items()},
        },
    )

    hitters_doc = load_json("advance_opposing_hitters.json")
    if isinstance(hitters_doc, dict) and hitters_doc.get("hitters"):
        n = 0
        for h in hitters_doc["hitters"]:
            key = norm_name(h.get("name") or "")
            row = h_by.get((h.get("team"), key)) or h_by.get(key)
            if not row:
                h["lbprc_line"] = h.get("lbprc_line")  # keep prior
                continue
            line = {
                "team": row["team"],
                "team_name": next(
                    (m["name"] for m in LIGAPR_TEAMS.values() if m["abbr"] == row["team"]),
                    row["team"],
                ),
                "season": SEASON_LABEL,
                "league": "LBPRC",
                "league_name": "Liga de Béisbol Profesional Roberto Clemente",
                "level": "LBPRC",
                "pa": row.get("pa"),
                "ab": row.get("ab"),
                "r": row.get("r"),
                "h": row.get("h"),
                "doubles": row.get("doubles"),
                "triples": row.get("triples"),
                "hr": row.get("hr"),
                "rbi": row.get("rbi"),
                "sb": None,
                "cs": None,
                "bb": row.get("bb"),
                "so": row.get("so"),
                "ba": row.get("ba"),
                "obp": row.get("obp"),
                "slg": row.get("slg"),
                "ops": row.get("ops"),
                "source": "ligapr.com estadisticas/equipos bateo",
            }
            h["lbprc_line"] = line
            h["winter_line"] = line
            n += 1
        hitters_doc["lbprc_line_updated"] = now_iso()
        write_json("advance_opposing_hitters.json", hitters_doc, compact=True)
        print(f"  attached winter lines to {n} hitters")

    pitchers_doc = load_json("advance_opposing_pitchers.json")
    if isinstance(pitchers_doc, dict) and pitchers_doc.get("pitchers"):
        n = 0
        for p in pitchers_doc["pitchers"]:
            key = norm_name(p.get("name") or "")
            row = p_by.get((p.get("team"), key)) or p_by.get(key)
            if not row:
                continue
            line = {
                "team": row["team"],
                "team_name": next(
                    (m["name"] for m in LIGAPR_TEAMS.values() if m["abbr"] == row["team"]),
                    row["team"],
                ),
                "season": SEASON_LABEL,
                "league": "LBPRC",
                "league_name": "Liga de Béisbol Profesional Roberto Clemente",
                "level": "LBPRC",
                "ip": row.get("ip"),
                "g": row.get("g"),
                "gs": row.get("gs"),
                "k": row.get("k"),
                "bb": row.get("bb"),
                "k9": row.get("k9"),
                "bb9": row.get("bb9"),
                "era": row.get("era"),
                "fip": None,
                "whip": row.get("whip"),
                "h": row.get("h"),
                "hr": row.get("hr"),
                "sb_allowed": None,
                "cs_allowed": None,
                "source": "ligapr.com estadisticas/equipos lanzadores",
            }
            p["lbprc_line"] = line
            p["winter_line"] = line
            n += 1
        pitchers_doc["lbprc_line_updated"] = now_iso()
        write_json("advance_opposing_pitchers.json", pitchers_doc, compact=True)
        print(f"  attached winter lines to {n} pitchers")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", type=int, default=CURRENT_LEAGUE_ID)
    ap.add_argument(
        "--demo-prior-season",
        action="store_true",
        help="Use 2025-26 finished boxscores to populate bullpen demo grids",
    )
    args = ap.parse_args()

    print("ligapr daily build", now_iso())
    info = resolve_league(args.league if not args.demo_prior_season else FALLBACK_LEAGUE_ID)
    print("league", info)

    print("fetching equipo season lines…")
    # Always try CURRENT season for winter lines / roster
    equipo_current = fetch_equipo_stats(CURRENT_LEAGUE_ID)

    # Bullpen: prefer current winter; if no finished games yet, auto-fill from prior season
    bullpen_league = info["league_id"]
    bullpen_sub = info["subleague_id"]
    bullpen_demo = False
    if args.demo_prior_season or (
        info["league_id"] == CURRENT_LEAGUE_ID and int(info.get("finished_games") or 0) == 0
    ):
        prior = resolve_league(FALLBACK_LEAGUE_ID)
        if int(prior.get("finished_games") or 0) > 0:
            bullpen_league = prior["league_id"]
            bullpen_sub = prior["subleague_id"]
            bullpen_demo = True
            print("bullpen fallback → prior season", prior)

    print("building bullpen usage…")
    bullpen = build_bullpen_usage(bullpen_league, bullpen_sub)
    if bullpen_demo:
        bullpen["demo"] = True
        bullpen["season_display"] = "2025-26 LBPRC (placeholder until 2026-27 games)"
        bullpen["note"] = (
            (bullpen.get("note") or "")
            + " Placeholder: 2025-26 finished boxscores until 2026-27 games post on ligapr."
        )
    else:
        bullpen["season_display"] = bullpen.get("season")
    write_json("bullpen_usage.json", bullpen)

    print("building pitcher one-pagers…")
    one_page = build_pitcher_one_page()
    write_json("pitcher_one_page.json", one_page)

    print("roster sync + winter lines…")
    # Prefer current-season equipo lists for roster/winter; if empty and demo, still attach prior for visibility? No — winter line is 2026-27 only.
    alert = sync_rosters_from_equipo(equipo_current)
    attach_winter_lines(equipo_current)

    # If current season empty and demo requested, leave winter blank but keep alert honest
    summary = {
        "updated": now_iso(),
        "league": info,
        "bullpen_teams": {k: len(v.get("games") or []) for k, v in bullpen.get("teams", {}).items()},
        "roster_alert": {
            "changed": alert.get("changed"),
            "added": len(alert.get("added") or []),
            "removed": len(alert.get("removed") or []),
            "message": alert.get("message"),
        },
        "one_page_teams": {k: len(v.get("pitchers") or []) for k, v in one_page.get("teams", {}).items()},
    }
    write_json("ligapr_daily_summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compact roster + rate cache for the strategy and small-ball pregame cards.

Rosters stay on the current LBPRC teams in lbprc_2025_rosters.json (PR players
are not moved). Counting stats: LBPRC 2025 hitting/pitching. 2026 summer
SB/CS/sac bunts: MLB + MiLB + LMB season files. Platoon whiff/AVG/OPS and bunt
situation counts: Synergy advance hitters when the player is in that file.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = DATA / "pregame_card_stats.json"

TEAMS = [
    {"abbrev": "SAN", "name": "Cangrejeros de Santurce", "color": "#8B0000", "logo": "assets/santurce-logo.svg"},
    {"abbrev": "CAG", "name": "Criollos de Caguas", "color": "#0033A0", "logo": None},
    {"abbrev": "CAR", "name": "Gigantes de Carolina", "color": "#0B3D91", "logo": "assets/gigantes-logo.svg"},
    {"abbrev": "MAY", "name": "Indios de Mayagüez", "color": "#0E6B3C", "logo": None},
    {"abbrev": "PON", "name": "Leones de Ponce", "color": "#9E1B32", "logo": None},
    {"abbrev": "SJU", "name": "Senadores de San Juan", "color": "#1C2833", "logo": "assets/sanjuan-logo.svg"},
]


def load(name: str):
    path = DATA / name
    if not path.exists():
        return None
    return json.loads(path.read_text())


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in {".---", "-.--", "—", "-", "None"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pct(numer, denom):
    if not denom:
        return None
    return round(100.0 * numer / denom, 1)


def last_name(full: str) -> str:
    parts = [p for p in re.split(r"\s+", (full or "").strip()) if p]
    if not parts:
        return ""
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}
    while parts and parts[-1].lower().rstrip(".") in suffixes:
        parts.pop()
    if not parts:
        return ""
    raw = parts[-1]
    # "De Los Santos" style: keep the last two if the previous is a particle
    particles = {"de", "del", "la", "los", "las", "da", "dos", "van", "von"}
    if len(parts) >= 2 and parts[-2].lower() in particles:
        raw = parts[-2] + " " + parts[-1]
    if len(parts) >= 3 and parts[-3].lower() in particles and parts[-2].lower() in particles:
        raw = " ".join(parts[-3:])
    nk = unicodedata.normalize("NFKD", raw)
    nk = "".join(c for c in nk if not unicodedata.combining(c))
    return nk.upper()


def agg_pitch_rows(rows: list | None) -> dict | None:
    if not rows:
        return None
    pa = ab = h = swings = whiffs = bunts = 0
    ops_w = 0.0
    for r in rows:
        p = int(r.get("pa") or 0)
        pa += p
        ab += int(r.get("ab") or 0)
        h += int(r.get("h") or 0)
        swings += int(r.get("swings") or 0)
        whiffs += int(r.get("whiffs") or 0)
        bunts += int(r.get("bunts") or 0)
        if r.get("ops") is not None and p:
            ops_w += float(r["ops"]) * p
    if pa < 1 and swings < 1 and bunts < 1:
        return None
    return {
        "pa": pa,
        "ab": ab,
        "h": h,
        "wh": round(100.0 * whiffs / swings, 1) if swings else None,
        "avg": round(h / ab, 3) if ab else None,
        "ops": round(ops_w / pa, 3) if pa else None,
        "bunts": bunts,
    }


def summer_by_id() -> dict[int, dict]:
    agg: dict[int, dict] = defaultdict(lambda: {"sb": 0, "cs": 0, "sac": 0, "pa": 0, "sources": []})
    for fname, label in (
        ("mlb_2026_hitting.json", "MLB"),
        ("milb_2026_hitting.json", "MiLB"),
        ("mexico_2026_hitting.json", "LMB"),
    ):
        rows = load(fname) or []
        if not isinstance(rows, list):
            continue
        for r in rows:
            pid = r.get("playerId")
            if not pid:
                continue
            pid = int(pid)
            a = agg[pid]
            a["sb"] += int(r.get("stolenBases") or 0)
            a["cs"] += int(r.get("caughtStealing") or 0)
            a["sac"] += int(r.get("sacBunts") or 0)
            a["pa"] += int(num(r.get("plateAppearances")) or 0)
            if label not in a["sources"]:
                a["sources"].append(label)
    return {pid: row for pid, row in agg.items() if row["sb"] or row["cs"] or row["sac"] or row["pa"]}


def main() -> None:
    rosters = load("lbprc_2025_rosters.json") or {}
    hitting = load("lbprc_2025_hitting.json") or []
    pitching = load("lbprc_2025_pitching.json") or []
    advance = load("advance_opposing_hitters.json") or {}
    arsenals = (load("pregame_pitcher_arsenals.json") or {}).get("pitchers") or []

    hit_by = {}
    for r in hitting:
        pid = int(r.get("playerId") or 0)
        if pid:
            hit_by[pid] = r
    pit_by = {}
    for r in pitching:
        pid = int(r.get("playerId") or 0)
        if pid:
            pit_by[pid] = r

    adv_by = {}
    for h in advance.get("hitters") or []:
        mid = h.get("mlb_id")
        if mid:
            adv_by[int(mid)] = h

    arsenal_by = {}
    for p in arsenals:
        pid = p.get("player_id")
        if pid:
            arsenal_by[int(pid)] = p

    summer = summer_by_id()
    players = []
    seen = set()

    for team in rosters.values():
        abbrev = team.get("teamAbbrev")
        for p in team.get("players") or []:
            pid = int(p.get("id") or 0)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            name = p.get("name") or ""
            roster_pos = (p.get("position") or "").upper()
            hit = hit_by.get(pid)
            pit = pit_by.get(pid)
            adv = adv_by.get(pid)
            arm = arsenal_by.get(pid)
            su = summer.get(pid)

            role = "P" if roster_pos == "P" else "H"
            bats = (adv or {}).get("bats") or None
            throws = (arm or {}).get("throws") or None

            hitter = None
            if role == "H" or hit:
                pa = int(num(hit.get("plateAppearances")) or 0) if hit else 0
                so = int(num(hit.get("strikeOuts")) or 0) if hit else 0
                go = int(num(hit.get("groundOuts")) or 0) if hit else 0
                ao = int(num(hit.get("airOuts")) or 0) if hit else 0
                sb = int(num(hit.get("stolenBases")) or 0) if hit else 0
                cs = int(num(hit.get("caughtStealing")) or 0) if hit else 0
                sac = int(num(hit.get("sacBunts")) or 0) if hit else 0
                overall = (adv or {}).get("overall") or {}
                vs_l = agg_pitch_rows((adv or {}).get("vs_l_by_pitch_type"))
                vs_r = agg_pitch_rows((adv or {}).get("vs_r_by_pitch_type"))
                risp = agg_pitch_rows((adv or {}).get("risp_by_pitch_type"))
                # A real 2B/3B split only exists if Synergy recorded any steal.
                sb2 = int(overall.get("sb_from_1b") or 0)
                sb3 = int(overall.get("sb_from_2b") or 0)
                wh = None
                if overall.get("swings"):
                    wh = round(100.0 * int(overall.get("whiffs") or 0) / int(overall["swings"]), 1)
                hitter = {
                    "pa": pa,
                    "k": pct(so, pa) if hit else None,
                    "gb": pct(go, go + ao) if hit and (go + ao) else None,
                    "avg": num(hit.get("avg")) if hit else None,
                    "ops": num(hit.get("ops")) if hit else None,
                    "wh": wh,
                    "sb": sb if hit else None,
                    "cs": cs if hit else None,
                    "sac": sac if hit else None,
                    "summer_sb": int(su["sb"]) if su else 0,
                    "summer_cs": int(su["cs"]) if su else 0,
                    "summer_sac": int(su["sac"]) if su else 0,
                    "summer_sources": (su or {}).get("sources") or [],
                    "vs_l": vs_l if vs_l and vs_l["pa"] >= 15 else None,
                    "vs_r": vs_r if vs_r and vs_r["pa"] >= 15 else None,
                    "sb2": sb2,
                    "sb3": sb3,
                    "sb_split": bool(sb2 or sb3),
                    "bunts": int(overall.get("bunts") or 0) if adv else None,
                    "bunts_risp": int((risp or {}).get("bunts") or 0) if adv else None,
                    "bunts_vl": int((vs_l or {}).get("bunts") or 0) if adv else None,
                    "bunts_vr": int((vs_r or {}).get("bunts") or 0) if adv else None,
                    "in_synergy": bool(adv),
                    "src": "LBPRC 2025" if hit else None,
                }
                if role == "P" and (not hit or pa < 5):
                    # Pitchers stay pitchers; keep a thin hitter block only when they actually hit.
                    if not hit or pa < 5:
                        hitter = None

            pitcher = None
            if role == "P" or (pit and roster_pos == "P"):
                bf = int(num(pit.get("battersFaced")) or 0) if pit else 0
                so = int(num(pit.get("strikeOuts")) or 0) if pit else 0
                bb = int(num(pit.get("baseOnBalls")) or 0) if pit else 0
                go = int(num(pit.get("groundOuts")) or 0) if pit else 0
                ao = int(num(pit.get("airOuts")) or 0) if pit else 0
                wh = None
                if arm:
                    wnum = wden = 0.0
                    for pitch in arm.get("arsenal") or []:
                        n = num(pitch.get("pitches")) or 0
                        w = num(pitch.get("whiff"))
                        if n and w is not None:
                            wnum += w * n
                            wden += n
                    if wden:
                        wh = round(wnum / wden, 1)
                pitcher = {
                    "bf": bf,
                    "k": pct(so, bf) if pit and bf else None,
                    "gb": pct(go, go + ao) if pit and (go + ao) else None,
                    "bb": pct(bb, bf) if pit and bf else None,
                    "ops": num(pit.get("ops")) if pit else None,
                    "avg": num(pit.get("avg")) if pit else None,
                    "wh": wh,
                    "gs": int(num(pit.get("gamesStarted")) or 0) if pit else 0,
                    "hand_split": False,
                    "src": "LBPRC 2025" if pit else ("arsenal whiff only" if wh is not None else None),
                }

            if role == "P":
                hitter = None

            players.append(
                {
                    "id": pid,
                    "name": name,
                    "last": last_name(name),
                    "team": abbrev,
                    "roster_pos": roster_pos,
                    "role": "P" if role == "P" else "H",
                    "bats": bats,
                    "throws": throws,
                    "hitter": hitter if role != "P" else None,
                    "pitcher": pitcher if role == "P" else None,
                }
            )

    # Gaps for the UI footnote / ship report
    hitters = [p for p in players if p["role"] == "H"]
    pitchers = [p for p in players if p["role"] == "P"]
    gaps = {
        "roster_source": "data/lbprc_2025_rosters.json (current LBPRC teams; PR players not moved)",
        "sb_2b_3b_split": "Synergy sb_from_1b / sb_from_2b is 0 for every advance hitter — runner heuristic did not record steals. LBPRC and 2026 summer files only have total SB/CS.",
        "sprint_speed": "No sprint-speed field in the caches. SPD column is blank.",
        "pitcher_platoon": "No vs-LHB / vs-RHB outcome split (K/GB/WH/BB/OPS) in the pitching cache. Both rows repeat the season line.",
        "hitter_platoon_k_gb": "K% and GB% are season totals. WH/AVG/OPS use Synergy vs LHP/RHP when that side has at least 15 PA.",
        "santurce_synergy": "The Synergy hitter file is opposing clubs only. A SAN roster player gets bunt/hand lines only when the same MLBAM id appears there (usually tagged to another club). Everyone else on SAN is counting stats only.",
        "sju_in_br_file": "pregame_baserunning_bunting.json omits SJU; SJU counting stats come from lbprc_2025_hitting.json.",
        "hitters": len(hitters),
        "pitchers": len(pitchers),
        "hitters_with_lbprc_line": sum(1 for p in hitters if p["hitter"] and p["hitter"].get("src")),
        "hitters_with_summer": sum(1 for p in hitters if p["hitter"] and (p["hitter"]["summer_sb"] or p["hitter"]["summer_cs"] or p["hitter"]["summer_sac"])),
        "hitters_with_hand_split": sum(1 for p in hitters if p["hitter"] and (p["hitter"].get("vs_l") or p["hitter"].get("vs_r"))),
        "hitters_with_sb_split": sum(1 for p in hitters if p["hitter"] and p["hitter"].get("sb_split")),
        "hitters_with_bunt_situations": sum(1 for p in hitters if p["hitter"] and p["hitter"].get("in_synergy")),
        "pitchers_with_line": sum(1 for p in pitchers if p["pitcher"] and p["pitcher"].get("k") is not None),
    }

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "teams": TEAMS,
        "players": players,
        "gaps": gaps,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT} players={len(players)}")
    for k, v in gaps.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

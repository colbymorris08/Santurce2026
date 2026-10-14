#!/usr/bin/env python3
"""Compact roster + rate cache for the strategy and small-ball pregame cards.

Rosters use the 2026-27 team assignments stored in lbprc_2025_rosters.json
(the legacy filename is retained for site compatibility). Counting stats: LBPRC 2025 when that line exists. If it does not, the
2026 then 2025 MLB Stats API season line (K/GB/AVG/OPS or pitcher K/GB/BB/OPS).
2026 summer SB/CS/sac bunts still come from the MLB + MiLB + LMB season files.
Platoon whiff and bunt situations: Synergy advance hitters when the player is
in that file. Whiff is not invented when Synergy has no swings.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
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


def _api_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "santurce-cards"})
    with urllib.request.urlopen(req, timeout=40) as response:
        return json.loads(response.read().decode())


def _best_split(splits: list, kind: str) -> dict | None:
    best = None
    best_n = -1
    for split in splits or []:
        stat = split.get("stat") or {}
        if kind == "hitting":
            n = int(stat.get("plateAppearances") or 0)
        else:
            n = int(stat.get("battersFaced") or stat.get("plateAppearances") or 0)
        if n > best_n:
            best_n = n
            best = split
    return best if best_n > 0 else None


def _hit_from_stat(stat: dict, season: str, sport: str) -> dict:
    pa = int(stat.get("plateAppearances") or 0)
    so = int(stat.get("strikeOuts") or 0)
    go = int(stat.get("groundOuts") or 0)
    ao = int(stat.get("airOuts") or 0)
    return {
        "pa": pa,
        "k": pct(so, pa),
        "gb": pct(go, go + ao) if (go + ao) else None,
        "avg": num(stat.get("avg")),
        "ops": num(stat.get("ops")),
        "sb": int(stat.get("stolenBases") or 0),
        "cs": int(stat.get("caughtStealing") or 0),
        "sac": int(stat.get("sacBunts") or 0),
        "src": f"{sport or 'MLB'} {season}",
    }


def _pit_from_stat(stat: dict, season: str, sport: str) -> dict:
    bf = int(stat.get("battersFaced") or stat.get("plateAppearances") or 0)
    so = int(stat.get("strikeOuts") or 0)
    bb = int(stat.get("baseOnBalls") or 0)
    go = int(stat.get("groundOuts") or 0)
    ao = int(stat.get("airOuts") or 0)
    return {
        "bf": bf,
        "k": pct(so, bf) if bf else None,
        "gb": pct(go, go + ao) if (go + ao) else None,
        "bb": pct(bb, bf) if bf else None,
        "ops": num(stat.get("ops")),
        "avg": num(stat.get("avg")),
        "gs": int(stat.get("gamesStarted") or 0),
        "src": f"{sport or 'MLB'} {season}",
    }


def season_api_lines(player_ids: list[int]) -> dict[int, dict]:
    """Most recent regular-season line on MLB Stats API, 2026 then 2025."""
    cache_path = DATA / "card_season_api.json"
    cached = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    for season in ("2026", "2025"):
        pending = []
        for pid in player_ids:
            if pid <= 0:
                continue
            row = cached.get(str(pid)) or {}
            if row.get("hitting") or row.get("pitching"):
                continue
            if season == "2025" and row.get("checked_2025"):
                continue
            pending.append(pid)
        for i in range(0, len(pending), 40):
            chunk = pending[i:i + 40]
            ids = ",".join(str(pid) for pid in chunk)
            url = (
                "https://statsapi.mlb.com/api/v1/people?personIds="
                + ids
                + "&hydrate=stats(group=[hitting,pitching],type=[season],season="
                + season
                + ",gameType=R)"
            )
            try:
                payload = _api_get(url)
            except Exception as exc:
                print(f"  stats api {season} chunk failed: {exc}")
                continue
            seen = set()
            for person in payload.get("people") or []:
                pid = int(person.get("id") or 0)
                if not pid:
                    continue
                seen.add(pid)
                row = cached.setdefault(str(pid), {})
                for block in person.get("stats") or []:
                    group = ((block.get("group") or {}).get("displayName") or "").lower()
                    kind = "hitting" if "hit" in group else "pitching" if "pitch" in group else ""
                    if not kind or row.get(kind):
                        continue
                    split = _best_split(block.get("splits") or [], kind)
                    if not split:
                        continue
                    stat = split.get("stat") or {}
                    sport = ((split.get("sport") or {}).get("abbreviation") or "").upper()
                    built = _hit_from_stat(stat, season, sport) if kind == "hitting" else _pit_from_stat(stat, season, sport)
                    built["team"] = (split.get("team") or {}).get("name")
                    row[kind] = built
                if season == "2025":
                    row["checked_2025"] = True
            for pid in chunk:
                if pid not in seen and season == "2025":
                    cached.setdefault(str(pid), {})["checked_2025"] = True
        cache_path.write_text(json.dumps(cached, ensure_ascii=False))
    # Older seasons for ids that never appeared in 2026 or 2025.
    for kind, group in (("hitting", "hitting"), ("pitching", "pitching")):
        pending = []
        for pid in player_ids:
            if pid <= 0:
                continue
            row = cached.get(str(pid)) or {}
            if row.get(kind) or row.get(f"checked_yby_{kind}"):
                continue
            pending.append(pid)
        for i in range(0, len(pending), 20):
            chunk = pending[i:i + 20]
            ids = ",".join(str(pid) for pid in chunk)
            url = (
                "https://statsapi.mlb.com/api/v1/people?personIds="
                + ids
                + "&hydrate=stats(group=["
                + group
                + "],type=[yearByYear],sportIds=[1,11,12,13,14,16,23])"
            )
            try:
                payload = _api_get(url)
            except Exception as exc:
                print(f"  year-by-year {kind} chunk failed: {exc}")
                continue
            seen = set()
            for person in payload.get("people") or []:
                pid = int(person.get("id") or 0)
                if not pid:
                    continue
                seen.add(pid)
                row = cached.setdefault(str(pid), {})
                row[f"checked_yby_{kind}"] = True
                if row.get(kind):
                    continue
                splits = []
                for block in person.get("stats") or []:
                    splits.extend(block.get("splits") or [])
                split = _latest_split(splits, kind)
                if not split:
                    continue
                stat = split.get("stat") or {}
                season = str(split.get("season") or "")
                sport = ((split.get("sport") or {}).get("abbreviation") or "").upper()
                built = _hit_from_stat(stat, season, sport) if kind == "hitting" else _pit_from_stat(stat, season, sport)
                built["team"] = (split.get("team") or {}).get("name")
                row[kind] = built
            for pid in chunk:
                if pid not in seen:
                    cached.setdefault(str(pid), {})[f"checked_yby_{kind}"] = True
    cache_path.write_text(json.dumps(cached, ensure_ascii=False))
    return {int(k): v for k, v in cached.items()}


def _latest_split(splits: list, kind: str) -> dict | None:
    best = None
    best_key = (-1, -1)
    for split in splits or []:
        stat = split.get("stat") or {}
        if kind == "hitting":
            n = int(stat.get("plateAppearances") or 0)
        else:
            n = int(stat.get("battersFaced") or stat.get("plateAppearances") or 0)
        if n <= 0:
            continue
        season = int(split.get("season") or 0)
        key = (season, n)
        if key > best_key:
            best_key = key
            best = split
    return best


def norm_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def rates_from_summer_line(line: dict | None) -> dict | None:
    """K/AVG/OPS from a researched batting line. GB is omitted when GO/AO are absent."""
    if not line:
        return None
    pa = int(line.get("pa") or 0)
    ab = int(line.get("ab") or 0)
    hits = int(line.get("h") or 0)
    so = int(line.get("so") or 0)
    if pa < 1:
        return None
    avg = num(line.get("ba"))
    if avg is None and ab:
        avg = round(hits / ab, 3)
    ops = num(line.get("ops"))
    if avg is None and ops is None:
        return None
    label = " ".join(str(part) for part in (line.get("season"), line.get("team")) if part)
    return {
        "pa": pa,
        "k": pct(so, pa),
        "gb": None,
        "avg": avg,
        "ops": ops,
        "src": label or "season line",
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
    adv_by_name = {}
    for h in advance.get("hitters") or []:
        mid = h.get("mlb_id")
        if mid:
            adv_by[int(mid)] = h
        team_abbrev = h.get("team") or ""
        adv_by_name[(team_abbrev, norm_name(h.get("name") or ""))] = h

    arsenal_by = {}
    for p in arsenals:
        pid = p.get("player_id")
        if pid:
            arsenal_by[int(pid)] = p

    roster_ids = []
    for team in rosters.values():
        for p in team.get("players") or []:
            pid = int(p.get("id") or 0)
            if pid > 0:
                roster_ids.append(pid)
    print(f"fetching season lines for {len(set(roster_ids))} roster ids")
    api_lines = season_api_lines(sorted(set(roster_ids)))
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
            api = api_lines.get(pid) or {}

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
                # Same Synergy rate as the hitter-advance header (whiffs / swings).
                # Hand whiff is kept at any sample. The 15 PA gate stays on AVG/OPS only.
                wh = overall.get("whiff_pct")
                if wh is None and overall.get("swings"):
                    wh = round(100.0 * int(overall.get("whiffs") or 0) / int(overall["swings"]), 1)
                elif wh is not None:
                    wh = round(float(wh), 1)
                wh_l, wh_l_pa = (None, None)
                wh_r, wh_r_pa = (None, None)
                if vs_l and vs_l.get("wh") is not None:
                    wh_l, wh_l_pa = vs_l["wh"], vs_l["pa"]
                if vs_r and vs_r.get("wh") is not None:
                    wh_r, wh_r_pa = vs_r["wh"], vs_r["pa"]
                src = "LBPRC 2025" if hit and pa else None
                api_hit = api.get("hitting")
                if (not hit or not pa) and api_hit:
                    pa = int(api_hit.get("pa") or 0)
                    so = 0
                    go = ao = 0
                    src = api_hit.get("src")
                    hitter_k = api_hit.get("k")
                    hitter_gb = api_hit.get("gb")
                    hitter_avg = api_hit.get("avg")
                    hitter_ops = api_hit.get("ops")
                else:
                    hitter_k = pct(so, pa) if hit else None
                    hitter_gb = pct(go, go + ao) if hit and (go + ao) else None
                    hitter_avg = num(hit.get("avg")) if hit else None
                    hitter_ops = num(hit.get("ops")) if hit else None
                if hitter_k is None and hitter_avg is None:
                    named = adv_by_name.get((abbrev, norm_name(name)))
                    line = ((adv or named or {}).get("summer_line"))
                    rates = rates_from_summer_line(line)
                    if rates:
                        pa = rates["pa"]
                        hitter_k = rates["k"]
                        hitter_gb = rates["gb"]
                        hitter_avg = rates["avg"]
                        hitter_ops = rates["ops"]
                        src = rates["src"]
                hitter = {
                    "pa": pa,
                    "k": hitter_k,
                    "gb": hitter_gb,
                    "avg": hitter_avg,
                    "ops": hitter_ops,
                    "wh": wh,
                    "wh_l": wh_l,
                    "wh_r": wh_r,
                    "wh_l_pa": wh_l_pa,
                    "wh_r_pa": wh_r_pa,
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
                    "src": src,
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
                src_p = "LBPRC 2025" if pit and bf else ("arsenal whiff only" if wh is not None else None)
                api_pit = api.get("pitching")
                if (not pit or not bf) and api_pit and api_pit.get("bf"):
                    bf = int(api_pit.get("bf") or 0)
                    src_p = api_pit.get("src")
                    pit_k = api_pit.get("k")
                    pit_gb = api_pit.get("gb")
                    pit_bb = api_pit.get("bb")
                    pit_ops = api_pit.get("ops")
                    pit_avg = api_pit.get("avg")
                    pit_gs = int(api_pit.get("gs") or 0)
                else:
                    pit_k = pct(so, bf) if pit and bf else None
                    pit_gb = pct(go, go + ao) if pit and (go + ao) else None
                    pit_bb = pct(bb, bf) if pit and bf else None
                    pit_ops = num(pit.get("ops")) if pit else None
                    pit_avg = num(pit.get("avg")) if pit else None
                    pit_gs = int(num(pit.get("gamesStarted")) or 0) if pit else 0
                pitcher = {
                    "bf": bf,
                    "k": pit_k,
                    "gb": pit_gb,
                    "bb": pit_bb,
                    "ops": pit_ops,
                    "avg": pit_avg,
                    "wh": wh,
                    "gs": pit_gs,
                    "hand_split": False,
                    "src": src_p,
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
        "roster_source": "data/lbprc_2025_rosters.json (2026-27 workbooks plus supplied October transactions)",
        "sb_2b_3b_split": "Synergy sb_from_1b / sb_from_2b is 0 for every advance hitter — runner heuristic did not record steals. LBPRC and 2026 summer files only have total SB/CS.",
        "sprint_speed": "No sprint-speed field in the caches. SPD column is blank.",
        "pitcher_platoon": "No vs-LHB / vs-RHB outcome split (K/GB/WH/BB/OPS) in the pitching cache. Both rows repeat the season line.",
        "hitter_platoon_k_gb": "K% and GB% are season totals. WH is the Synergy whiff vs that hand when swings exist, otherwise the hitter's overall whiff. AVG/OPS use the Synergy split only when that side has at least 15 PA.",
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

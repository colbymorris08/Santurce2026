#!/usr/bin/env python3
"""Compact roster + rate cache for the strategy and small-ball pregame cards.

Rosters use the 2026-27 team assignments stored in lbprc_2025_rosters.json
(the legacy filename is retained for site compatibility). Strategy-card rates use the newest season that actually has a line:
2026 summer, then 2025-26 winter, then 2025 summer, then the next older
winter, then the next older summer. An independent-league line is skipped
when that player has no Synergy whiff or count tables, so the next real
season is used. 2026 summer SB/CS/sac bunts still come
from the MLB + MiLB + LMB season files.
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


def season_rank(label: str) -> tuple | None:
    """Higher sorts first. Summer 2026, then winter 2025-26, then summer 2025."""
    text = (label or "").lower().replace("–", "-")
    years = [int(year) for year in re.findall(r"20\d{2}", text)]
    if not years:
        return None
    if any(token in text for token in ("winter", "lbprc", "lidom", "lmp")):
        if len(years) >= 2:
            return (years[-1], 0)
        return (years[0] + 1, 0)
    return (years[-1], 1)


def season_text(rank: tuple, team: str | None = None) -> str:
    year, kind = rank
    if kind == 1:
        label = f"Summer {year}"
    else:
        label = f"Winter {year - 1}-{str(year)[2:]}"
    team = (team or "").strip()
    return f"{label} {team}".strip()


def choose_line(candidates: list) -> dict | None:
    best = None
    best_key = None
    for line in candidates:
        if not line or not line.get("rank"):
            continue
        sample = int(line.get("pa") or line.get("bf") or 0)
        if sample < 1:
            continue
        key = (line["rank"], sample, line.get("gb") is not None)
        if best_key is None or key > best_key:
            best = line
            best_key = key
    return best


def _accumulate_rows(files: list[str], kind: str) -> dict[int, dict]:
    buckets: dict[int, dict] = {}
    for fname in files:
        rows = load(fname) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            pid = int(row.get("playerId") or 0)
            if pid <= 0:
                continue
            sample = int(num(row.get("plateAppearances" if kind == "hitting" else "battersFaced")) or 0)
            if sample < 1:
                continue
            bucket = buckets.setdefault(pid, {
                "pa": 0, "bf": 0, "so": 0, "bb": 0, "go": 0, "ao": 0, "gs": 0,
                "avg_w": 0.0, "ops_w": 0.0, "w": 0.0, "team": None, "team_n": 0,
            })
            weight = float(sample)
            if kind == "hitting":
                bucket["pa"] += sample
            else:
                bucket["bf"] += sample
                bucket["bb"] += int(num(row.get("baseOnBalls")) or 0)
                bucket["gs"] += int(num(row.get("gamesStarted")) or 0)
            bucket["so"] += int(num(row.get("strikeOuts")) or 0)
            bucket["go"] += int(num(row.get("groundOuts")) or 0)
            bucket["ao"] += int(num(row.get("airOuts")) or 0)
            avg = num(row.get("avg"))
            ops = num(row.get("ops"))
            if avg is not None:
                bucket["avg_w"] += avg * weight
            if ops is not None:
                bucket["ops_w"] += ops * weight
            bucket["w"] += weight
            abbrev = row.get("teamAbbrev") or row.get("teamName")
            if abbrev and sample >= bucket["team_n"]:
                bucket["team"] = abbrev
                bucket["team_n"] = sample
    return buckets


def hitting_candidates_from_files(files: list[str], label: str) -> dict[int, dict]:
    rank = season_rank(label)
    out = {}
    for pid, bucket in _accumulate_rows(files, "hitting").items():
        pa = bucket["pa"]
        if not pa or not rank:
            continue
        out[pid] = {
            "rank": rank,
            "pa": pa,
            "k": pct(bucket["so"], pa),
            "gb": pct(bucket["go"], bucket["go"] + bucket["ao"]) if (bucket["go"] + bucket["ao"]) else None,
            "avg": round(bucket["avg_w"] / bucket["w"], 3) if bucket["w"] else None,
            "ops": round(bucket["ops_w"] / bucket["w"], 3) if bucket["w"] else None,
            "src": season_text(rank, bucket.get("team")),
        }
    return out


def pitching_candidates_from_files(files: list[str], label: str) -> dict[int, dict]:
    rank = season_rank(label)
    out = {}
    for pid, bucket in _accumulate_rows(files, "pitching").items():
        bf = bucket["bf"]
        if not bf or not rank:
            continue
        out[pid] = {
            "rank": rank,
            "bf": bf,
            "k": pct(bucket["so"], bf),
            "gb": pct(bucket["go"], bucket["go"] + bucket["ao"]) if (bucket["go"] + bucket["ao"]) else None,
            "bb": pct(bucket["bb"], bf),
            "ops": round(bucket["ops_w"] / bucket["w"], 3) if bucket["w"] else None,
            "avg": round(bucket["avg_w"] / bucket["w"], 3) if bucket["w"] else None,
            "gs": bucket["gs"],
            "src": season_text(rank, bucket.get("team")),
        }
    return out


def tagged_api_line(line: dict | None, kind: str) -> dict | None:
    if not line:
        return None
    rank = season_rank(line.get("src") or "")
    sample = int(line.get("pa") or 0) if kind == "hitting" else int(line.get("bf") or 0)
    if not rank or sample < 1:
        return None
    tagged = dict(line)
    tagged["rank"] = rank
    tagged["src"] = season_text(rank, line.get("team"))
    return tagged


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
    rank = season_rank(str(line.get("season") or ""))
    if not rank:
        return None
    return {
        "rank": rank,
        "pa": pa,
        "k": pct(so, pa),
        "gb": None,
        "avg": avg,
        "ops": ops,
        "src": season_text(rank, line.get("team")),
    }


INDY_LEAGUES = {"ATLL", "FRON", "AA", "PION", "USBL"}
COUNT_TABLES = (
    "pre2k_vs_r_by_pitch_type",
    "pre2k_vs_l_by_pitch_type",
    "twok_vs_r_by_pitch_type",
    "twok_vs_l_by_pitch_type",
)


def is_indy_line(line: dict | None) -> bool:
    if not line:
        return False
    if str(line.get("level") or "") == "Ind":
        return True
    return str(line.get("league") or "") in INDY_LEAGUES


def has_synergy_detail(packet: dict | None) -> bool:
    """Whiff or count-usage tables. An indy counting line does not supply these."""
    if not packet:
        return False
    overall = packet.get("overall") or {}
    if int(overall.get("swings") or 0) > 0:
        return True
    return any(packet.get(key) for key in COUNT_TABLES)


def confirmed_hitting_lines() -> dict[int, dict]:
    """Lines for roster ids the season files and Stats API do not carry."""
    raw = load("card_confirmed_lines.json") or {}
    out = {}
    for key, row in raw.items():
        if row.get("role") == "P":
            continue
        rank = season_rank(str(row.get("season") or ""))
        pa = int(row.get("pa") or 0)
        if not rank or pa < 1:
            continue
        so = int(row.get("so") or 0)
        go = int(row.get("go") or 0)
        ao = int(row.get("ao") or 0)
        out[int(key)] = {
            "rank": rank,
            "pa": pa,
            "k": pct(so, pa),
            "gb": pct(go, go + ao) if (go + ao) else None,
            "avg": num(row.get("avg")),
            "ops": num(row.get("ops")),
            "sb": int(row.get("sb") or 0),
            "cs": int(row.get("cs") or 0),
            "source": row.get("source") or "",
            "src": row.get("src") or season_text(rank, row.get("team")),
        }
    return out


def confirmed_pitching_lines() -> dict[int, dict]:
    raw = load("card_confirmed_lines.json") or {}
    out = {}
    for key, row in raw.items():
        if row.get("role") != "P":
            continue
        rank = season_rank(str(row.get("season") or ""))
        bf = int(row.get("bf") or 0)
        if not rank or bf < 1:
            continue
        so = int(row.get("so") or 0)
        bb = int(row.get("bb") or 0)
        go = int(row.get("go") or 0)
        ao = int(row.get("ao") or 0)
        out[int(key)] = {
            "rank": rank,
            "bf": bf,
            "k": pct(so, bf),
            "gb": pct(go, go + ao) if (go + ao) else None,
            "bb": pct(bb, bf),
            "ops": num(row.get("ops")),
            "avg": num(row.get("avg")),
            "gs": int(row.get("gs") or 0),
            "src": row.get("src") or season_text(rank, row.get("team")),
        }
    return out


SB_LEVELS = {
    "MLB": "MLB",
    "AAA": "AAA",
    "AA": "AA",
    "A+": "A+",
    "A": "A",
    "RK": "RK",
    "ROK": "RK",
    "MEX": "LMB",
    "LMB": "LMB",
    "IND": "Indy",
    "INDY": "Indy",
}
SB_LEVEL_ORDER = ["MLB", "AAA", "AA", "A+", "A", "RK", "LMB", "Indy"]


def sb_level_label(raw: str | None) -> str | None:
    key = str(raw or "").strip().upper()
    if not key:
        return None
    return SB_LEVELS.get(key, key)


def league_from_text(text: str | None) -> str | None:
    """Level named in a confirmed-line note, such as '(AAA)' or 'Mexican League'."""
    found = []
    for raw in re.findall(r"\((AAA|AA|A\+|A|RK|MLB|LMB|MEX|Indy|Ind)\)", text or "", flags=re.I):
        label = sb_level_label(raw)
        if label and label not in found:
            found.append(label)
    if found:
        return "/".join(found)
    low = (text or "").lower()
    if any(token in low for token in ("mexican", "lmb", " liga mex")):
        return "LMB"
    if any(token in low for token in ("indy", "independent", "atlantic league", "frontier", "american association")):
        return "Indy"
    if "mlb" in low and "milb" not in low:
        return "MLB"
    if "aaa" in low:
        return "AAA"
    if re.search(r"\baa\b", low):
        return "AA"
    if "milb" in low:
        return "MiLB"
    return None


def summer_level_rows(levels: dict) -> list[dict]:
    rows = []
    seen = set()
    for label in SB_LEVEL_ORDER:
        row = levels.get(label)
        if not row or not (row["sb"] or row["cs"] or row.get("sac")):
            continue
        rows.append({"league": label, "sb": row["sb"], "cs": row["cs"], "sac": int(row.get("sac") or 0)})
        seen.add(label)
    for label, row in levels.items():
        if label in seen or not (row["sb"] or row["cs"] or row.get("sac")):
            continue
        rows.append({"league": label, "sb": row["sb"], "cs": row["cs"], "sac": int(row.get("sac") or 0)})
    return rows


def advance_packet(pid: int, team: str, name: str, by_id: dict, by_name: dict) -> dict | None:
    """Synergy packet for this roster row. A placeholder id can use the same team and name."""
    named = by_name.get((team, norm_name(name)))
    direct = by_id.get(pid)

    def swings(rec: dict | None) -> int:
        return int(((rec or {}).get("overall") or {}).get("swings") or 0)

    if direct and swings(direct):
        return direct
    if not named:
        return direct
    mid = named.get("mlb_id")
    if pid > 0 and mid and int(mid) != int(pid):
        return direct
    if swings(named) or not direct:
        return named
    return direct


def rodriguez_whiff() -> dict | None:
    """Usage-weighted miss rates from the Synergy Rodriguez packet. Miss here is whiff."""
    doc = load("advance_rodriguez.json") or {}
    arsenal = doc.get("arsenal") or []
    if not arsenal:
        return None
    left_n = left_d = right_n = right_d = 0.0
    for pitch in arsenal:
        left_w = num(pitch.get("usage_l")) or 0
        right_w = num(pitch.get("usage_r")) or 0
        if pitch.get("miss_l") is not None and left_w:
            left_n += float(pitch["miss_l"]) * left_w
            left_d += left_w
        if pitch.get("miss_r") is not None and right_w:
            right_n += float(pitch["miss_r"]) * right_w
            right_d += right_w
    overall = num((doc.get("totals") or {}).get("miss_pct"))
    if overall is None and not left_d and not right_d:
        return None
    return {
        "wh": overall,
        "wh_l": round(left_n / left_d, 1) if left_d else overall,
        "wh_r": round(right_n / right_d, 1) if right_d else overall,
    }


def saved_whiff() -> dict[int, dict]:
    """Player-level Synergy whiff saved by fill_card_whiff.py.

    The opposing advance file omits Santurce, and a truncated team pull misses
    players whose swings are in 2026 MLB/MiLB. This cache is what the card
    uses when the advance packet has no whiff.
    """
    doc = load("card_whiff.json") or {}
    out = {}
    for row in doc.get("players") or []:
        try:
            out[int(row["id"])] = row
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fill_whiff_from_cache(pid: int, wh, wh_l, wh_r, wh_l_pa, wh_r_pa, cache: dict):
    saved = cache.get(int(pid)) or {}
    if saved.get("wh") is None:
        return wh, wh_l, wh_r, wh_l_pa, wh_r_pa
    if wh is None:
        wh = saved.get("wh")
    if wh_l is None and saved.get("wh_l") is not None:
        wh_l, wh_l_pa = saved.get("wh_l"), saved.get("swings_l")
    if wh_r is None and saved.get("wh_r") is not None:
        wh_r, wh_r_pa = saved.get("wh_r"), saved.get("swings_r")
    return wh, wh_l, wh_r, wh_l_pa, wh_r_pa


def summer_by_id() -> dict[int, dict]:
    agg: dict[int, dict] = defaultdict(lambda: {"sb": 0, "cs": 0, "sac": 0, "pa": 0, "levels": {}})
    for fname in ("mlb_2026_hitting.json", "milb_2026_hitting.json", "mexico_2026_hitting.json"):
        rows = load(fname) or []
        if not isinstance(rows, list):
            continue
        for r in rows:
            pid = r.get("playerId")
            if not pid:
                continue
            pid = int(pid)
            label = sb_level_label(r.get("_level")) or "MiLB"
            a = agg[pid]
            sb = int(r.get("stolenBases") or 0)
            cs = int(r.get("caughtStealing") or 0)
            a["sb"] += sb
            a["cs"] += cs
            a["sac"] += int(r.get("sacBunts") or 0)
            a["pa"] += int(num(r.get("plateAppearances")) or 0)
            bucket = a["levels"].setdefault(label, {"sb": 0, "cs": 0, "sac": 0})
            bucket["sb"] += sb
            bucket["cs"] += cs
            bucket["sac"] += int(r.get("sacBunts") or 0)
    out = {}
    for pid, row in agg.items():
        if not (row["sb"] or row["cs"] or row["sac"] or row["pa"]):
            continue
        levels = summer_level_rows(row["levels"])
        row["levels"] = levels
        row["sources"] = [level["league"] for level in levels]
        out[pid] = row
    return out


GB_SPORTS = (1, 11, 12, 13, 14, 16, 23)
SUMMER_GB_FILES = {
    "hitting": ("milb_2026_hitting.json", "mlb_2026_hitting.json", "mexico_2026_hitting.json"),
    "pitching": ("milb_2026_pitching.json", "mlb_2026_pitching.json", "mexico_2026_pitching.json"),
}
WINTER_GB_FILES = {
    "hitting": ("lbprc_2025_hitting.json", "lidom_2025_hitting.json"),
    "pitching": ("lbprc_2025_pitching.json", "lidom_2025_pitching.json"),
}


def _gb_year(src: str | None) -> tuple[str, int] | None:
    text = (src or "").lower()
    years = [int(year) for year in re.findall(r"20\d{2}", text)]
    if not years:
        return None
    if "winter" in text:
        return ("winter", years[0])
    return ("summer", years[-1])


def _sum_ground_outs(files: tuple[str, ...], pid: int) -> tuple[int, int]:
    go = ao = 0
    for fname in files:
        for row in load(fname) or []:
            if int(row.get("playerId") or 0) != pid:
                continue
            go += int(num(row.get("groundOuts")) or 0)
            ao += int(num(row.get("airOuts")) or 0)
    return go, ao


def _local_ground_ball(pid: int, src: str | None, kind: str) -> float | None:
    if pid <= 0:
        return None
    window = _gb_year(src)
    if not window:
        return None
    season_kind, year = window
    if season_kind == "winter" and year == 2025:
        files = WINTER_GB_FILES[kind]
    elif season_kind == "summer" and year == 2026:
        files = SUMMER_GB_FILES[kind]
    else:
        return None
    go, ao = _sum_ground_outs(files, pid)
    return pct(go, go + ao) if (go + ao) else None


GB_NAME_ALIASES = {
    "cameron cauley": "cam cauley",
    "jeffrey caglianone": "jac caglianone",
    "dominic hamel": "dom hamel",
    "cameron maldonado": "cam maldonado",
}


def _stats_ground_index(group: str, season: int) -> dict[int, dict]:
    """player id -> per-level ground-out splits and a normalized name."""
    found: dict[int, dict] = {}
    for sport in GB_SPORTS:
        offset = 0
        while True:
            url = (
                "https://statsapi.mlb.com/api/v1/stats?stats=season"
                f"&group={group}&season={season}&sportId={sport}"
                f"&playerPool=ALL&gameType=R&limit=2000&offset={offset}"
            )
            try:
                payload = _api_get(url)
            except Exception as exc:
                print(f"  gb stats {group} {season} sport {sport} offset {offset}: {exc}")
                break
            block = (payload.get("stats") or [{}])[0]
            splits = block.get("splits") or []
            for split in splits:
                player = split.get("player") or {}
                pid = int(player.get("id") or 0)
                stat = split.get("stat") or {}
                if pid <= 0:
                    continue
                sample = int(stat.get("plateAppearances") or stat.get("battersFaced") or 0)
                bag = found.setdefault(pid, {"name": norm_name(player.get("fullName") or ""), "splits": []})
                if player.get("fullName"):
                    bag["name"] = norm_name(player["fullName"])
                bag["splits"].append({
                    "sample": sample,
                    "go": int(stat.get("groundOuts") or 0),
                    "ao": int(stat.get("airOuts") or 0),
                })
            total = int(block.get("totalSplits") or 0)
            offset += len(splits)
            if not splits or offset >= total:
                break
    return found


def _rate_from_splits(splits: list[dict], sample: int) -> float | None:
    """Use the level whose PA/BF matches the card line; otherwise the full season."""
    exact = [row for row in splits if sample and row["sample"] == sample and (row["go"] + row["ao"])]
    if len(exact) == 1:
        row = exact[0]
        return pct(row["go"], row["go"] + row["ao"])
    go = sum(row["go"] for row in splits)
    ao = sum(row["ao"] for row in splits)
    total = sum(row["sample"] for row in splits)
    if sample and total == sample and (go + ao):
        return pct(go, go + ao)
    if not sample and (go + ao):
        return pct(go, go + ao)
    if len(exact) > 1 and (go + ao):
        return pct(go, go + ao)
    return None


def fill_missing_ground_balls(players: list[dict]) -> int:
    """Fill GB% from ground outs / (ground outs + air outs). Does not invent a rate."""
    pools: dict[tuple[str, int], dict[int, dict]] = {}
    names: dict[tuple[str, int], dict[str, list[int]]] = {}
    filled = 0

    def pool(kind: str, year: int) -> dict[int, dict]:
        key = (kind, year)
        if key not in pools:
            group = "hitting" if kind == "hitting" else "pitching"
            print(f"  loading {group} ground outs {year}")
            pools[key] = _stats_ground_index(group, year)
            by_name: dict[str, list[int]] = {}
            for pid, bag in pools[key].items():
                if bag.get("name"):
                    by_name.setdefault(bag["name"], []).append(pid)
            names[key] = by_name
        return pools[key]

    for player in players:
        kind = "hitting" if player.get("role") == "H" else "pitching"
        block_name = "hitter" if kind == "hitting" else "pitcher"
        block = player.get(block_name) or {}
        if not block or block.get("gb") is not None:
            continue
        src = block.get("src") or ""
        if src == "arsenal whiff only":
            src = ""
        pid = int(player.get("id") or 0)
        rate = _local_ground_ball(pid, src, kind)
        window = _gb_year(src)
        # A college or NPB line does not have MLB ground outs. Leave it for a
        # source that actually publishes GO/AO, instead of borrowing another league.
        borrowed_ok = not any(token in src.lower() for token in ("college", "npb", "lotte", "seibu", " sei"))
        years = []
        if window and window[0] == "summer" and borrowed_ok:
            years = [window[1]]
        elif not window:
            years = [2026, 2025]
        sample = int(block.get("pa") or block.get("bf") or 0)
        for year in years:
            if rate is not None:
                break
            index = pool(kind, year)
            bag = index.get(pid) if pid > 0 else None
            if bag is None and pid <= 0:
                wanted = norm_name(player.get("name") or "")
                wanted = GB_NAME_ALIASES.get(wanted, wanted)
                hits = names[(kind, year)].get(wanted) or []
                if len(hits) == 1:
                    bag = index.get(hits[0])
            if bag:
                rate = _rate_from_splits(bag.get("splits") or [], sample)
        if rate is None or rate == block.get("gb"):
            continue
        block["gb"] = rate
        filled += 1

    # A card line for one level should use that level's ground outs, not the
    # sum of every level the player touched that year.
    for player in players:
        kind = "hitting" if player.get("role") == "H" else "pitching"
        block = player.get("hitter" if kind == "hitting" else "pitcher") or {}
        if not block or block.get("gb") is None:
            continue
        window = _gb_year(block.get("src") or "")
        if not window or window[0] != "summer":
            continue
        sample = int(block.get("pa") or block.get("bf") or 0)
        if sample < 1:
            continue
        pid = int(player.get("id") or 0)
        index = pool(kind, window[1])
        bag = index.get(pid) if pid > 0 else None
        if bag is None and pid <= 0:
            wanted = GB_NAME_ALIASES.get(norm_name(player.get("name") or ""), norm_name(player.get("name") or ""))
            hits = names[(kind, window[1])].get(wanted) or []
            if len(hits) == 1:
                bag = index.get(hits[0])
        if not bag:
            continue
        exact = [row for row in bag.get("splits") or [] if row["sample"] == sample and (row["go"] + row["ao"])]
        if len(exact) != 1:
            continue
        rate = pct(exact[0]["go"], exact[0]["go"] + exact[0]["ao"])
        if rate is not None and rate != block.get("gb"):
            block["gb"] = rate
            filled += 1
    return filled


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

    whiff_cache = saved_whiff()

    arsenal_by = {}
    for p in arsenals:
        pid = p.get("player_id")
        if pid:
            arsenal_by[int(pid)] = p

    summer_hit = hitting_candidates_from_files(
        ["mlb_2026_hitting.json", "milb_2026_hitting.json", "mexico_2026_hitting.json"],
        "Summer 2026",
    )
    winter_hit = hitting_candidates_from_files(
        ["lbprc_2025_hitting.json", "lidom_2025_hitting.json", "lmp_2025_hitting.json"],
        "Winter 2025-26",
    )
    summer_pit = pitching_candidates_from_files(
        ["mlb_2026_pitching.json", "milb_2026_pitching.json", "mexico_2026_pitching.json"],
        "Summer 2026",
    )
    winter_pit = pitching_candidates_from_files(
        ["lbprc_2025_pitching.json", "lidom_2025_pitching.json", "lmp_2025_pitching.json"],
        "Winter 2025-26",
    )
    roster_ids = []
    for team in rosters.values():
        for p in team.get("players") or []:
            pid = int(p.get("id") or 0)
            if pid > 0:
                roster_ids.append(pid)
    print(f"fetching season lines for {len(set(roster_ids))} roster ids")
    api_lines = season_api_lines(sorted(set(roster_ids)))
    confirmed = confirmed_hitting_lines()
    confirmed_pit = confirmed_pitching_lines()
    summer = summer_by_id()
    players = []
    # Key by team + id so a dual-rostered player (same MLBAM id on two clubs)
    # can appear on both strategy-card team pools.
    seen = set()

    for team in rosters.values():
        abbrev = team.get("teamAbbrev")
        for p in team.get("players") or []:
            pid = int(p.get("id") or 0)
            key = (abbrev, pid)
            if not pid or key in seen:
                continue
            seen.add(key)
            name = p.get("name") or ""
            roster_pos = (p.get("position") or "").upper()
            hit = hit_by.get(pid)
            pit = pit_by.get(pid)
            adv = advance_packet(pid, abbrev, name, adv_by, adv_by_name)
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
                wh, wh_l, wh_r, wh_l_pa, wh_r_pa = fill_whiff_from_cache(
                    pid, wh, wh_l, wh_r, wh_l_pa, wh_r_pa, whiff_cache
                )
                named = adv_by_name.get((abbrev, norm_name(name)))
                packet = adv or named or {}
                summer_rates = rates_from_summer_line(packet.get("summer_line"))
                if is_indy_line(packet.get("summer_line")) and not has_synergy_detail(packet):
                    summer_rates = None
                chosen = choose_line([
                    summer_hit.get(pid),
                    winter_hit.get(pid),
                    tagged_api_line(api.get("hitting"), "hitting"),
                    summer_rates,
                    confirmed.get(pid),
                ])
                if chosen:
                    pa = int(chosen.get("pa") or 0)
                    src = chosen.get("src")
                    hitter_k = chosen.get("k")
                    hitter_gb = chosen.get("gb")
                    hitter_avg = chosen.get("avg")
                    hitter_ops = chosen.get("ops")
                else:
                    src = None
                    pa = 0
                    hitter_k = hitter_gb = hitter_avg = hitter_ops = None
                summer_levels = list((su or {}).get("levels") or [])
                if not summer_levels and chosen and (chosen.get("sb") or chosen.get("cs")):
                    league = league_from_text(f"{chosen.get('source') or ''} {chosen.get('src') or ''}")
                    if league and "winter" not in str(chosen.get("src") or "").lower():
                        summer_levels = [{
                            "league": league,
                            "sb": int(chosen.get("sb") or 0),
                            "cs": int(chosen.get("cs") or 0),
                        }]
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
                    "summer_sb": sum(int(level.get("sb") or 0) for level in summer_levels),
                    "summer_cs": sum(int(level.get("cs") or 0) for level in summer_levels),
                    "summer_sac": int(su["sac"]) if su else 0,
                    "summer_sources": [level["league"] for level in summer_levels],
                    "summer_levels": summer_levels,
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
                rod = rodriguez_whiff() if pid == 605446 else None
                if rod:
                    wh = rod.get("wh")
                wh_l = (rod or {}).get("wh_l")
                wh_r = (rod or {}).get("wh_r")
                wh, wh_l, wh_r, _wh_l_pa, _wh_r_pa = fill_whiff_from_cache(
                    pid, wh, wh_l, wh_r, None, None, whiff_cache
                )
                chosen_p = choose_line([
                    summer_pit.get(pid),
                    winter_pit.get(pid),
                    tagged_api_line(api.get("pitching"), "pitching"),
                    confirmed_pit.get(pid),
                ])
                if chosen_p:
                    bf = int(chosen_p.get("bf") or 0)
                    src_p = chosen_p.get("src")
                    pit_k = chosen_p.get("k")
                    pit_gb = chosen_p.get("gb")
                    pit_bb = chosen_p.get("bb")
                    pit_ops = chosen_p.get("ops")
                    pit_avg = chosen_p.get("avg")
                    pit_gs = int(chosen_p.get("gs") or 0)
                else:
                    src_p = "arsenal whiff only" if wh is not None else None
                    bf = 0
                    pit_k = pit_gb = pit_bb = pit_ops = pit_avg = None
                    pit_gs = 0
                pitcher = {
                    "bf": bf,
                    "k": pit_k,
                    "gb": pit_gb,
                    "bb": pit_bb,
                    "ops": pit_ops,
                    "avg": pit_avg,
                    "wh": wh,
                    "wh_l": wh_l,
                    "wh_r": wh_r,
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

    filled_gb = fill_missing_ground_balls(players)
    print(f"  ground balls filled: {filled_gb}")

    # Gaps for the UI footnote / ship report
    hitters = [p for p in players if p["role"] == "H"]
    pitchers = [p for p in players if p["role"] == "P"]
    gaps = {
        "roster_source": "data/lbprc_2025_rosters.json (2026-27 workbooks plus supplied October transactions)",
        "sb_2b_3b_split": "Synergy sb_from_1b / sb_from_2b is 0 for every advance hitter — runner heuristic did not record steals. LBPRC and 2026 summer files only have total SB/CS.",
        "sprint_speed": "No sprint-speed field in the caches. SPD column is blank.",
        "pitcher_platoon": "No vs-LHB / vs-RHB outcome split (K/GB/WH/BB/OPS) in the pitching cache. Both rows repeat the season line.",
        "hitter_platoon_k_gb": "K% and GB% are season totals. WH is the Synergy whiff vs that hand when swings exist, otherwise the hitter's overall whiff. AVG/OPS use the Synergy split only when that side has at least 15 PA.",
        "santurce_synergy": "WH is Synergy whiffs/swings. Players missing from the opposing advance file, including Santurce, use data/card_whiff.json (2026 summer, then LBPRC, then 2025 summer). A player with no swings in that window stays blank.",
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

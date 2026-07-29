#!/usr/bin/env python3
"""
Build Free Agent Pool from MLB Stats (bdfed) AA + AAA 2026 leaders.

Excludes players born in Dominican Republic or Venezuela.
Adds MLB service days (BBRef when debuted, else 0) and prior winter-league flag.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "data"
EXCLUDE_COUNTRIES = {
    "dominican republic",
    "dominican",
    "venezuela",
    "ven",
    "dom",
}
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}
# 11 = AAA, 12 = AA
SPORT_LEVELS = {11: "AAA", 12: "AA"}


def http_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def to_float(v):
    if v is None or v == "" or v in (".---", "-.--", "—.——", "---"):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if s.startswith("."):
        s = "0" + s
    try:
        return float(s)
    except ValueError:
        return None


def ip_to_float(ip) -> float | None:
    """Convert MLB IP string like 12.1 / 12.2 to outs-aware float."""
    if ip is None:
        return None
    if isinstance(ip, (int, float)):
        return float(ip)
    s = str(ip).strip()
    if not s or s in (".---", "-.--"):
        return None
    try:
        if "." in s:
            whole, frac = s.split(".", 1)
            return int(whole) + int(frac) / 3.0
        return float(s)
    except ValueError:
        return None


def fetch_sport_group(season: int, sport_id: int, group: str) -> list[dict]:
    rows: list[dict] = []
    offset, limit = 0, 100
    sort = "onBasePlusSlugging" if group == "hitting" else "inningsPitched"
    while True:
        url = (
            "https://bdfed.stitch.mlbinfra.com/bdfed/stats/player"
            f"?stitch_env=prod&season={season}&sportId={sport_id}&stats=season"
            f"&group={group}&gameType=R&limit={limit}&offset={offset}"
            f"&sortStat={sort}&order=desc&playerPool=ALL"
        )
        d = http_json(url)
        batch = d.get("stats") or []
        for s in batch:
            s["_level"] = SPORT_LEVELS[sport_id]
        rows.extend(batch)
        total = int(d.get("totalSplits") or 0)
        print(f"sport {sport_id} {group}: {offset}+{len(batch)}/{total}")
        offset += limit
        if offset >= total or not batch:
            break
        time.sleep(0.12)
    return rows


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


def fetch_people(ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for batch in chunks(ids, 50):
        ids_s = ",".join(str(i) for i in batch)
        try:
            d = http_json(f"https://statsapi.mlb.com/api/v1/people?personIds={ids_s}")
        except Exception as exc:  # noqa: BLE001
            print("people batch fail", exc)
            time.sleep(1)
            continue
        for p in d.get("people") or []:
            out[int(p["id"])] = p
        time.sleep(0.12)
    return out


def fetch_bbref_service_days(mlbam_id: int, cache: dict) -> int:
    if mlbam_id in cache:
        return int(cache[mlbam_id])
    try:
        from pybaseball import playerid_reverse_lookup
        import requests
        from bs4 import BeautifulSoup
    except Exception:
        cache[mlbam_id] = 0
        return 0
    try:
        df = playerid_reverse_lookup([mlbam_id], key_type="mlbam")
        if df is None or df.empty:
            cache[mlbam_id] = 0
            return 0
        bbref = df.iloc[0].get("key_bbref")
        if not bbref or str(bbref) == "nan":
            cache[mlbam_id] = 0
            return 0
        bbref = str(bbref)
        url = f"https://www.baseball-reference.com/players/{bbref[0]}/{bbref}.shtml"
        resp = requests.get(url, headers=UA, timeout=30)
        if resp.status_code != 200:
            cache[mlbam_id] = 0
            return 0
        soup = BeautifulSoup(resp.text, "html.parser")
        info = soup.find("div", {"id": "info"})
        days = 0
        if info:
            text = info.get_text(" ", strip=True)
            m = re.search(r"[Ss]ervice\s+[Tt]ime[:\s]+([0-9]+)\.([0-9]{3})", text)
            if m:
                days = int(m.group(1)) * 172 + int(m.group(2))
            else:
                m2 = re.search(r"[Ss]ervice\s+[Tt]ime[:\s]+([0-9]+\.[0-9]+)", text)
                if m2:
                    days = int(round(float(m2.group(1)) * 172))
        cache[mlbam_id] = days
        time.sleep(0.55)
        return days
    except Exception as exc:  # noqa: BLE001
        print("service fail", mlbam_id, exc)
        cache[mlbam_id] = 0
        return 0


def normalize_pitcher(s: dict, people: dict, winter_ids: set) -> dict | None:
    mid = int(s["playerId"])
    person = people.get(mid) or {}
    birth = person.get("birthCountry") or ""
    if birth.strip().lower() in EXCLUDE_COUNTRIES:
        return None
    ip = ip_to_float(s.get("inningsPitched"))
    bf = to_float(s.get("battersFaced"))
    so = to_float(s.get("strikeOuts")) or 0
    bb = to_float(s.get("baseOnBalls")) or 0
    k_pct = (so / bf) if bf else None
    bb_pct = (bb / bf) if bf else None
    k_bb_pct = (k_pct - bb_pct) if (k_pct is not None and bb_pct is not None) else to_float(
        s.get("strikeoutsMinusWalksPercentage")
    )
    return {
        "name": s.get("playerFullName") or s.get("playerName"),
        "team": s.get("teamAbbrev"),
        "level": s.get("_level"),
        "league": s.get("leagueName"),
        "mlbam_id": mid,
        "birth_country": birth or "—",
        "mlb_service_days": 0,
        "international": mid in winter_ids,
        "mlb_debut": person.get("mlbDebutDate"),
        "ip": round(ip, 1) if ip is not None else None,
        "k_pct": k_pct,
        "bb_pct": bb_pct,
        "k_bb_pct": k_bb_pct,
        "k_bb": to_float(s.get("strikeoutWalkRatio")) or to_float(s.get("strikesoutsToWalks")),
        "strike_pct": to_float(s.get("strikePercentage")),
        "babip": to_float(s.get("babip")),
        "swstr": to_float(s.get("whiffPercentage")),
        "era": to_float(s.get("era")),
        "whip": to_float(s.get("whip")),
        "k9": to_float(s.get("strikeoutsPer9Inn")) or to_float(s.get("strikeoutsPer9")),
        "bb9": to_float(s.get("walksPer9Inn")) or to_float(s.get("baseOnBallsPer9")),
        "gb_pct": None,
        "fb_pct": to_float(s.get("flyBallPercentage")),
        "avg_against": to_float(s.get("avg")),
    }


def normalize_hitter(s: dict, people: dict, winter_ids: set) -> dict | None:
    mid = int(s["playerId"])
    person = people.get(mid) or {}
    birth = person.get("birthCountry") or ""
    if birth.strip().lower() in EXCLUDE_COUNTRIES:
        return None
    pa = to_float(s.get("plateAppearances"))
    so = to_float(s.get("strikeOuts")) or 0
    bb = to_float(s.get("baseOnBalls")) or 0
    k_pct = (so / pa) if pa else to_float(s.get("strikeoutsPerPlateAppearance"))
    bb_pct = (bb / pa) if pa else to_float(s.get("walksPerPlateAppearance"))
    swings = to_float(s.get("totalSwings"))
    misses = to_float(s.get("swingAndMisses"))
    swstr = (misses / swings) if swings else None
    return {
        "name": s.get("playerFullName") or s.get("playerName"),
        "team": s.get("teamAbbrev"),
        "level": s.get("_level"),
        "league": s.get("leagueName"),
        "position": s.get("positionAbbrev") or s.get("primaryPositionAbbrev"),
        "mlbam_id": mid,
        "birth_country": birth or "—",
        "mlb_service_days": 0,
        "international": mid in winter_ids,
        "mlb_debut": person.get("mlbDebutDate"),
        "pa": int(pa) if pa is not None else None,
        "avg": to_float(s.get("avg")),
        "obp": to_float(s.get("obp")),
        "slg": to_float(s.get("slg")),
        "ops": to_float(s.get("ops")),
        "iso": to_float(s.get("iso")),
        "babip": to_float(s.get("babip")),
        "k_pct": k_pct,
        "bb_pct": bb_pct,
        "bb_k": to_float(s.get("walksPerStrikeout")),
        "swstr": swstr,
        "hr": to_float(s.get("homeRuns")),
        "sb": to_float(s.get("stolenBases")),
    }


def enrich_service(rows: list, service_cache: dict) -> None:
    need = [r for r in rows if r.get("mlb_debut") and r.get("mlbam_id")]
    print(f"service lookups needed: {len(need)}")
    for i, r in enumerate(need, 1):
        mid = int(r["mlbam_id"])
        r["mlb_service_days"] = fetch_bbref_service_days(mid, service_cache)
        if i % 25 == 0:
            print(f"  service {i}/{len(need)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--skip-service", action="store_true", help="Leave service days at 0/cache only")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    winter_path = OUT_DIR / "winter_league_player_ids.json"
    winter_ids = set(json.loads(winter_path.read_text())) if winter_path.exists() else set()

    raw_pitch, raw_hit = [], []
    for sport_id in (11, 12):
        raw_pitch.extend(fetch_sport_group(args.season, sport_id, "pitching"))
        raw_hit.extend(fetch_sport_group(args.season, sport_id, "hitting"))

    ids = sorted({int(r["playerId"]) for r in raw_pitch + raw_hit if r.get("playerId")})
    print(f"unique ids: {len(ids)}")
    people = fetch_people(ids)
    print(f"people: {len(people)}")

    pitchers = [p for s in raw_pitch if (p := normalize_pitcher(s, people, winter_ids))]
    hitters = [h for s in raw_hit if (h := normalize_hitter(s, people, winter_ids))]

    pitchers = [p for p in pitchers if (p.get("ip") or 0) >= 15]
    hitters = [h for h in hitters if (h.get("pa") or 0) >= 60]
    pitchers.sort(key=lambda r: (r.get("k_bb_pct") is not None, r.get("k_bb_pct") or -99), reverse=True)
    hitters.sort(key=lambda r: (r.get("ops") is not None, r.get("ops") or -99), reverse=True)

    svc_cache_path = OUT_DIR / "service_days_cache.json"
    service_cache = (
        {int(k): int(v) for k, v in json.loads(svc_cache_path.read_text()).items()}
        if svc_cache_path.exists()
        else {}
    )
    if not args.skip_service:
        enrich_service(pitchers + hitters, service_cache)
        svc_cache_path.write_text(json.dumps({str(k): v for k, v in service_cache.items()}))
    else:
        for r in pitchers + hitters:
            mid = r.get("mlbam_id")
            if mid and mid in service_cache:
                r["mlb_service_days"] = service_cache[mid]

    out = {
        "season": args.season,
        "levels": ["AAA", "AA"],
        "excluded_birth_countries": ["Dominican Republic", "Venezuela"],
        "note": "DR/VEN born players removed (likely already on Latin winter clubs). Service days from BBRef when available.",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pitchers": pitchers,
        "hitters": hitters,
    }
    path = OUT_DIR / "free_agent_pool.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {path} — pitchers {len(pitchers)}, hitters {len(hitters)}")


if __name__ == "__main__":
    main()

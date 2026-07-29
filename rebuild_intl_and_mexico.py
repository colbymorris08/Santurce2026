#!/usr/bin/env python3
"""
Rebuild Free Agent Pool international flags + add 2026 Mexican League pitchers.

International = True only if the player has MLB Stats rows in:
  - Caribbean / Latin winter leagues (LIDOM, LMP, LVBP, PWL, Caribbean Series)
  - Mexican League (summer MEX, sportId 23)
NOT Arizona Fall League (AFL) or Australian Baseball League (ABL).
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}
EXCLUDE_COUNTRIES = {
    "dominican republic",
    "dominican",
    "venezuela",
    "ven",
    "dom",
}
# Winter sport 17 leagueIds that count as international (exclude AFL=119, ABL=595)
INTL_WINTER_LEAGUE_IDS = {131, 132, 133, 135, 162}  # LIDOM, LMP, PWL, LVBP, CS
MEXICO_SPORT = 23


def http_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def to_float(v):
    if v is None or v == "" or v in (".---", "-.--"):
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


def fetch_bdfed(season: int, sport_id: int, group: str) -> list[dict]:
    rows: list[dict] = []
    offset, limit = 0, 100
    sort = "inningsPitched" if group == "pitching" else "onBasePlusSlugging"
    while True:
        url = (
            "https://bdfed.stitch.mlbinfra.com/bdfed/stats/player"
            f"?stitch_env=prod&season={season}&sportId={sport_id}&stats=season"
            f"&group={group}&gameType=R&limit={limit}&offset={offset}"
            f"&sortStat={sort}&order=desc&playerPool=ALL"
        )
        d = http_json(url)
        batch = d.get("stats") or []
        rows.extend(batch)
        total = int(d.get("totalSplits") or 0)
        offset += limit
        if offset >= total or not batch:
            break
        time.sleep(0.1)
    return rows


def collect_intl_ids() -> set[int]:
    ids: set[int] = set()
    # Latin winter leagues (not AFL/ABL)
    for year in range(2018, 2027):
        for group in ("pitching", "hitting"):
            try:
                rows = fetch_bdfed(year, 17, group)
            except Exception as exc:  # noqa: BLE001
                print("winter fail", year, group, exc)
                continue
            n = 0
            for s in rows:
                lid = int(s.get("leagueId") or 0)
                if lid not in INTL_WINTER_LEAGUE_IDS:
                    continue
                pid = s.get("playerId")
                if not pid:
                    continue
                # require real playing time
                ip = ip_to_float(s.get("inningsPitched")) or 0
                pa = to_float(s.get("plateAppearances")) or 0
                g = to_float(s.get("gamesPitched") or s.get("gamesPlayed")) or 0
                if group == "pitching" and ip <= 0 and g <= 0:
                    continue
                if group == "hitting" and pa <= 0 and g <= 0:
                    continue
                ids.add(int(pid))
                n += 1
            print(f"winter {year} {group}: kept {n}/{len(rows)}")
    # Mexico summer (LMB)
    for year in range(2018, 2027):
        for group in ("pitching", "hitting"):
            try:
                rows = fetch_bdfed(year, MEXICO_SPORT, group)
            except Exception as exc:  # noqa: BLE001
                print("mexico fail", year, group, exc)
                continue
            for s in rows:
                pid = s.get("playerId")
                if not pid:
                    continue
                ip = ip_to_float(s.get("inningsPitched")) or 0
                pa = to_float(s.get("plateAppearances")) or 0
                if group == "pitching" and ip <= 0:
                    continue
                if group == "hitting" and pa <= 0:
                    continue
                ids.add(int(pid))
            print(f"mexico {year} {group}: {len(rows)}")
    return ids


def fetch_people(ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        try:
            d = http_json(
                "https://statsapi.mlb.com/api/v1/people?personIds="
                + ",".join(map(str, batch))
            )
            for p in d.get("people") or []:
                out[int(p["id"])] = p
        except Exception as exc:  # noqa: BLE001
            print("people fail", exc)
        time.sleep(0.1)
    return out


def normalize_mexico_pitcher(s: dict, people: dict, intl_ids: set[int]) -> dict | None:
    mid = int(s["playerId"])
    person = people.get(mid) or {}
    birth = (person.get("birthCountry") or "").strip()
    if birth.lower() in EXCLUDE_COUNTRIES:
        return None
    ip = ip_to_float(s.get("inningsPitched"))
    if (ip or 0) < 10:
        return None
    bf = to_float(s.get("battersFaced"))
    so = to_float(s.get("strikeOuts")) or 0
    bb = to_float(s.get("baseOnBalls")) or 0
    k_pct = (so / bf) if bf else None
    bb_pct = (bb / bf) if bf else None
    k_bb_pct = (k_pct - bb_pct) if (k_pct is not None and bb_pct is not None) else None
    return {
        "name": s.get("playerFullName") or s.get("playerName"),
        "team": s.get("teamAbbrev"),
        "level": "MEX",
        "league": s.get("leagueName") or "MEX",
        "mlbam_id": mid,
        "birth_country": birth or "—",
        "mlb_service_days": 0,
        "international": True,  # Mexico = international by definition
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
        "source": "Mexican League 2026",
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("Collecting international IDs (winter Latin + Mexico)…")
    intl_ids = collect_intl_ids()
    (OUT / "winter_league_player_ids.json").write_text(json.dumps(sorted(intl_ids)))
    print(f"Wrote intl ids: {len(intl_ids)}")
    print("Diamond", 682981 in intl_ids, "Alford", 688230 in intl_ids)

    fa_path = OUT / "free_agent_pool.json"
    fa = json.loads(fa_path.read_text())

    # Re-flag existing pool
    for group in ("pitchers", "hitters"):
        for r in fa.get(group) or []:
            mid = r.get("mlbam_id")
            r["international"] = bool(mid and int(mid) in intl_ids)

    # Add Mexico 2026 pitchers
    print("Fetching Mexico 2026 pitchers…")
    mex_rows = fetch_bdfed(2026, MEXICO_SPORT, "pitching")
    mex_ids = [int(s["playerId"]) for s in mex_rows if s.get("playerId")]
    people = fetch_people(sorted(set(mex_ids)))
    existing = {int(p["mlbam_id"]) for p in fa.get("pitchers") or [] if p.get("mlbam_id")}
    added = 0
    for s in mex_rows:
        row = normalize_mexico_pitcher(s, people, intl_ids)
        if not row:
            continue
        mid = int(row["mlbam_id"])
        if mid in existing:
            # already in AA/AAA pool — mark international True, keep MiLB row
            for p in fa["pitchers"]:
                if p.get("mlbam_id") == mid:
                    p["international"] = True
                    p["mexico_2026"] = True
            continue
        fa["pitchers"].append(row)
        existing.add(mid)
        added += 1

    # Preserve service days already enriched where present
    fa["pitchers"].sort(
        key=lambda r: (r.get("k_bb_pct") is not None, r.get("k_bb_pct") or -99),
        reverse=True,
    )
    fa["intl_definition"] = (
        "Y if player has LIDOM/LMP/LVBP/PWL/CS winter stats or Mexican League (MEX) stats; "
        "AFL and ABL do not count."
    )
    fa["levels"] = ["AAA", "AA", "MEX"]
    fa["generated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    fa_path.write_text(json.dumps(fa, indent=2))

    # sanity
    def flag(name):
        for p in fa["pitchers"]:
            if name.lower() in (p.get("name") or "").lower():
                return p.get("name"), p.get("international"), p.get("level")
        return None

    print("sanity Diamond", flag("Derek Diamond"))
    print("sanity Alford", flag("Peyton Alford"))
    yn = sum(1 for p in fa["pitchers"] + fa["hitters"] if p.get("international"))
    print(
        f"Updated FA pool — pitchers {len(fa['pitchers'])} (+{added} MEX), "
        f"hitters {len(fa['hitters'])}, intl Y={yn}"
    )


if __name__ == "__main__":
    main()

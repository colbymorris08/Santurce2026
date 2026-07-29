#!/usr/bin/env python3
"""
Lineup-spot success for Santurce hitters:
  LIDOM 2025 + recent MLB/AAA/AA (game logs → boxscores), parallelized.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
UA = {"User-Agent": "SanturceAnalytics/1.0"}
FOCUS_TEAM_ID = 685
# Keep recent only so build finishes in minutes
LEVELS = {
    1: ("MLB", [2024, 2025]),
    11: ("AAA", [2025, 2026]),
    12: ("AA", [2025, 2026]),
}


def http_json(url: str, timeout: float = 45.0):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def lineup_slot(batting_order) -> int | None:
    if batting_order is None:
        return None
    try:
        spot = int(str(batting_order).strip()[0])
        return spot if 1 <= spot <= 9 else None
    except (TypeError, ValueError, IndexError):
        return None


def empty_counter():
    return {"games": 0, "AB": 0, "H": 0, "doubles": 0, "hr": 0, "rbi": 0, "BB": 0, "SO": 0, "PA": 0, "triples": 0}


def add_bat(row, bat: dict) -> bool:
    ab = int(bat.get("atBats") or 0)
    pa = int(bat.get("plateAppearances") or 0)
    if ab == 0 and pa == 0:
        return False
    row["games"] += 1
    row["AB"] += ab
    row["H"] += int(bat.get("hits") or 0)
    row["doubles"] += int(bat.get("doubles") or 0)
    row["triples"] += int(bat.get("triples") or 0)
    row["hr"] += int(bat.get("homeRuns") or 0)
    row["rbi"] += int(bat.get("rbi") or 0)
    row["BB"] += int(bat.get("baseOnBalls") or 0)
    row["SO"] += int(bat.get("strikeOuts") or 0)
    row["PA"] += pa
    return True


def build_lidom(names: dict[int, str], agg) -> None:
    d = http_json(
        f"https://statsapi.mlb.com/api/v1/schedule?sportId=17&season=2025&teamId={FOCUS_TEAM_ID}&gameTypes=R"
    )
    pks = sorted(
        {
            int(g["gamePk"])
            for day in d.get("dates") or []
            for g in day.get("games") or []
            if g.get("gamePk")
        }
    )
    print(f"LIDOM games {len(pks)}")
    for i, pk in enumerate(pks, 1):
        try:
            box = http_json(f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore")
        except Exception as exc:  # noqa: BLE001
            print("lidom fail", pk, exc)
            continue
        for side in ("home", "away"):
            team = box["teams"][side]
            if int((team.get("team") or {}).get("id") or 0) != FOCUS_TEAM_ID:
                continue
            for _k, p in (team.get("players") or {}).items():
                pid = (p.get("person") or {}).get("id")
                if not pid or int(pid) not in names:
                    continue
                spot = lineup_slot(p.get("battingOrder"))
                if not spot:
                    continue
                add_bat(agg[(int(pid), "LIDOM", 2025, spot)], ((p.get("stats") or {}).get("batting")) or {})
        if i % 20 == 0:
            print(f"  lidom {i}/{len(pks)}")
        time.sleep(0.04)


def fetch_game_logs(pid: int, name: str):
    rows = []
    for sport_id, (level, seasons) in LEVELS.items():
        for season in seasons:
            url = (
                f"https://statsapi.mlb.com/api/v1/people/{pid}/stats"
                f"?stats=gameLog&group=hitting&season={season}&sportId={sport_id}"
            )
            try:
                d = http_json(url)
            except Exception:
                continue
            splits = ((d.get("stats") or [{}])[0].get("splits") or [])
            if splits:
                print(f"  {name} {level} {season}: {len(splits)}")
            for s in splits:
                pk = (s.get("game") or {}).get("gamePk")
                if pk:
                    rows.append((int(pk), pid, level, int(season)))
            time.sleep(0.05)
    return rows


def fetch_one_box(pk: int, want: list[tuple[int, str, int]]):
    """Return list of (pid, level, season, spot, bat_dict)."""
    try:
        box = http_json(f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore")
    except Exception:
        return []
    found = []
    want_ids = {t[0] for t in want}
    for side in ("home", "away"):
        for _k, p in ((box.get("teams") or {}).get(side, {}).get("players") or {}).items():
            pid = (p.get("person") or {}).get("id")
            if not pid or int(pid) not in want_ids:
                continue
            spot = lineup_slot(p.get("battingOrder"))
            if not spot:
                continue
            bat = ((p.get("stats") or {}).get("batting")) or {}
            for t in want:
                if t[0] == int(pid):
                    found.append((t[0], t[1], t[2], spot, bat))
    return found


def finalize(agg, names):
    out = []
    for (pid, level, season, spot), c in sorted(agg.items(), key=lambda x: (x[0][1], names.get(x[0][0], ""), x[0][2], x[0][3])):
        ab, h = c["AB"], c["H"]
        tb = h + c["doubles"] + 2 * c["triples"] + 3 * c["hr"]
        src = {
            "LIDOM": "LIDOM boxscores",
            "MLB": "MLB game logs → boxscores",
            "AAA": "AAA game logs → boxscores",
            "AA": "AA game logs → boxscores",
        }.get(level, level)
        out.append(
            {
                "name": names.get(pid, str(pid)),
                "player_id": pid,
                "level": level,
                "season": season,
                "lineup_spot": spot,
                "games": c["games"],
                "PA": c["PA"],
                "AB": ab,
                "H": h,
                "doubles": c["doubles"],
                "triples": c["triples"],
                "HR": c["hr"],
                "RBI": c["rbi"],
                "BB": c["BB"],
                "SO": c["SO"],
                "AVG": round(h / ab, 3) if ab else None,
                "SLG": round(tb / ab, 3) if ab else None,
                "source": src,
            }
        )
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rosters = json.loads((OUT / "lidom_2025_rosters.json").read_text())
    names = {
        int(p["id"]): p["name"]
        for p in rosters["685"]["players"]
        if p.get("position") != "P" and p.get("id")
    }
    print(f"Santurce hitters: {len(names)}")
    agg = defaultdict(empty_counter)

    print("LIDOM…")
    build_lidom(names, agg)

    print("Collecting recent MLB/AAA/AA game logs…")
    mapping = defaultdict(list)
    for pid, name in names.items():
        for pk, p2, level, season in fetch_game_logs(pid, name):
            mapping[pk].append((p2, level, season))

    pks = list(mapping.keys())
    print(f"unique pro games: {len(pks)}")

    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_one_box, pk, mapping[pk]): pk for pk in pks}
        for fut in as_completed(futs):
            for pid, level, season, spot, bat in fut.result():
                add_bat(agg[(pid, level, season, spot)], bat)
            done += 1
            if done % 100 == 0:
                print(f"  boxes {done}/{len(pks)}")

    out = finalize(agg, names)
    path = OUT / "pregame_lineup_spots.json"
    path.write_text(json.dumps(out, indent=2))
    by = defaultdict(int)
    for r in out:
        by[r["level"]] += 1
    print(f"Wrote {path} rows={len(out)} {dict(by)}")


if __name__ == "__main__":
    main()

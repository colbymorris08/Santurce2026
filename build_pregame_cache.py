#!/usr/bin/env python3
"""
Build Pregame Analytics caches for Cangrejeros de Santurce (advance scouting reports).

Outputs in data/:
  pregame_lineup_spots.json
  pregame_pitcher_vs_batter.json
  pregame_hitter_vs_pitcher.json
  pregame_baserunning_bunting.json
  pregame_opposing_stuff.json
  pregame_spray_charts.json
  lidom_pitcher_stuff.json
  free_agent_pool.json  (enriched with stuff_plus when available)
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
UA = {"User-Agent": "SanturceAnalytics/1.0"}
FOCUS_TEAM_ID = 685
TEAM_ABBREV = {
    685: "SAN",
    686: "CAG",
    687: "CAR",
    688: "MAY",
    689: "PON",
    4087: "SJU",
}
LBPRC_TEAMS = {
    685: "Cangrejeros de Santurce",
    686: "Criollos de Caguas",
    687: "Gigantes de Carolina",
    688: "Indios de Mayaguez",
    689: "Leones de Ponce",
    4087: "Senadores de San Juan",
}
PS_BASE = "https://oriolebird.pythonanywhere.com"


def http_json(url: str, timeout: float = 60.0):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def to_float(v):
    if v is None or v in ("", ".---", "-.--"):
        return None
    try:
        s = str(v).strip()
        if s.startswith("."):
            s = "0" + s
        return float(s)
    except ValueError:
        return None


def lineup_slot(batting_order) -> int | None:
    if batting_order is None:
        return None
    try:
        return int(str(batting_order)[0])
    except (TypeError, ValueError):
        return None


def fetch_focus_game_pks(season: int = 2025) -> list[int]:
    d = http_json(
        f"https://statsapi.mlb.com/api/v1/schedule?sportId=17&season={season}"
        f"&teamId={FOCUS_TEAM_ID}&gameTypes=R"
    )
    pks = []
    for day in d.get("dates") or []:
        for g in day.get("games") or []:
            if g.get("gamePk"):
                pks.append(int(g["gamePk"]))
    return sorted(set(pks))


def build_lineup_spots(pks: list[int], focus_hitters: dict[int, str]) -> list[dict]:
    """Per Licey hitter × lineup spot aggregates from LIDOM boxscores."""
    # (player_id, spot) -> counters
    agg = defaultdict(lambda: {"games": 0, "AB": 0, "H": 0, "doubles": 0, "hr": 0, "rbi": 0, "BB": 0, "SO": 0, "PA": 0})
    for i, pk in enumerate(pks, 1):
        try:
            box = http_json(f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore")
        except Exception as exc:  # noqa: BLE001
            print("box fail", pk, exc)
            continue
        for side in ("home", "away"):
            team = box["teams"][side]
            if int((team.get("team") or {}).get("id") or 0) != FOCUS_TEAM_ID:
                continue
            for _key, p in (team.get("players") or {}).items():
                person = p.get("person") or {}
                pid = person.get("id")
                if not pid or int(pid) not in focus_hitters:
                    continue
                spot = lineup_slot(p.get("battingOrder"))
                if not spot:
                    continue
                bat = ((p.get("stats") or {}).get("batting")) or {}
                ab = int(bat.get("atBats") or 0)
                if ab == 0 and int(bat.get("plateAppearances") or 0) == 0:
                    continue
                row = agg[(int(pid), spot)]
                row["games"] += 1
                row["AB"] += ab
                row["H"] += int(bat.get("hits") or 0)
                row["doubles"] += int(bat.get("doubles") or 0)
                row["hr"] += int(bat.get("homeRuns") or 0)
                row["rbi"] += int(bat.get("rbi") or 0)
                row["BB"] += int(bat.get("baseOnBalls") or 0)
                row["SO"] += int(bat.get("strikeOuts") or 0)
                row["PA"] += int(bat.get("plateAppearances") or 0)
        if i % 15 == 0:
            print(f"  lineup boxscores {i}/{len(pks)}")
        time.sleep(0.08)

    out = []
    for (pid, spot), c in sorted(agg.items()):
        ab = c["AB"]
        h = c["H"]
        tb = h + c["doubles"] + 2 * c["hr"]  # rough (missing 3B)
        out.append(
            {
                "name": focus_hitters[pid],
                "player_id": pid,
                "lineup_spot": spot,
                "games": c["games"],
                "PA": c["PA"],
                "AB": ab,
                "H": h,
                "doubles": c["doubles"],
                "HR": c["hr"],
                "RBI": c["rbi"],
                "BB": c["BB"],
                "SO": c["SO"],
                "AVG": round(h / ab, 3) if ab else None,
                "SLG": round(tb / ab, 3) if ab else None,
                "source": "LIDOM 2025 boxscores",
            }
        )
    return out


def build_pitcher_vs_batter(
    pks: list[int],
    focus_pitchers: dict[int, str],
    opp_hitters: dict[int, dict],
) -> list[dict]:
    """Santurce pitchers vs opposing LIDOM hitters from live feeds."""
    agg = defaultdict(lambda: {"PA": 0, "AB": 0, "H": 0, "doubles": 0, "HR": 0, "BB": 0, "SO": 0, "TB": 0})
    for i, pk in enumerate(pks, 1):
        try:
            feed = http_json(f"https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live")
        except Exception as exc:  # noqa: BLE001
            print("feed fail", pk, exc)
            continue
        plays = ((feed.get("liveData") or {}).get("plays") or {}).get("allPlays") or []
        for play in plays:
            m = play.get("matchup") or {}
            pit = m.get("pitcher") or {}
            bat = m.get("batter") or {}
            pid = pit.get("id")
            bid = bat.get("id")
            if not pid or int(pid) not in focus_pitchers:
                continue
            if not bid or int(bid) not in opp_hitters:
                continue
            result = play.get("result") or {}
            et = result.get("eventType") or result.get("type") or ""
            # skip non-PA events
            if et in ("stolen_base_2b", "stolen_base_3b", "caught_stealing_2b", "caught_stealing_3b", "pickoff", "balk", "wild_pitch", "passed_ball"):
                continue
            row = agg[(int(pid), int(bid))]
            row["PA"] += 1
            desc = (result.get("event") or "").lower()
            if "walk" in desc or et == "walk" or et == "intent_walk":
                row["BB"] += 1
            elif "strikeout" in desc or et == "strikeout":
                row["SO"] += 1
                row["AB"] += 1
            elif et in ("hit_by_pitch", "sac_bunt", "sac_fly", "catcher_interf"):
                pass
            else:
                row["AB"] += 1
                if et == "single":
                    row["H"] += 1
                    row["TB"] += 1
                elif et == "double":
                    row["H"] += 1
                    row["doubles"] += 1
                    row["TB"] += 2
                elif et == "triple":
                    row["H"] += 1
                    row["TB"] += 3
                elif et == "home_run":
                    row["H"] += 1
                    row["HR"] += 1
                    row["TB"] += 4
        if i % 15 == 0:
            print(f"  matchup feeds {i}/{len(pks)}")
        time.sleep(0.08)

    out = []
    for (pid, bid), c in sorted(agg.items(), key=lambda x: -x[1]["PA"]):
        ab = c["AB"]
        meta = opp_hitters[bid]
        out.append(
            {
                "pitcher_name": focus_pitchers[pid],
                "pitcher_id": pid,
                "hitter_name": meta["name"],
                "hitter_id": bid,
                "hitter_team": meta.get("team") or "",
                "hitter_team_name": meta.get("team_name") or "",
                "PA": c["PA"],
                "AB": ab,
                "H": c["H"],
                "doubles": c["doubles"],
                "HR": c["HR"],
                "BB": c["BB"],
                "SO": c["SO"],
                "AVG": round(c["H"] / ab, 3) if ab else None,
                "SLG": round(c["TB"] / ab, 3) if ab else None,
                "source": "LIDOM 2025 play-by-play",
            }
        )
    return out


def build_hitter_vs_pitcher(
    pks: list[int],
    focus_hitters: dict[int, str],
    opp_pitchers: dict[int, dict],
) -> list[dict]:
    """Santurce hitters vs opposing LIDOM pitchers from live feeds."""
    agg = defaultdict(lambda: {"PA": 0, "AB": 0, "H": 0, "doubles": 0, "HR": 0, "BB": 0, "SO": 0, "TB": 0})
    for i, pk in enumerate(pks, 1):
        try:
            feed = http_json(f"https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live")
        except Exception as exc:  # noqa: BLE001
            print("feed fail", pk, exc)
            continue
        plays = ((feed.get("liveData") or {}).get("plays") or {}).get("allPlays") or []
        for play in plays:
            m = play.get("matchup") or {}
            pit = m.get("pitcher") or {}
            bat = m.get("batter") or {}
            pid = pit.get("id")
            bid = bat.get("id")
            if not bid or int(bid) not in focus_hitters:
                continue
            if not pid or int(pid) not in opp_pitchers:
                continue
            result = play.get("result") or {}
            et = result.get("eventType") or result.get("type") or ""
            if et in ("stolen_base_2b", "stolen_base_3b", "caught_stealing_2b", "caught_stealing_3b", "pickoff", "balk", "wild_pitch", "passed_ball"):
                continue
            row = agg[(int(bid), int(pid))]
            row["PA"] += 1
            desc = (result.get("event") or "").lower()
            if "walk" in desc or et == "walk" or et == "intent_walk":
                row["BB"] += 1
            elif "strikeout" in desc or et == "strikeout":
                row["SO"] += 1
                row["AB"] += 1
            elif et in ("hit_by_pitch", "sac_bunt", "sac_fly", "catcher_interf"):
                pass
            else:
                row["AB"] += 1
                if et == "single":
                    row["H"] += 1
                    row["TB"] += 1
                elif et == "double":
                    row["H"] += 1
                    row["doubles"] += 1
                    row["TB"] += 2
                elif et == "triple":
                    row["H"] += 1
                    row["TB"] += 3
                elif et == "home_run":
                    row["H"] += 1
                    row["HR"] += 1
                    row["TB"] += 4
        if i % 15 == 0:
            print(f"  hvp feeds {i}/{len(pks)}")
        time.sleep(0.08)

    out = []
    for (bid, pid), c in sorted(agg.items(), key=lambda x: -x[1]["PA"]):
        ab = c["AB"]
        meta = opp_pitchers[pid]
        out.append(
            {
                "hitter_name": focus_hitters[bid],
                "hitter_id": bid,
                "pitcher_name": meta["name"],
                "pitcher_id": pid,
                "pitcher_team": meta.get("team") or "",
                "pitcher_team_name": meta.get("team_name") or "",
                "PA": c["PA"],
                "AB": ab,
                "H": c["H"],
                "doubles": c["doubles"],
                "HR": c["HR"],
                "BB": c["BB"],
                "SO": c["SO"],
                "AVG": round(c["H"] / ab, 3) if ab else None,
                "SLG": round(c["TB"] / ab, 3) if ab else None,
                "source": "LIDOM 2025 play-by-play",
            }
        )
    return out


def build_baserunning_bunting(hitting_rows: list[dict], opp_ids: set[int], people: dict) -> list[dict]:
    """Opposing LIDOM hitters BR/bunt rates; AAA Prospect Savant sprint/bunt proxy when thin."""
    out = []
    for s in hitting_rows:
        pid = int(s.get("playerId") or 0)
        if pid not in opp_ids or int(s.get("teamId") or 0) == FOCUS_TEAM_ID:
            continue
        pa = to_float(s.get("plateAppearances")) or 0
        sb = to_float(s.get("stolenBases")) or 0
        cs = to_float(s.get("caughtStealing")) or 0
        sac = to_float(s.get("sacBunts")) or 0
        attempts = sb + cs
        out.append(
            {
                "name": s.get("playerFullName") or s.get("playerName"),
                "player_id": pid,
                "team": s.get("teamAbbrev"),
                "PA": int(pa),
                "SB": int(sb),
                "CS": int(cs),
                "SB_pct": round(sb / attempts, 3) if attempts else None,
                "sac_bunts": int(sac),
                "sac_per_pa": round(sac / pa, 4) if pa else None,
                "birth_country": (people.get(pid) or {}).get("birthCountry"),
                "source": "LIDOM 2025 season",
                "aaa_fallback": None,
            }
        )
    out.sort(key=lambda r: (-(r["SB"] or 0), -(r["sac_bunts"] or 0)))
    return out


def enrich_aaa_baserunning(rows: list[dict]) -> None:
    """For low-sample LIDOM hitters, pull Prospect Savant AAA block rates if present."""
    for r in rows:
        if (r.get("PA") or 0) >= 40:
            continue
        pid = r["player_id"]
        try:
            ps = http_json(f"{PS_BASE}/player/{pid}")
        except Exception:
            continue
        if not isinstance(ps, dict):
            continue
        # prefer newest AAA block
        aaa_keys = sorted([k for k in ps if k.endswith("_AAA")], reverse=True)
        if not aaa_keys:
            continue
        blk = ps[aaa_keys[0]]
        r["aaa_fallback"] = {
            "level_key": aaa_keys[0],
            "bb_rate": blk.get("bbrate"),
            "k_rate": blk.get("krate"),
            "pull": blk.get("pull"),
            "oppo_air": blk.get("oppoair"),
            "source": "Prospect Savant AAA",
        }
        time.sleep(0.12)


def fetch_ps_stuff(pid: int, season: int = 2025) -> dict | None:
    try:
        rows = http_json(f"{PS_BASE}/stuff/{pid}/{season}")
    except Exception:
        return None
    if not isinstance(rows, list) or not rows:
        # try prior year
        try:
            rows = http_json(f"{PS_BASE}/stuff/{pid}/{season - 1}")
        except Exception:
            return None
        season = season - 1
    if not isinstance(rows, list) or not rows:
        return None
    total = sum(int(r.get("pitches") or 0) for r in rows)
    if total <= 0:
        return None
    w_stuff = sum(float(r.get("psStuff") or 0) * int(r.get("pitches") or 0) for r in rows) / total
    arsenal = [
        {
            "pitch_type": r.get("pitch_type"),
            "pitch_name": r.get("pitch_name"),
            "ps_stuff": round(float(r.get("psStuff") or 0), 1),
            "velo": round(float(r.get("release_speed") or 0), 1),
            "usage": r.get("usage"),
            "whiff": r.get("swing_miss_percent"),
            "pitches": r.get("pitches"),
        }
        for r in rows
    ]
    return {
        "player_id": pid,
        "season": season,
        "stuff_plus": round(w_stuff, 1),
        "pitches": total,
        "arsenal": arsenal,
        "source": "Prospect Savant",
    }


def build_opposing_stuff(opp_pitchers: dict[int, dict], people: dict) -> list[dict]:
    """Stuff for opposing LIDOM pitchers — PS for AAA/MiLB tracking, Statcast index note for MLB."""
    out = []
    for pid, meta in opp_pitchers.items():
        person = people.get(pid) or {}
        debut = person.get("mlbDebutDate")
        ps = fetch_ps_stuff(pid, 2025)
        row = {
            "name": meta["name"],
            "player_id": pid,
            "team": meta.get("team"),
            "mlb_debut": debut,
            "birth_country": person.get("birthCountry"),
            "stuff_plus": (ps or {}).get("stuff_plus"),
            "pitches": (ps or {}).get("pitches"),
            "arsenal": (ps or {}).get("arsenal") or [],
            "source": (ps or {}).get("source") or ("MLB Statcast (KNCT index) — pending" if debut else "unavailable"),
            "level_hint": "MLB" if debut else "MiLB/AAA",
        }
        out.append(row)
        time.sleep(0.1)
    out.sort(key=lambda r: (r.get("stuff_plus") is not None, r.get("stuff_plus") or 0), reverse=True)
    return out


def build_spray_charts(
    hitter_ids: dict[int, str],
    people: dict,
    team_by_pid: dict[int, str] | None = None,
) -> dict:
    """
    MLB: Statcast hc_x/hc_y with nobody-on / RISP / 2-strike splits.
    AAA: NOT available via Statcast (pybaseball returns empty). Prospect Savant
    only exposes directional rates (pull / oppo air) — stored as proxy.
    """
    mlb_sprays = []
    aaa_proxies = []
    # Limit Statcast pulls to keep build time reasonable
    mlb_ids = [pid for pid, _ in hitter_ids.items() if (people.get(pid) or {}).get("mlbDebutDate")]
    milb_ids = [pid for pid in hitter_ids if pid not in set(mlb_ids)]

    try:
        from pybaseball import statcast_batter
    except Exception:
        statcast_batter = None

    for i, pid in enumerate(mlb_ids[:40], 1):
        if not statcast_batter:
            break
        try:
            df = statcast_batter("2025-03-01", "2025-10-15", pid)
        except Exception as exc:  # noqa: BLE001
            print("statcast fail", pid, exc)
            continue
        if df is None or df.empty or "hc_x" not in df.columns:
            continue
        bip = df[df["hc_x"].notna() & df["hc_y"].notna()].copy()
        if bip.empty:
            continue

        def pack(sub, label):
            hit_map = {
                "home_run": "HR",
                "triple": "3B",
                "double": "2B",
                "single": "1B",
                "field_out": "OUT",
                "force_out": "OUT",
                "grounded_into_double_play": "OUT",
                "double_play": "OUT",
                "triple_play": "OUT",
                "fielders_choice": "OUT",
                "fielders_choice_out": "OUT",
                "sac_fly": "SF",
                "sac_fly_double_play": "SF",
                "sac_bunt": "SAC",
                "field_error": "E",
            }
            pts = []
            for r in sub.itertuples():
                ev = getattr(r, "events", None)
                try:
                    import math as _math

                    if ev is not None and isinstance(ev, float) and _math.isnan(ev):
                        ev = None
                except Exception:
                    pass
                hit = hit_map.get(str(ev), "BIP") if ev else "BIP"
                ls = getattr(r, "launch_speed", None)
                dist = getattr(r, "hit_distance_sc", None)
                try:
                    ls = None if ls is None or (isinstance(ls, float) and str(ls) == "nan") else round(float(ls), 1)
                except Exception:
                    ls = None
                try:
                    dist = None if dist is None or (isinstance(dist, float) and str(dist) == "nan") else round(float(dist), 1)
                except Exception:
                    dist = None
                pts.append(
                    {
                        "x": round(float(r.hc_x), 1),
                        "y": round(float(r.hc_y), 1),
                        "ev": ls,
                        "dist": dist,
                        "hit": hit,
                        "event": str(ev) if ev else None,
                    }
                )
                if len(pts) >= 250:
                    break
            return {"split": label, "n": len(sub), "points": pts}

        nobody = bip[bip["on_1b"].isna() & bip["on_2b"].isna() & bip["on_3b"].isna()]
        risp = bip[bip["on_2b"].notna() | bip["on_3b"].notna()]
        two_k = bip[bip["strikes"] >= 2]
        mlb_sprays.append(
            {
                "name": hitter_ids[pid],
                "player_id": pid,
                "team": (team_by_pid or {}).get(pid),
                "source": "MLB Statcast 2025",
                "splits": [
                    pack(nobody, "nobody_on"),
                    pack(risp, "risp"),
                    pack(two_k, "two_strikes"),
                ],
            }
        )
        print(f"  spray MLB {i}/{min(40, len(mlb_ids))} {hitter_ids[pid]} bip={len(bip)}")
        time.sleep(0.4)

    for pid in milb_ids[:60]:
        try:
            ps = http_json(f"{PS_BASE}/player/{pid}")
        except Exception:
            continue
        if not isinstance(ps, dict):
            continue
        aaa_keys = sorted([k for k in ps if "_AAA" in k], reverse=True)
        if not aaa_keys:
            continue
        blk = ps[aaa_keys[0]]
        aaa_proxies.append(
            {
                "name": hitter_ids[pid],
                "player_id": pid,
                "team": (team_by_pid or {}).get(pid),
                "level_key": aaa_keys[0],
                "source": "Prospect Savant directional rates (not true spray)",
                "pull": blk.get("pull"),
                "pull_air": blk.get("pullair"),
                "oppo_air": blk.get("oppoair"),
                "oppo_gb": blk.get("oppogb"),
                "note": "AAA pitch-by-pitch spray with RISP / 2K splits is not available via Statcast or Prospect Savant public API.",
            }
        )
        time.sleep(0.1)

    def _avg(key: str) -> float | None:
        vals = [float(r[key]) for r in aaa_proxies if r.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "mlb": mlb_sprays,
        "aaa_proxy": aaa_proxies,
        "aaa_averages": {
            "pull": _avg("pull"),
            "pull_air": _avg("pull_air"),
            "oppo_air": _avg("oppo_air"),
        },
        "aaa_spray_available": False,
        "aaa_note": (
            "True spray charts (hc_x/hc_y) with nobody-on / RISP / 2-strike splits are available "
            "for MLB hitters via Statcast. For AAA-only hitters, Statcast returns no rows and "
            "Prospect Savant only provides directional rates (pull / oppo) from their most recent "
            "AAA season — not count or base-state sprays."
        ),
    }


def enrich_pool_and_lidom_stuff(pitching_rows: list[dict], fa_path: Path) -> None:
    """Add stuff_plus to LIDOM pitchers + FA pool pitchers (PS where available)."""
    lidom_stuff = {}
    ids = sorted({int(r["playerId"]) for r in pitching_rows if r.get("playerId")})
    print(f"LIDOM pitcher stuff lookups: {len(ids)}")
    for i, pid in enumerate(ids, 1):
        ps = fetch_ps_stuff(pid, 2025)
        if ps:
            lidom_stuff[str(pid)] = ps
        if i % 25 == 0:
            print(f"  lidom stuff {i}/{len(ids)}")
        time.sleep(0.08)
    (OUT / "lidom_pitcher_stuff.json").write_text(json.dumps(lidom_stuff, indent=2))

    if not fa_path.exists():
        return
    fa = json.loads(fa_path.read_text())
    # Enrich top pitchers by k_bb_pct (limit PS calls)
    pitchers = fa.get("pitchers") or []
    targets = pitchers[:250]
    print(f"FA pool stuff lookups: {len(targets)}")
    for i, p in enumerate(targets, 1):
        mid = p.get("mlbam_id")
        if not mid:
            p["stuff_plus"] = None
            p["stuff_source"] = None
            continue
        # reuse lidom cache if present
        cached = lidom_stuff.get(str(mid))
        if cached:
            p["stuff_plus"] = cached.get("stuff_plus")
            p["stuff_source"] = cached.get("source")
            continue
        ps = fetch_ps_stuff(int(mid), 2026) or fetch_ps_stuff(int(mid), 2025)
        if ps:
            p["stuff_plus"] = ps.get("stuff_plus")
            p["stuff_source"] = ps.get("source")
        else:
            # proxy: scale whiff into ~60-140 band when PS missing
            wh = p.get("swstr")
            p["stuff_plus"] = round(60 + min(1.0, max(0.0, (wh or 0) / 0.45)) * 80, 1) if wh is not None else None
            p["stuff_source"] = "whiff proxy" if wh is not None else None
        if i % 40 == 0:
            print(f"  fa stuff {i}/{len(targets)}")
        time.sleep(0.08)
    for p in pitchers[250:]:
        wh = p.get("swstr")
        p["stuff_plus"] = round(60 + min(1.0, max(0.0, (wh or 0) / 0.45)) * 80, 1) if wh is not None else None
        p["stuff_source"] = "whiff proxy" if wh is not None else None
    fa_path.write_text(json.dumps(fa, indent=2))
    print("Updated free_agent_pool.json with stuff_plus")


def fetch_people(ids: list[int]) -> dict[int, dict]:
    out = {}
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        try:
            d = http_json(
                "https://statsapi.mlb.com/api/v1/people?personIds=" + ",".join(map(str, batch))
            )
            for p in d.get("people") or []:
                out[int(p["id"])] = p
        except Exception as exc:  # noqa: BLE001
            print("people fail", exc)
        time.sleep(0.12)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rosters = json.loads((OUT / "lbprc_2025_rosters.json").read_text())
    hitting = json.loads((OUT / "lbprc_2025_hitting.json").read_text())
    pitching = json.loads((OUT / "lbprc_2025_pitching.json").read_text())

    focus_hitters = {
        int(p["id"]): p["name"]
        for p in rosters["685"]["players"]
        if p.get("position") != "P" and p.get("id")
    }
    focus_pitchers = {
        int(p["id"]): p["name"]
        for p in rosters["685"]["players"]
        if p.get("position") == "P" and p.get("id")
    }
    opp_hitters = {}
    opp_pitchers = {}
    for tid, team in rosters.items():
        if str(tid) == str(FOCUS_TEAM_ID):
            continue
        abbrev = TEAM_ABBREV.get(int(tid), team.get("teamAbbrev") or "")
        for p in team["players"]:
            if not p.get("id"):
                continue
            if p.get("position") == "P":
                opp_pitchers[int(p["id"])] = {
                    "name": p["name"],
                    "team": abbrev,
                    "team_name": team["teamName"],
                }
            else:
                opp_hitters[int(p["id"])] = {
                    "name": p["name"],
                    "team": abbrev,
                    "team_name": team["teamName"],
                }

    all_ids = sorted(set(focus_hitters) | set(focus_pitchers) | set(opp_hitters) | set(opp_pitchers))
    print("fetching people", len(all_ids))
    people = fetch_people(all_ids)

    print("fetching Santurce schedule…")
    pks = fetch_focus_game_pks(2025)
    print("games", len(pks))

    print("building lineup spots…")
    lineup = build_lineup_spots(pks, focus_hitters)
    (OUT / "pregame_lineup_spots.json").write_text(json.dumps(lineup, indent=2))
    print("lineup rows", len(lineup))

    print("building pitcher vs batter…")
    matchups = build_pitcher_vs_batter(pks, focus_pitchers, opp_hitters)
    (OUT / "pregame_pitcher_vs_batter.json").write_text(json.dumps(matchups, indent=2))
    print("matchup rows", len(matchups))

    print("building hitter vs pitcher…")
    hvp = build_hitter_vs_pitcher(pks, focus_hitters, opp_pitchers)
    (OUT / "pregame_hitter_vs_pitcher.json").write_text(json.dumps(hvp, indent=2))
    print("hvp rows", len(hvp))

    print("building baserunning/bunting…")
    br = build_baserunning_bunting(hitting, set(opp_hitters), people)
    enrich_aaa_baserunning(br[:80])
    (OUT / "pregame_baserunning_bunting.json").write_text(json.dumps(br, indent=2))
    print("BR rows", len(br))

    print("building opposing stuff…")
    stuff = build_opposing_stuff(opp_pitchers, people)
    (OUT / "pregame_opposing_stuff.json").write_text(json.dumps(stuff, indent=2))
    print("stuff rows", len(stuff))

    print("building spray charts…")
    # Focus spray on opposing LBPRC hitters (advance scouting targets)
    spray_names = {pid: meta["name"] for pid, meta in opp_hitters.items()}
    spray_teams = {pid: meta["team"] for pid, meta in opp_hitters.items()}
    spray = build_spray_charts(spray_names, people, spray_teams)
    (OUT / "pregame_spray_charts.json").write_text(json.dumps(spray, indent=2))
    print("mlb sprays", len(spray["mlb"]), "aaa proxies", len(spray["aaa_proxy"]))

    print("enriching FA pool + LIDOM pitcher stuff…")
    enrich_pool_and_lidom_stuff(pitching, OUT / "free_agent_pool.json")
    print("DONE")


if __name__ == "__main__":
    main()

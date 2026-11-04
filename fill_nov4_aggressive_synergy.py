#!/usr/bin/env python3
"""Aggressive Synergy/MLB fill for Nov-4 stubs + Murphy/Aponte.

Uses DoH DNS fix, FA-pool / Synergy mlbProfileId resolution, and broad
2025–2026 any-league windows (Pioneer/Showcase/indy OK). Rebuilds one-page.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import synergy_dns_fix  # noqa: F401
import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import ensure_roster_player as erp
import fill_missing_platoon_synergy as fmp
import refresh_roster_synergy as rrs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
REPORT = RUNS / "nov4_aggressive_fill_report.json"

# Known Synergy / MLB overrides when search is ambiguous
FORCED = {
    ("PON", "ryanmurphy"): {"mlb_id": 694885, "synergy_id": "5e31bc4696adda46f4f954da"},
    ("PON", "johnaponte"): {"mlb_id": None, "synergy_id": "616f22089b4397faf0dc0052"},
}

NAME_VARIANTS = {
    "juan javier echavarria": ["Juan Echavarria", "Javier Echavarria", "Juan Javier"],
    "will bush": ["William Bush", "Will Bush"],
    "hemmanuel rosario": ["Emmanuel Rosario", "Manuel Rosario", "Hemmanuel Rosario"],
    "jose rivera": ["Jose A Rivera", "Jose Alberto Rivera", "J Rivera"],
    "luis ramos": ["Luis A Ramos", "Luis Ramos"],
    "jose acosta": ["Joseph Acosta", "Jose Daniel Acosta", "Jose D Acosta"],
}


def want_players() -> list[dict]:
    added = json.loads((DATA / "roster_official_2026_11_04_added.json").read_text())["added"]
    keys = {(a["team"], rrs.norm(a["name"])) for a in added}
    for name in ("Hemmanuel Rosario", "Jose Rivera", "Ryan Murphy", "John Aponte"):
        keys.add(("PON", rrs.norm(name)))
    hitters, pitchers = rrs.roster_players()
    out = []
    for kind, rows in (("hitter", hitters), ("pitcher", pitchers)):
        for row in rows:
            if (row["team"], rrs.norm(row["name"])) in keys:
                out.append({**row, "kind": kind})
    out.sort(key=lambda r: (r["team"], r["kind"], r["name"]))
    return out


def rewrite_roster_mlb(name: str, team: str, mlb_id: int) -> None:
    rost = json.loads((DATA / "lbprc_2025_rosters.json").read_text())
    want = ligapr.norm_name(name)
    for pack in rost.values():
        if pack.get("teamAbbrev") != team:
            continue
        for p in pack.get("players") or []:
            if ligapr.norm_name(p.get("name") or "") == want:
                old = int(p.get("id") or 0)
                if old != mlb_id:
                    print(f"  roster id {old} → {mlb_id}")
                    p["id"] = mlb_id
                    (DATA / "lbprc_2025_rosters.json").write_text(
                        json.dumps(rost, indent=2, ensure_ascii=False) + "\n"
                    )
                    if old < 0:
                        from add_ponce_official_nov4 import _retarget_caches

                        _retarget_caches(
                            name,
                            old,
                            {
                                "name": name,
                                "id": mlb_id,
                                "team": team,
                                "throws": p.get("throws"),
                                "jersey": p.get("jersey"),
                                "position": p.get("position"),
                            },
                        )
                return


def resolve_mlb(player: dict) -> int | None:
    mid = player.get("mlb_id")
    if mid and int(mid) > 0:
        return int(mid)
    forced = FORCED.get((player["team"], rrs.norm(player["name"])))
    if forced and forced.get("mlb_id"):
        return int(forced["mlb_id"])
    fa = erp.lookup_mlb_id_from_fa(player["name"], pitcher=player["kind"] == "pitcher")
    if fa:
        return fa
    return None


def resolve_sid(client: rrs.Synergy, player: dict) -> tuple[str, dict | None, list]:
    forced = FORCED.get((player["team"], rrs.norm(player["name"])))
    if forced and forced.get("synergy_id"):
        return str(forced["synergy_id"]), {"id": forced["synergy_id"], "name": player["name"]}, []

    names = [player["name"]] + NAME_VARIANTS.get(rrs.norm(player["name"]), [])
    rrs.NAME_VARIANTS[rrs.norm(player["name"])] = NAME_VARIANTS.get(rrs.norm(player["name"]), [])
    sid, matched, rows = client.search(player)
    if sid:
        return str(sid), matched, rows
    for alt in names[1:]:
        sid, matched, rows = client.search({**player, "name": alt})
        if sid:
            return str(sid), matched, rows
    # Prefer unique mlb-bearing exact match
    want = {rrs.norm(n) for n in names}
    exact = [r for r in rows if rrs.norm(r.get("name") or "") in want]
    with_mlb = []
    for r in exact:
        raw = r.get("mlbProfileId") or r.get("ids")
        try:
            m = int(raw or 0)
        except (TypeError, ValueError):
            m = 0
        if m > 0:
            with_mlb.append((m, r))
    if player.get("mlb_id"):
        for m, r in with_mlb:
            if m == int(player["mlb_id"]):
                return str(r["id"]), r, rows
    if len(with_mlb) == 1:
        return str(with_mlb[0][1]["id"]), with_mlb[0][1], rows
    if len(exact) == 1 and exact[0].get("id"):
        return str(exact[0]["id"]), exact[0], rows
    # Last resort: probe candidates for recent events (cap 6)
    probe = exact[:6] if exact else rows[:6]
    best = None
    best_n = -1
    for r in probe:
        sid_try = str(r.get("id") or "")
        if not sid_try:
            continue
        try:
            raw = client.events(sid=sid_try, pitcher=player["kind"] == "pitcher")
        except Exception:
            raw = []
        n = len(raw)
        print(f"    probe {r.get('name')} {sid_try[:8]}… events={n}")
        if n > best_n:
            best_n, best = n, r
        if n >= 50:
            break
    if best and best_n > 0:
        return str(best["id"]), best, rows
    return "", None, rows


def pick_window(raw: list[dict]) -> tuple[list[dict], str]:
    stats, window = rrs.select_stats_window(raw)
    if stats:
        return stats, window
    recent = [e for e in raw if bas.event_season_year(e) in (2025, 2026)]
    if recent:
        return recent, "2025-2026 any league"
    if raw:
        # keep newest 3 seasons
        years = sorted({bas.event_season_year(e) for e in raw if bas.event_season_year(e)}, reverse=True)
        keep_y = set(years[:3])
        kept = [e for e in raw if bas.event_season_year(e) in keep_y]
        return kept or raw, f"years {sorted(keep_y)}"
    return [], "none"


def fill_pitcher(client, player, docs) -> dict:
    key = rrs.row_key(player)
    old = docs["pitcher_by"].get(key) or {}
    sid, matched, cands = resolve_sid(client, player)
    if not sid:
        return {
            **player,
            "status": "unresolved",
            "candidates": [c.get("name") for c in cands[:12]],
        }
    raw = client.events(sid=sid, pitcher=True)
    if player["name"] == "John Aponte" and len(raw) < 100:
        raw = bas.fetch_events_pages(
            client.token,
            pitcher_id=sid,
            max_events=2000,
            take=200,
            pause_s=0.04,
            timeout_s=30,
            max_attempts=3,
            stop_before_year=2018,
            retain=lambda e: True,
            max_scan=5000,
        )
    stats, window = pick_window(raw)
    mid = player.get("mlb_id")
    mix_pack = bas.aggregate_pitcher_count_mixes(stats or raw)
    mix = mix_pack.get(sid) or (next(iter(mix_pack.values())) if mix_pack else {})
    mix = dict(mix or {})
    mix.update(
        {
            "name": player["name"],
            "team": player["team"],
            "mlb_id": mid,
            "player_id": player.get("player_id") or mid,
            "synergy_id": sid,
            "throws": old.get("throws") or "R",
            "window_used": window,
            "source": window,
        }
    )
    if old.get("summer_line"):
        mix["summer_line"] = old["summer_line"]
    docs["pitcher_by"][key] = {**old, **mix}

    ar = docs["arsenal_by"].get(key) or {
        "name": player["name"],
        "player_id": player.get("player_id") or mid,
        "team": player["team"],
        "throws": "R",
        "arsenal": [],
        "view": "hitter",
    }
    ar = rrs.merge_arsenal(ar, mix, stats or raw)
    ar.update(
        {
            "name": player["name"],
            "team": player["team"],
            "player_id": player.get("player_id") or mid,
            "mlb_id": mid,
            "synergy_id": sid,
        }
    )
    if mix.get("platoon"):
        ar["platoon"] = mix["platoon"]
    if mix.get("count_mix"):
        ar["count_mix"] = mix["count_mix"]
        ar["usage_by_count"] = mix["count_mix"]
    ar.pop("note", None)
    docs["arsenal_by"][key] = ar

    heat_key = str(mid or sid)
    heat = rrs.make_heat(player, sid, raw, True) if raw else rrs.empty_heat(
        player["name"], player["team"], sid, mid, True
    )
    docs["p_heat"][heat_key] = heat
    docs["p_heat"][sid] = heat

    return {
        **player,
        "status": "populated" if (mix.get("pitches") or 0) > 0 else ("empty" if raw else "no_events"),
        "synergy_id": sid,
        "synergy_name": (matched or {}).get("name"),
        "raw_events": len(raw),
        "stats_events": len(stats),
        "window_used": window,
        "pitches": mix.get("pitches") or 0,
        "arsenal_n": len(ar.get("arsenal") or []),
        "heat_located": heat.get("located"),
    }


def fill_hitter(client, player, docs) -> dict:
    key = rrs.row_key(player)
    old = docs["hitter_by"].get(key) or {}
    sid, matched, cands = resolve_sid(client, player)
    if not sid:
        return {
            **player,
            "status": "unresolved",
            "candidates": [c.get("name") for c in cands[:12]],
        }
    raw = client.events(sid=sid, pitcher=False)
    stats, window = pick_window(raw)
    mid = player.get("mlb_id")
    if stats:
        new = rrs.merge_hitter(old, player, sid, stats, window)
    else:
        new = {
            **old,
            "name": player["name"],
            "team": player["team"],
            "synergy_id": sid,
            "mlb_id": mid,
        }
    docs["hitter_by"][key] = new
    docs["h_heat"][sid] = (
        rrs.make_heat(player, sid, raw, False)
        if raw
        else rrs.empty_heat(player["name"], player["team"], sid, mid, False)
    )
    # spray upsert
    try:
        bas.merge_spray_into_pregame([new])
    except Exception as exc:  # noqa: BLE001
        print(f"  spray warn: {exc}")
    return {
        **player,
        "status": "populated" if (new.get("overall") or {}).get("pitches") else ("empty" if raw else "no_events"),
        "synergy_id": sid,
        "synergy_name": (matched or {}).get("name"),
        "raw_events": len(raw),
        "stats_events": len(stats),
        "window_used": window,
        "pitches": (new.get("overall") or {}).get("pitches"),
        "spray": len(new.get("spray_points") or []),
        "heat_located": docs["h_heat"][sid].get("located"),
    }


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    players = want_players()
    print(f"targets={len(players)}")

    # Resolve MLB ids first
    for p in players:
        mid = resolve_mlb(p)
        if mid and (not p.get("mlb_id") or int(p.get("mlb_id") or 0) <= 0):
            rewrite_roster_mlb(p["name"], p["team"], mid)
            p["mlb_id"] = mid
            p["player_id"] = mid
            print(f"MLB resolve {p['team']} {p['name']} → {mid}")

    client = rrs.Synergy()
    hitter_doc = json.loads((DATA / "advance_opposing_hitters.json").read_text())
    pitcher_doc = json.loads((DATA / "advance_opposing_pitchers.json").read_text())
    arsenal_doc = json.loads((DATA / "pregame_pitcher_arsenals.json").read_text())
    heat_doc = json.loads((DATA / "advance_heatmaps.json").read_text())
    docs = {
        "hitter_by": {rrs.row_key(r): r for r in hitter_doc.get("hitters") or []},
        "pitcher_by": {rrs.row_key(r): r for r in pitcher_doc.get("pitchers") or []},
        "arsenal_by": {rrs.row_key(r): r for r in arsenal_doc.get("pitchers") or []},
        "h_heat": dict(heat_doc.get("hitters") or {}),
        "p_heat": dict(heat_doc.get("pitchers") or {}),
    }

    results = []
    for i, player in enumerate(players, 1):
        # skip if already well populated unless Murphy/Aponte
        key = rrs.row_key(player)
        force = (player["team"], rrs.norm(player["name"])) in FORCED
        if player["kind"] == "pitcher":
            existing = docs["pitcher_by"].get(key) or {}
            if not force and (existing.get("pitches") or 0) >= 20 and existing.get("count_mix"):
                print(f"[{i}/{len(players)}] skip populated {player['name']} pitches={existing.get('pitches')}")
                results.append({**player, "status": "already", "pitches": existing.get("pitches")})
                continue
        else:
            existing = docs["hitter_by"].get(key) or {}
            if not force and (existing.get("overall") or {}).get("pitches"):
                print(f"[{i}/{len(players)}] skip populated {player['name']}")
                results.append({**player, "status": "already", "pitches": (existing.get("overall") or {}).get("pitches")})
                continue

        print(f"[{i}/{len(players)}] {player['kind']} {player['team']} {player['name']} mlb={player.get('mlb_id')}", flush=True)
        try:
            if player["kind"] == "pitcher":
                result = fill_pitcher(client, player, docs)
            else:
                result = fill_hitter(client, player, docs)
        except Exception as exc:  # noqa: BLE001
            result = {**player, "status": "error", "error": repr(exc)}
            print(f"  ERROR {exc!r}")
        results.append(result)
        print(
            f"  → {result.get('status')} sid={result.get('synergy_id')} "
            f"raw={result.get('raw_events')} kept={result.get('stats_events')} "
            f"pitches={result.get('pitches')} spray={result.get('spray')}",
            flush=True,
        )
        time.sleep(0.15)

    # Summer lines for pitchers with mlb ids
    need = [
        int(r["mlb_id"])
        for r in docs["pitcher_by"].values()
        if int(r.get("mlb_id") or 0) > 0 and not r.get("summer_line")
    ]
    # only our targets
    target_mids = {int(p["mlb_id"]) for p in players if p.get("mlb_id") and int(p["mlb_id"]) > 0}
    need = [m for m in need if m in target_mids]
    if need:
        lines = bas.fetch_2026_pitcher_summer_lines(need)
        for key, row in docs["pitcher_by"].items():
            mid = int(row.get("mlb_id") or 0)
            if mid in lines:
                row["summer_line"] = lines[mid]
                if key in docs["arsenal_by"]:
                    docs["arsenal_by"][key]["summer_line"] = lines[mid]
                    docs["arsenal_by"][key]["level_hint"] = lines[mid].get("level")

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    hitter_doc["hitters"] = sorted(docs["hitter_by"].values(), key=lambda r: (r.get("team") or "", r.get("name") or ""))
    hitter_doc["updated"] = now
    pitcher_doc["pitchers"] = sorted(docs["pitcher_by"].values(), key=lambda r: (r.get("team") or "", r.get("name") or ""))
    pitcher_doc["updated"] = now
    arsenal_doc["pitchers"] = sorted(docs["arsenal_by"].values(), key=lambda r: (r.get("team") or "", r.get("name") or ""))
    arsenal_doc["updated"] = now
    heat_doc["hitters"] = docs["h_heat"]
    heat_doc["pitchers"] = docs["p_heat"]
    heat_doc["updated"] = now

    rrs.atomic_write(DATA / "advance_opposing_hitters.json", hitter_doc)
    rrs.atomic_write(DATA / "advance_opposing_pitchers.json", pitcher_doc)
    rrs.atomic_write(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
    rrs.atomic_write(DATA / "advance_heatmaps.json", heat_doc, compact=True)

    mixes = {
        str(r["synergy_id"]): r
        for r in docs["pitcher_by"].values()
        if r.get("synergy_id") and r.get("pitches")
    }
    try:
        bas.merge_synergy_count_into_arsenals(mixes)
    except Exception as exc:  # noqa: BLE001
        print(f"merge warn: {exc}")
    try:
        fmp.copy_arsenal_platoon_to_advance()
    except Exception as exc:  # noqa: BLE001
        print(f"platoon warn: {exc}")

    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)

    # Ensure TTH / stubs for every target
    for p in players:
        roster = erp.find_roster(p["name"], p.get("mlb_id"), p["team"]) or {
            **p,
            "id": p.get("player_id") or p.get("mlb_id") or 0,
        }
        erp.ensure_shared(roster)
        if p["kind"] == "pitcher":
            erp.ensure_pitcher(roster)
        else:
            erp.ensure_hitter(roster)

    by = {}
    for r in results:
        by[r.get("status") or "?"] = by.get(r.get("status") or "?", 0) + 1
    report = {"updated": now, "by_status": by, "players": results}
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"done": True, "by_status": by}, indent=2))
    print(f"report → {REPORT}")


if __name__ == "__main__":
    main()

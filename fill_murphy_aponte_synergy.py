#!/usr/bin/env python3
"""Aggressive Synergy/MLB fill for Ryan Murphy (#40) and John Aponte (#57) PON.

Fixes Murphy's synthetic roster id → MLBAM 694885, resolves Synergy ids even when
name search returns duplicates, and uses broad 2025–2026 (any league) windows so
Pioneer/Showcase samples for Aponte are not dropped.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import synergy_dns_fix  # noqa: F401 — patch Synergy host DNS before API calls
import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import ensure_roster_player as erp
import fill_missing_platoon_synergy as fmp
import refresh_roster_synergy as rrs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
REPORT = RUNS / "murphy_aponte_fill_report.json"

MURPHY_MLB = 694885
MURPHY_SID = "5e31bc4696adda46f4f954da"
APONTE_SID = "616f22089b4397faf0dc0052"

TARGETS = [
    {
        "name": "Ryan Murphy",
        "team": "PON",
        "mlb_id": MURPHY_MLB,
        "synergy_id": MURPHY_SID,
        "throws": "R",
        "jersey": "40",
        "position": "P",
    },
    {
        "name": "John Aponte",
        "team": "PON",
        "mlb_id": None,  # no MLBAM; keep synthetic roster id
        "synergy_id": APONTE_SID,
        "throws": "R",
        "jersey": "57",
        "position": "P",
    },
]


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc, *, compact: bool = False) -> None:
    path = DATA / name
    if compact:
        path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def retarget_murphy_id() -> dict:
    """Replace synthetic Murphy id with MLBAM 694885 across roster + caches."""
    rost = load("lbprc_2025_rosters.json")
    old_id = None
    for pack in rost.values():
        if pack.get("teamAbbrev") != "PON":
            continue
        for p in pack.get("players") or []:
            if ligapr.norm_name(p.get("name") or "") != "ryanmurphy":
                continue
            old_id = int(p.get("id") or 0)
            p["id"] = MURPHY_MLB
            p["jersey"] = p.get("jersey") or "40"
            p["throws"] = p.get("throws") or "R"
            p["position"] = "P"
            print(f"roster Murphy id {old_id} → {MURPHY_MLB}")
    dump("lbprc_2025_rosters.json", rost)

    player = {"name": "Ryan Murphy", "id": MURPHY_MLB, "team": "PON", "throws": "R", "jersey": "40", "position": "P"}
    if old_id and old_id != MURPHY_MLB:
        # reuse official-add retarget helper pattern
        from add_ponce_official_nov4 import _retarget_caches

        _retarget_caches("Ryan Murphy", old_id, player)
    erp.ensure_pitcher(player)
    erp.ensure_shared(player)
    return {"old_id": old_id, "new_id": MURPHY_MLB}


def resolve_sid(client: rrs.Synergy, player: dict) -> tuple[str, dict | None, list]:
    forced = str(player.get("synergy_id") or "")
    if forced:
        return forced, {"id": forced, "name": player["name"]}, []
    sid, matched, rows = client.search(
        {"name": player["name"], "mlb_id": player.get("mlb_id")}
    )
    if sid:
        return str(sid), matched, rows
    # Aggressive: among exact-name hits, prefer mlbProfileId match, else first with id
    want = rrs.norm(player["name"])
    exact = [r for r in rows if rrs.norm(r.get("name") or "") == want]
    mid = player.get("mlb_id")
    if mid:
        for r in exact or rows:
            raw = r.get("mlbProfileId") or r.get("ids")
            try:
                if int(raw or 0) == int(mid):
                    return str(r["id"]), r, rows
            except (TypeError, ValueError):
                pass
    if len(exact) == 1 and exact[0].get("id"):
        return str(exact[0]["id"]), exact[0], rows
    return "", None, rows


def pick_stats_window(raw: list[dict]) -> tuple[list[dict], str]:
    """Prefer primary heat windows, but don't let a tiny 2024 JC sample
    beat a larger 2025–2026 indy/Pioneer/showcase pool (Aponte case)."""
    stats, window = rrs.select_stats_window(raw)
    recent = [e for e in raw if bas.event_season_year(e) in (2025, 2026)]
    if recent and (not stats or len(recent) >= max(40, 3 * len(stats))):
        return recent, "2025-2026 any league"
    if stats:
        return stats, window
    if raw:
        return raw, "unfiltered"
    return [], "none"


def upsert_stuff(player: dict, arsenal: dict, summer: dict | None) -> None:
    stuff = load("pregame_opposing_stuff.json")
    rows = stuff if isinstance(stuff, list) else (stuff.get("pitchers") or [])
    key = ligapr.norm_name(player["name"])
    mid = int(player.get("mlb_id") or 0)
    payload = {
        "name": player["name"],
        "player_id": mid if mid > 0 else player.get("player_id"),
        "mlb_id": mid if mid > 0 else None,
        "team": "PON",
        "mlb_debut": arsenal.get("mlb_debut"),
        "birth_country": None,
        "stuff_plus": arsenal.get("stuff_plus"),
        "pitches": arsenal.get("pitches") or 0,
        "arsenal": arsenal.get("arsenal") or [],
        "source": "Synergy events" if (arsenal.get("pitches") or 0) else "unavailable",
        "level_hint": (summer or {}).get("level") or arsenal.get("level_hint"),
    }
    found = False
    for i, row in enumerate(rows):
        rid = int(row.get("mlb_id") or row.get("player_id") or 0)
        if (mid > 0 and rid == mid) or ligapr.norm_name(row.get("name") or "") == key:
            rows[i] = {**row, **payload}
            found = True
            break
    if not found:
        rows.append(payload)
    if isinstance(stuff, list):
        dump("pregame_opposing_stuff.json", rows)
    else:
        stuff["pitchers"] = rows
        dump("pregame_opposing_stuff.json", stuff)


def fill_one(client: rrs.Synergy, player: dict, docs: dict) -> dict:
    name = player["name"]
    key = rrs.row_key({"team": "PON", "name": name})
    old = docs["pitcher_by"].get(key) or {}
    sid, matched, candidates = resolve_sid(client, player)
    if not sid:
        return {
            **player,
            "status": "unresolved",
            "candidates": [c.get("name") for c in candidates[:12]],
        }

    raw = client.events(sid=sid, pitcher=True)
    # Aponte: prior pull stopped early; pull full retained set if thin
    if name == "John Aponte" and len(raw) < 100:
        raw = bas.fetch_events_pages(
            client.token,
            pitcher_id=sid,
            max_events=2000,
            take=200,
            pause_s=0.04,
            timeout_s=20,
            max_attempts=3,
            stop_before_year=2018,
            retain=lambda e: True,
            max_scan=5000,
        )

    stats, window = pick_stats_window(raw)
    mid = player.get("mlb_id")
    roster_pid = mid if mid and int(mid) > 0 else int(
        (erp.find_roster(name, None, "PON") or {}).get("id") or 0
    )

    mix_pack = bas.aggregate_pitcher_count_mixes(stats or raw)
    mix = mix_pack.get(sid) or (next(iter(mix_pack.values())) if mix_pack else {})
    mix = dict(mix or {})
    mix.update(
        {
            "name": name,
            "team": "PON",
            "mlb_id": mid if mid and int(mid) > 0 else None,
            "player_id": roster_pid or None,
            "synergy_id": sid,
            "throws": player.get("throws") or old.get("throws") or "R",
            "window_used": window,
            "source": window,
        }
    )
    if old.get("summer_line"):
        mix["summer_line"] = old["summer_line"]

    docs["pitcher_by"][key] = {**old, **mix}

    ar = docs["arsenal_by"].get(key) or {
        "name": name,
        "player_id": roster_pid or None,
        "team": "PON",
        "throws": player.get("throws") or "R",
        "arsenal": [],
        "view": "hitter",
    }
    ar = rrs.merge_arsenal(ar, mix, stats or raw)
    ar.update(
        {
            "name": name,
            "team": "PON",
            "player_id": roster_pid or None,
            "mlb_id": mid if mid and int(mid) > 0 else None,
            "throws": ar.get("throws") or player.get("throws") or "R",
            "synergy_id": sid,
            "note": None,
        }
    )
    if mix.get("platoon"):
        ar["platoon"] = mix["platoon"]
    if mix.get("count_mix"):
        ar["count_mix"] = mix["count_mix"]
        ar["usage_by_count"] = mix["count_mix"]
    ar.pop("note", None)
    docs["arsenal_by"][key] = ar

    heat = rrs.make_heat(
        {"name": name, "team": "PON", "mlb_id": mid},
        sid,
        raw,
        True,
    ) if raw else rrs.empty_heat(name, "PON", sid, mid, True)
    heat_key = str(mid if mid and int(mid) > 0 else sid)
    docs["p_heat"][heat_key] = heat
    docs["p_heat"][sid] = heat

    upsert_stuff(
        {**player, "player_id": roster_pid},
        ar,
        mix.get("summer_line"),
    )

    return {
        "name": name,
        "team": "PON",
        "status": "populated" if (mix.get("pitches") or 0) > 0 else ("empty" if raw else "no_events"),
        "mlb_id": mid,
        "player_id": roster_pid,
        "synergy_id": sid,
        "synergy_name": (matched or {}).get("name"),
        "raw_events": len(raw),
        "stats_events": len(stats),
        "window_used": window,
        "pitches": mix.get("pitches") or 0,
        "arsenal_types": [a.get("type") for a in (ar.get("arsenal") or [])],
        "platoon": bool(mix.get("platoon")),
        "count_buckets": list((mix.get("count_mix") or {}).keys()),
        "heat_located": heat.get("located"),
        "summer_line": bool(mix.get("summer_line")),
    }


def attach_summer_lines(pitcher_by: dict, arsenal_by: dict) -> None:
    need = []
    for row in pitcher_by.values():
        mid = int(row.get("mlb_id") or 0)
        if mid > 0 and not row.get("summer_line"):
            need.append(mid)
    if not need:
        return
    lines = bas.fetch_2026_pitcher_summer_lines(need)
    for key, row in pitcher_by.items():
        mid = int(row.get("mlb_id") or 0)
        if mid in lines:
            row["summer_line"] = lines[mid]
            if key in arsenal_by:
                arsenal_by[key]["summer_line"] = lines[mid]
                arsenal_by[key]["level_hint"] = lines[mid].get("level")


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    id_fix = retarget_murphy_id()

    client = rrs.Synergy()
    pitcher_doc = load("advance_opposing_pitchers.json")
    arsenal_doc = load("pregame_pitcher_arsenals.json")
    heat_doc = load("advance_heatmaps.json")

    docs = {
        "pitcher_by": {rrs.row_key(r): r for r in pitcher_doc.get("pitchers") or []},
        "arsenal_by": {rrs.row_key(r): r for r in arsenal_doc.get("pitchers") or []},
        "p_heat": dict(heat_doc.get("pitchers") or {}),
    }

    # Drop stale Murphy rows keyed only by old synthetic id leftovers (name key wins)
    report = {"id_fix": id_fix, "players": []}
    for i, player in enumerate(TARGETS, 1):
        print(f"[{i}/{len(TARGETS)}] {player['name']} …", flush=True)
        result = fill_one(client, player, docs)
        report["players"].append(result)
        print(
            f"  {result['status']} sid={result.get('synergy_id')} "
            f"raw={result.get('raw_events')} kept={result.get('stats_events')} "
            f"pitches={result.get('pitches')} window={result.get('window_used')}",
            flush=True,
        )
        time.sleep(0.2)

    attach_summer_lines(docs["pitcher_by"], docs["arsenal_by"])
    for row in report["players"]:
        key = rrs.row_key({"team": "PON", "name": row["name"]})
        row["summer_line"] = bool((docs["pitcher_by"].get(key) or {}).get("summer_line"))
        if row["name"] == "Ryan Murphy":
            sl = (docs["pitcher_by"].get(key) or {}).get("summer_line") or {}
            row["summer_summary"] = {
                "team": sl.get("team"),
                "level": sl.get("level"),
                "ip": sl.get("ip"),
                "era": sl.get("era"),
                "k9": sl.get("k9"),
                "bb9": sl.get("bb9"),
            }

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pitcher_doc["pitchers"] = sorted(
        docs["pitcher_by"].values(), key=lambda r: (r.get("team") or "", r.get("name") or "")
    )
    pitcher_doc["updated"] = now
    arsenal_doc["pitchers"] = sorted(
        docs["arsenal_by"].values(), key=lambda r: (r.get("team") or "", r.get("name") or "")
    )
    arsenal_doc["updated"] = now
    heat_doc["pitchers"] = docs["p_heat"]
    heat_doc["updated"] = now

    dump("advance_opposing_pitchers.json", pitcher_doc)
    dump("pregame_pitcher_arsenals.json", arsenal_doc)
    dump("advance_heatmaps.json", heat_doc, compact=True)

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
        print(f"platoon copy warn: {exc}")

    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)

    # Ensure TTH rows carry corrected Murphy id
    tth = load("pregame_tth_pop.json")
    for row in tth.get("players") or []:
        if ligapr.norm_name(row.get("name") or "") == "ryanmurphy" and row.get("team") == "PON":
            row["player_id"] = MURPHY_MLB
            row["jersey"] = row.get("jersey") or "40"
        if ligapr.norm_name(row.get("name") or "") == "johnaponte" and row.get("team") == "PON":
            row["jersey"] = row.get("jersey") or "57"
    dump("pregame_tth_pop.json", tth)

    report["updated"] = now
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2))
    print(f"report → {REPORT}")


if __name__ == "__main__":
    main()

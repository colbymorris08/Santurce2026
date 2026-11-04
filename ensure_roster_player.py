#!/usr/bin/env python3
"""Ensure a roster player appears in every pregame tab that applies to their role.

Pitchers → Pitcher Advance, One Page, Stuff, Bullpen gate, Matchup/HVP gate,
          TTH, Strategy (roster), card_whiff when SAN.
Catchers → TTH (pop/arm), plus hitter tabs below.
Hitters  → Hitter Advance, Spray, BR card, Lineup spots (SAN), OF tabs gate,
          Strategy (roster), card_whiff when SAN. (TTH only if C.)
Both     → lbprc_2025_rosters.json (must already list them).

By default this wires roster identity AND fills every tab that has real data
(Synergy usages/heat + Prospect Savant movement for pitchers). Prefer a real
MLBAM id (`--id`). If the roster still has a synthetic negative id,
`--resolve-mlb` looks up free_agent_pool + Synergy mlbProfileId and rewrites
the roster + caches. Pass `--stub-only` only when you intentionally want empty
shells (offline / no credentials).

  python3 ensure_roster_player.py --name "Ryan Murphy" --team PON --id 694885
  python3 ensure_roster_player.py --name "John Aponte" --team PON --resolve-mlb
  python3 ensure_roster_player.py --name "X" --team CAG --stub-only   # identity only
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import build_ligapr_daily as ligapr

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

PITCHER_POS = {"P", "RHP", "LHP", "RHR", "LHR", "RHS", "LHS"}
CATCHER_POS = {"C"}


def load(name: str) -> Any:
    return json.loads((DATA / name).read_text())


def dump(name: str, doc: Any) -> None:
    (DATA / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def find_roster(name: str | None, mlb_id: int | None, team: str | None) -> dict | None:
    rosters = load("lbprc_2025_rosters.json")
    want = ligapr.norm_name(name or "")
    for pack in rosters.values():
        abbr = pack.get("teamAbbrev")
        if team and abbr != team.upper():
            continue
        for p in pack.get("players") or []:
            pid = int(p.get("id") or 0)
            if mlb_id and pid == mlb_id:
                return {**p, "team": abbr, "team_name": pack.get("teamName")}
            if want and ligapr.norm_name(p.get("name") or "") == want:
                return {**p, "team": abbr, "team_name": pack.get("teamName")}
    return None


def lookup_mlb_id_from_fa(name: str, *, pitcher: bool | None = None) -> int | None:
    """Resolve MLBAM id from free_agent_pool.json by exact normalized name."""
    fa = load("free_agent_pool.json")
    want = ligapr.norm_name(name)
    pools: list[dict] = []
    if pitcher is True:
        pools = list(fa.get("pitchers") or [])
    elif pitcher is False:
        pools = list(fa.get("hitters") or [])
    else:
        pools = list(fa.get("pitchers") or []) + list(fa.get("hitters") or [])
    hits = [r for r in pools if ligapr.norm_name(r.get("name") or "") == want]
    ids = []
    for row in hits:
        try:
            mid = int(row.get("mlbam_id") or row.get("mlb_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid > 0:
            ids.append(mid)
    uniq = sorted(set(ids))
    if len(uniq) == 1:
        return uniq[0]
    return None


def lookup_mlb_id_from_synergy(name: str) -> tuple[int | None, str | None]:
    """Return (mlb_id, synergy_id) when Synergy search yields a unique MLB profile."""
    try:
        import refresh_roster_synergy as rrs
    except Exception:  # noqa: BLE001
        return None, None
    try:
        client = rrs.Synergy()
        sid, matched, rows = client.search({"name": name, "mlb_id": None})
    except Exception as exc:  # noqa: BLE001
        print(f"  synergy resolve skip: {exc}")
        return None, None
    want = ligapr.norm_name(name)
    exact = [r for r in rows if ligapr.norm_name(r.get("name") or "") == want]
    with_mlb = []
    for row in exact:
        raw = row.get("mlbProfileId") or row.get("ids")
        try:
            mid = int(raw or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid > 0:
            with_mlb.append((mid, str(row.get("id") or "")))
    uniq = {(m, s) for m, s in with_mlb if s}
    if len(uniq) == 1:
        mid, sid_found = next(iter(uniq))
        return mid, sid_found
    if matched and sid:
        raw = matched.get("mlbProfileId") or matched.get("ids")
        try:
            mid = int(raw or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid > 0:
            return mid, str(sid)
    return None, (str(sid) if sid else None)


def rewrite_roster_id(player: dict, new_id: int) -> None:
    """Persist a corrected MLBAM id onto lbprc_2025_rosters.json."""
    rost = load("lbprc_2025_rosters.json")
    want = ligapr.norm_name(player.get("name") or "")
    team = (player.get("team") or "").upper()
    for pack in rost.values():
        if team and pack.get("teamAbbrev") != team:
            continue
        for row in pack.get("players") or []:
            if ligapr.norm_name(row.get("name") or "") == want:
                row["id"] = int(new_id)
                dump("lbprc_2025_rosters.json", rost)
                player["id"] = int(new_id)
                return
    raise SystemExit(f"rewrite_roster_id miss {player.get('name')} {team}")


def fill_movement_now(player: dict) -> dict[str, Any]:
    """Overlay Prospect Savant / Statcast / Synergy IVB+HB onto the arsenal row."""
    if not is_pitcher(player):
        return {"status": "skipped", "reason": "not_pitcher"}
    import fill_movement_plot_batches as move
    import fill_remaining_holes as holes

    arsenal_doc = holes.load("pregame_pitcher_arsenals.json")
    arsenal_by = holes.index_rows(arsenal_doc.get("pitchers") or [])
    holes_ckpt = json.loads(holes.CHECKPOINT.read_text()) if holes.CHECKPOINT.is_file() else {}
    resolved = holes_ckpt.get("resolved_ids") or {}
    row = move.ensure_row(arsenal_doc, player, arsenal_by)
    if holes.has_movement(row):
        return {
            "status": "already",
            "source": row.get("ps_source") or row.get("movement_source"),
            "season": row.get("ps_season") or row.get("movement_season"),
        }
    token_box: dict = {"keys": set()}
    found = move.lookup_pitcher(player, row, resolved, token_box)
    added = False
    if found.get("pitches"):
        added = move.merge_plot_only(
            row,
            found["pitches"],
            source=found["source"],
            season=found["season"],
            throws=found.get("throws") or player.get("throws") or "",
        )
    if added:
        note = str(row.get("note") or "")
        if "stub" in note.lower() or "scaffold" in note.lower() or "run Synergy" in note:
            row.pop("note", None)
        arsenal_doc["updated"] = now()
        holes.dump(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
    return {
        "status": "filled" if added else "no_plot",
        "source": found.get("source") or None,
        "season": found.get("season"),
        "why": None if added else move.best_why(found.get("attempts") or []),
    }


def fill_synergy_now(player: dict) -> dict[str, Any]:
    """Pull Synergy into advance/arsenal/heat/one-page for one roster player."""
    import synergy_dns_fix  # noqa: F401
    import build_advance_synergy as bas
    import refresh_roster_synergy as rrs

    kind = "pitcher" if is_pitcher(player) else "hitter"
    mid = int(player.get("id") or 0)
    mlb_id = mid if mid > 0 else None
    search_player = {
        "name": player["name"],
        "team": player["team"],
        "mlb_id": mlb_id,
        "player_id": mid,
        "position": player.get("position"),
    }
    client = rrs.Synergy()
    sid, matched, rows = client.search(search_player)
    if not sid:
        # Prefer unique mlb-bearing exact match (also covers forced KNOWN paths)
        want = ligapr.norm_name(player["name"])
        exact = [r for r in rows if ligapr.norm_name(r.get("name") or "") == want]
        if mlb_id:
            for r in exact or rows:
                raw = r.get("mlbProfileId") or r.get("ids")
                try:
                    if int(raw or 0) == int(mlb_id):
                        sid = str(r["id"])
                        matched = r
                        break
                except (TypeError, ValueError):
                    pass
        if not sid and len(exact) == 1:
            sid = str(exact[0]["id"])
            matched = exact[0]
    if not sid:
        return {
            "status": "unresolved",
            "candidates": [r.get("name") for r in rows[:12]],
        }

    raw = client.events(sid=str(sid), pitcher=kind == "pitcher")
    stats, window = rrs.select_stats_window(raw)
    if not stats and raw:
        stats = [e for e in raw if bas.event_season_year(e) in (2025, 2026)] or raw
        window = "2025-2026 any league" if stats is not raw else "unfiltered"

    if kind == "pitcher":
        pitcher_doc = load("advance_opposing_pitchers.json")
        arsenal_doc = load("pregame_pitcher_arsenals.json")
        heat_doc = load("advance_heatmaps.json")
        pitcher_by = {rrs.row_key(r): r for r in pitcher_doc.get("pitchers") or []}
        arsenal_by = {rrs.row_key(r): r for r in arsenal_doc.get("pitchers") or []}
        p_heat = dict(heat_doc.get("pitchers") or {})
        key = rrs.row_key(search_player)
        old = pitcher_by.get(key) or {}
        packed = bas.aggregate_pitcher_count_mixes(stats or raw)
        mix = packed.get(str(sid)) or (next(iter(packed.values())) if packed else {})
        mix = dict(mix or {})
        mix.update(
            {
                "name": player["name"],
                "team": player["team"],
                "mlb_id": mlb_id,
                "player_id": mid or None,
                "synergy_id": str(sid),
                "throws": player.get("throws") or old.get("throws"),
                "window_used": window,
                "source": window,
            }
        )
        if mlb_id and not mix.get("summer_line"):
            lines = bas.fetch_2026_pitcher_summer_lines([mlb_id])
            if mlb_id in lines:
                mix["summer_line"] = lines[mlb_id]
        pitcher_by[key] = {**old, **mix}
        ar = arsenal_by.get(key) or {
            "name": player["name"],
            "player_id": mid or None,
            "team": player["team"],
            "throws": player.get("throws"),
            "arsenal": [],
            "view": "hitter",
        }
        ar = rrs.merge_arsenal(ar, mix, stats or raw)
        ar.update(
            {
                "name": player["name"],
                "team": player["team"],
                "player_id": mid or None,
                "mlb_id": mlb_id,
                "synergy_id": str(sid),
            }
        )
        if mix.get("platoon"):
            ar["platoon"] = mix["platoon"]
        if mix.get("count_mix"):
            ar["count_mix"] = mix["count_mix"]
        ar.pop("note", None)
        arsenal_by[key] = ar
        heat_key = str(mlb_id or sid)
        p_heat[heat_key] = (
            rrs.make_heat(search_player, str(sid), raw, True)
            if raw
            else rrs.empty_heat(player["name"], player["team"], str(sid), mlb_id, True)
        )
        p_heat[str(sid)] = p_heat[heat_key]
        stamp = now()
        pitcher_doc["pitchers"] = sorted(
            pitcher_by.values(), key=lambda r: (r.get("team") or "", r.get("name") or "")
        )
        pitcher_doc["updated"] = stamp
        arsenal_doc["pitchers"] = sorted(
            arsenal_by.values(), key=lambda r: (r.get("team") or "", r.get("name") or "")
        )
        arsenal_doc["updated"] = stamp
        heat_doc["pitchers"] = p_heat
        heat_doc["updated"] = stamp
        dump("advance_opposing_pitchers.json", pitcher_doc)
        dump("pregame_pitcher_arsenals.json", arsenal_doc)
        dump("advance_heatmaps.json", heat_doc)
        try:
            bas.merge_synergy_count_into_arsenals(
                {str(sid): mix} if mix.get("pitches") else {}
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  arsenal merge warn: {exc}")
        movement = fill_movement_now(player)
        return {
            "status": "populated" if mix.get("pitches") else "empty",
            "synergy_id": str(sid),
            "synergy_name": (matched or {}).get("name"),
            "raw_events": len(raw),
            "stats_events": len(stats or []),
            "window_used": window,
            "pitches": mix.get("pitches") or 0,
            "movement": movement,
        }

    # hitter path
    hitter_doc = load("advance_opposing_hitters.json")
    heat_doc = load("advance_heatmaps.json")
    hitter_by = {rrs.row_key(r): r for r in hitter_doc.get("hitters") or []}
    h_heat = dict(heat_doc.get("hitters") or {})
    key = rrs.row_key(search_player)
    old = hitter_by.get(key) or {}
    if stats:
        hitter_by[key] = rrs.merge_hitter(old, search_player, str(sid), stats, window)
    else:
        old.update(
            {
                "name": player["name"],
                "team": player["team"],
                "synergy_id": str(sid),
                "mlb_id": mlb_id,
            }
        )
        hitter_by[key] = old
    h_heat[str(sid)] = (
        rrs.make_heat(search_player, str(sid), raw, False)
        if raw
        else rrs.empty_heat(player["name"], player["team"], str(sid), mlb_id, False)
    )
    stamp = now()
    hitter_doc["hitters"] = sorted(
        hitter_by.values(), key=lambda r: (r.get("team") or "", r.get("name") or "")
    )
    hitter_doc["updated"] = stamp
    heat_doc["hitters"] = h_heat
    heat_doc["updated"] = stamp
    dump("advance_opposing_hitters.json", hitter_doc)
    dump("advance_heatmaps.json", heat_doc)
    try:
        bas.merge_spray_into_pregame([hitter_by[key]])
    except Exception as exc:  # noqa: BLE001
        print(f"  spray merge warn: {exc}")
    new = hitter_by[key]
    return {
        "status": "populated" if (new.get("overall") or {}).get("pitches") else "empty",
        "synergy_id": str(sid),
        "synergy_name": (matched or {}).get("name"),
        "raw_events": len(raw),
        "stats_events": len(stats or []),
        "window_used": window,
        "pitches": (new.get("overall") or {}).get("pitches"),
        "spray": len(new.get("spray_points") or []),
    }


def is_pitcher(player: dict) -> bool:
    return str(player.get("position") or "").upper() in PITCHER_POS


def is_catcher(player: dict) -> bool:
    return str(player.get("position") or "").upper() in CATCHER_POS


def ensure_list_row(rows: list, player: dict, id_keys: tuple[str, ...], stub: dict) -> str:
    mid = int(player.get("id") or 0)
    key = ligapr.norm_name(player.get("name") or "")
    for row in rows:
        for ik in id_keys:
            try:
                if mid > 0 and int(row.get(ik) or 0) == mid:
                    row["name"] = player["name"]
                    row["team"] = player["team"]
                    return "updated"
            except (TypeError, ValueError):
                pass
        if ligapr.norm_name(row.get("name") or "") == key:
            row["name"] = player["name"]
            row["team"] = player["team"]
            if mid > 0:
                for ik in id_keys:
                    if ik in row or ik.endswith("_id"):
                        row[ik] = mid
                        break
            return "updated"
    rows.append(stub)
    return "added"


def ensure_pitcher(player: dict) -> dict[str, str]:
    mid = int(player.get("id") or 0)
    actions = {}

    ar = load("pregame_pitcher_arsenals.json")
    rows = ar.get("pitchers") or []
    actions["arsenal"] = ensure_list_row(
        rows,
        player,
        ("player_id", "mlb_id"),
        {
            "name": player["name"],
            "player_id": mid if mid > 0 else None,
            "team": player["team"],
            "throws": player.get("throws"),
            "mlb_debut": None,
            "level_hint": None,
            "stuff_plus": None,
            "pitches": 0,
            "arsenal": [],
            "usage_by_count": {},
            "count_mix": {},
            "platoon": {},
            "view": "hitter",
            "note": "Roster stub — run Synergy / Prospect Savant fills",
        },
    )
    ar["pitchers"] = rows
    ar["updated"] = now()
    dump("pregame_pitcher_arsenals.json", ar)

    stuff = load("pregame_opposing_stuff.json")
    stuff_rows = stuff if isinstance(stuff, list) else (stuff.get("pitchers") or [])
    actions["stuff"] = ensure_list_row(
        stuff_rows,
        player,
        ("player_id", "mlb_id"),
        {
            "name": player["name"],
            "player_id": mid if mid > 0 else None,
            "team": player["team"],
            "mlb_debut": None,
            "birth_country": None,
            "stuff_plus": None,
            "pitches": None,
            "arsenal": [],
            "source": "unavailable",
            "level_hint": None,
        },
    )
    if isinstance(stuff, list):
        dump("pregame_opposing_stuff.json", stuff_rows)
    else:
        stuff["pitchers"] = stuff_rows
        dump("pregame_opposing_stuff.json", stuff)

    adv = load("advance_opposing_pitchers.json")
    arows = adv.get("pitchers") or []
    actions["advance"] = ensure_list_row(
        arows,
        player,
        ("mlb_id", "player_id"),
        {
            "name": player["name"],
            "mlb_id": mid if mid > 0 else None,
            "team": player["team"],
            "throws": player.get("throws"),
            "synergy_id": None,
            "pitches": 0,
            "summer_line": None,
            "platoon": {},
            "count_mix": {},
        },
    )
    adv["pitchers"] = arows
    adv["updated"] = now()
    dump("advance_opposing_pitchers.json", adv)

    heat = load("advance_heatmaps.json")
    heats = heat.get("pitchers")
    if isinstance(heats, dict) and mid > 0:
        key = str(mid)
        if key not in heats:
            heats[key] = {
                "name": player["name"],
                "mlb_id": mid,
                "team": player["team"],
                "located": False,
                "vs_r": {},
                "vs_l": {},
            }
            heat["pitchers"] = heats
            dump("advance_heatmaps.json", heat)
            actions["heatmap"] = "added"
        else:
            heats[key]["name"] = player["name"]
            heats[key]["team"] = player["team"]
            actions["heatmap"] = "updated"
    elif isinstance(heats, list):
        actions["heatmap"] = ensure_list_row(
            heats,
            player,
            ("mlb_id", "player_id"),
            {
                "name": player["name"],
                "mlb_id": mid if mid > 0 else None,
                "team": player["team"],
                "vs_r": {},
                "vs_l": {},
            },
        )
        heat["pitchers"] = heats
        dump("advance_heatmaps.json", heat)

    return actions


def ensure_hitter(player: dict) -> dict[str, str]:
    mid = int(player.get("id") or 0)
    actions = {}

    adv = load("advance_opposing_hitters.json")
    rows = adv.get("hitters") or []
    actions["advance"] = ensure_list_row(
        rows,
        player,
        ("mlb_id", "player_id"),
        {
            "name": player["name"],
            "mlb_id": mid if mid > 0 else None,
            "team": player["team"],
            "bats": player.get("bats"),
            "throws": player.get("throws"),
            "position": player.get("position"),
            "synergy_id": None,
            "overall": {},
            "by_pitch_type": [],
            "vs_l_by_pitch_type": [],
            "vs_r_by_pitch_type": [],
            "risp_by_pitch_type": [],
            "spray_points": [],
            "spray_splits": {},
            "summer_line": None,
        },
    )
    adv["hitters"] = rows
    adv["updated"] = now()
    dump("advance_opposing_hitters.json", adv)

    br = load("pregame_baserunning_bunting.json")
    if isinstance(br, list):
        actions["br"] = ensure_list_row(
            br,
            player,
            ("player_id", "mlb_id"),
            {
                "player_id": mid if mid > 0 else None,
                "name": player["name"],
                "team": player["team"],
                "sb": None,
                "cs": None,
                "sac": None,
            },
        )
        dump("pregame_baserunning_bunting.json", br)

    spray = load("pregame_spray_charts.json")
    syn = spray.get("synergy") or []
    if isinstance(syn, list) and mid > 0 and not any(int(r.get("player_id") or r.get("mlb_id") or 0) == mid for r in syn):
        syn.append(
            {
                "player_id": mid,
                "mlb_id": mid,
                "name": player["name"],
                "team": player["team"],
                "points": [],
                "splits": {},
            }
        )
        spray["synergy"] = syn
        dump("pregame_spray_charts.json", spray)
        actions["spray"] = "added"
    else:
        actions["spray"] = "ok"

    return actions


def ensure_and_fill(player: dict, *, stub_only: bool = False) -> dict[str, Any]:
    """Wire every tab cache, then fill Synergy + Savant unless stub_only."""
    actions = ensure_shared(player)
    if is_pitcher(player):
        actions.update(ensure_pitcher(player))
        role = "pitcher"
    else:
        actions.update(ensure_hitter(player))
        role = "hitter"
    fill_result = None
    if not stub_only:
        fill_result = fill_synergy_now(player)
    if role == "pitcher":
        one = ligapr.build_pitcher_one_page()
        ligapr.write_json("pitcher_one_page.json", one)
        actions["one_page"] = "rebuilt"
    return {"role": role, "actions": actions, "fill": fill_result}


def ensure_shared(player: dict) -> dict[str, str]:
    mid = int(player.get("id") or 0)
    actions = {}

    # TTH / catcher-pop sheet is pitchers + catchers only (no INF/OF).
    if is_pitcher(player) or is_catcher(player):
        tth = load("pregame_tth_pop.json")
        rows = tth.get("players") or []
        actions["tth"] = ensure_list_row(
            rows,
            player,
            ("player_id",),
            {
                "player_id": mid if mid > 0 else None,
                "name": player["name"],
                "team": player["team"],
                "position": "P" if is_pitcher(player) else "C",
                "jersey": player.get("jersey") or "",
                "status": "Active",
                "time_to_home": None,
                "catcher_pop_time": None,
                "catcher_arm": None,
                "notes": "",
            },
        )
        tth["players"] = rows
        dump("pregame_tth_pop.json", tth)
    else:
        actions["tth"] = "skipped"

    # Strategy card reads live roster — no stub file. card_whiff for SAN.
    if player.get("team") == "SAN" and mid > 0:
        whiff = load("card_whiff.json")
        wrows = whiff.get("players") or whiff.get("pitchers") or whiff.get("hitters") or []
        # structure varies; only touch list-of-players shapes
        if isinstance(wrows, list):
            actions["card_whiff"] = ensure_list_row(
                wrows,
                player,
                ("mlb_id", "player_id", "id"),
                {
                    "name": player["name"],
                    "mlb_id": mid,
                    "team": "SAN",
                    "role": "pitcher" if is_pitcher(player) else "hitter",
                    "whiff": None,
                },
            )
            if "players" in whiff:
                whiff["players"] = wrows
            dump("card_whiff.json", whiff)

    return actions


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--id", type=int, default=None, help="MLBAM id")
    ap.add_argument("--team", default=None, help="Team abbrev filter")
    ap.add_argument("--rebuild-one-page", action="store_true")
    ap.add_argument(
        "--resolve-mlb",
        action="store_true",
        help="If roster id is synthetic/missing, resolve MLBAM via FA pool + Synergy",
    )
    ap.add_argument(
        "--fill-synergy",
        action="store_true",
        help="Deprecated alias: fill is now the default (Synergy + Savant movement)",
    )
    ap.add_argument(
        "--stub-only",
        action="store_true",
        help="Wire identity into caches only — skip Synergy/Savant fills",
    )
    args = ap.parse_args()
    do_fill = not args.stub_only  # fill-all-tabs is the default

    player = find_roster(args.name, args.id, args.team)
    if not player:
        raise SystemExit(
            f"Not on roster yet: {args.name!r} id={args.id} team={args.team}. "
            "Add them to data/lbprc_2025_rosters.json first."
        )

    resolve_info: dict[str, Any] = {}
    cur_id = int(player.get("id") or 0)
    if args.id and int(args.id) > 0 and cur_id != int(args.id):
        print(f"rewriting roster id {cur_id} → {args.id}")
        rewrite_roster_id(player, int(args.id))
        resolve_info["roster_id"] = int(args.id)
    elif args.resolve_mlb and cur_id <= 0:
        fa_id = lookup_mlb_id_from_fa(player["name"], pitcher=is_pitcher(player))
        syn_id, syn_sid = lookup_mlb_id_from_synergy(player["name"])
        chosen = args.id or fa_id or syn_id
        if chosen and int(chosen) > 0:
            print(f"resolved MLBAM {player['name']} → {chosen} (fa={fa_id} synergy={syn_id})")
            rewrite_roster_id(player, int(chosen))
            resolve_info = {"mlb_id": int(chosen), "fa": fa_id, "synergy_mlb": syn_id, "synergy_id": syn_sid}
        else:
            resolve_info = {"mlb_id": None, "fa": fa_id, "synergy_mlb": syn_id, "synergy_id": syn_sid}
            print(f"no unique MLBAM for {player['name']} (fa={fa_id} synergy={syn_id}/{syn_sid})")

    print(f"{player['team']} {player['name']} ({player.get('position')}) id={player.get('id')}")
    actions = ensure_shared(player)
    if is_pitcher(player):
        actions.update(ensure_pitcher(player))
        role = "pitcher"
    else:
        actions.update(ensure_hitter(player))
        role = "hitter"

    synergy_result = None
    if do_fill:
        print("filling Synergy + Prospect Savant movement…")
        synergy_result = fill_synergy_now(player)
        print(json.dumps({"synergy": synergy_result}, indent=2))
    else:
        print("stub-only: skipped Synergy/Savant fills")

    if args.rebuild_one_page or role == "pitcher" or do_fill:
        one = ligapr.build_pitcher_one_page()
        ligapr.write_json("pitcher_one_page.json", one)
        actions["one_page"] = "rebuilt"

    print(json.dumps({"role": role, "actions": actions, "resolve": resolve_info}, indent=2))


if __name__ == "__main__":
    main()

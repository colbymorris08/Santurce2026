#!/usr/bin/env python3
"""Fill Synergy count/hand for PS-movement arsenal arms that still lack count_mix.

Resolves pitcherId via /api/players/search (firstName/lastName + mlbProfileId),
pulls events/filter by pitcherId for 2025–2026 LBPRC/MLB/MiLB/LMB, merges into
pregame_pitcher_arsenals.json without overwriting Prospect Savant movement.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

import build_advance_synergy as bas

# Known Synergy ids (verified via firstName/lastName search + mlbProfileId).
KNOWN_IDS = {
    676454: ("5a83555ada5c2a61ccc83fce", "Brendan Cellucci", "CAG"),
    676221: ("5fd12a915fe05a0e9fc112e2", "Jorge Benitez", "CAG"),
    694365: ("5d0d76e94b8b4e352c29c0a8", "Magdiel Cotto", "CAG"),
    502179: ("59c8a8f844b2b8aa7ed10eeb", "Paolo Espino", "CAG"),
    681544: ("5c3b6408e5a2bd1e14c63583", "Wyatt Olds", "CAG"),
    701884: ("601d7d25cf03a3b45431e488", "Magnus Ellerts", "CAR"),
    800278: ("680080488961ae9b3dede5e6", "Pitterson Rosa", "CAR"),
    657624: ("5aeffb0b9559b26e7e9b7c09", "Josh James", "MAY"),
    814300: ("5fcd991b002101f9faa62a2e", "Josimar Cousin", "MAY"),
    678013: ("5f84b8c41cb0540001a544d9", "Lenny Torres Jr.", "PON"),
    701240: ("58a74edd35268c61b0b88a71", "Ethan Routzahn", "SJU"),
    666130: ("5f178ff5e0962f87adc0caaf", "Jay Groome", "SJU"),
}


def _split_name(name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    while parts and re.fullmatch(r"(?i)(jr\.?|sr\.?|ii|iii|iv|v)", parts[-1] or ""):
        parts.pop()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[-1]


def resolve_sid(token: str, name: str, mlb_id: int | None) -> str | None:
    if mlb_id and mlb_id in KNOWN_IDS:
        return KNOWN_IDS[mlb_id][0]
    first, last = _split_name(name)
    if not last:
        return None
    try:
        doc = bas.api_get(
            "/api/players/search",
            token,
            {
                "firstName": first or name.split()[0],
                "lastName": last,
                "sportId": bas.SPORT_ID_BASEBALL,
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  search fail {name}: {exc}")
        return None
    rows = doc.get("result") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return None
    if mlb_id:
        for r in rows:
            raw = r.get("mlbProfileId") or r.get("ids")
            try:
                mid = int(raw) if raw not in (None, "", 0, "0") else None
            except (TypeError, ValueError):
                mid = None
            if mid == int(mlb_id) and r.get("id"):
                return str(r["id"])
    return None


def movement_only() -> list[dict]:
    data = json.loads((bas.OUT / "pregame_pitcher_arsenals.json").read_text())
    out = []
    for p in data.get("pitchers") or []:
        has_ps = bas._has_ps_movement(p.get("arsenal")) or str(p.get("ps_source") or "").startswith(
            "Prospect Savant"
        )
        cm = p.get("count_mix") or {}
        has_count = bool(cm.get("0-0") or cm.get("2_strikes") or cm.get("overall"))
        if has_ps and not has_count:
            out.append(p)
    return out


def main() -> None:
    year_set = {2025, 2026}
    max_events = 1000
    token = bas.login_token(headed=False)
    print("Synergy login ok")
    missing = movement_only()
    print(f"movement-only={len(missing)}")
    target_league_ids = set(bas.SAMPLE_LEAGUES.values())

    # Resume prior opposing mixes
    pitcher_mixes: dict[str, dict] = {}
    ck = bas.OUT / "advance_opposing_pitchers.json"
    if ck.is_file():
        prev = json.loads(ck.read_text())
        for row in prev.get("pitchers") or []:
            sid = str(row.get("synergy_id") or "")
            if sid and row.get("pitches"):
                pitcher_mixes[sid] = row

    filled: list[dict] = []
    empty: list[dict] = []
    unresolved: list[dict] = []
    events_by_league: dict[str, int] = defaultdict(int)

    for i, p in enumerate(sorted(missing, key=lambda r: (r.get("team") or "", r.get("name") or "")), 1):
        name = p.get("name") or "?"
        team = p.get("team") or "?"
        try:
            mid = int(p.get("player_id") or 0) or None
        except (TypeError, ValueError):
            mid = None
        sid = resolve_sid(token, name, mid)
        if not sid:
            unresolved.append({"name": name, "team": team, "mlb_id": mid, "reason": "synergy_id_not_found"})
            print(f"[{i}/{len(missing)}] {team} {name} UNRESOLVED")
            continue
        if sid in pitcher_mixes and (pitcher_mixes[sid].get("pitches") or 0) >= 20:
            print(f"[{i}/{len(missing)}] {team} {name} already in opposing pull pitches={pitcher_mixes[sid].get('pitches')}")
            filled.append({"name": name, "team": team, "mlb_id": mid, "synergy_id": sid, "via": "checkpoint"})
            continue
        print(f"[{i}/{len(missing)}] {team} {name} pitcherId={sid}")
        raw = bas.fetch_events_pages(token, pitcher_id=sid, max_events=max_events)
        kept = bas.filter_events_by_leagues_years(raw, league_ids=target_league_ids, years=year_set)
        for ev in kept:
            lid = bas.event_league_id(ev)
            for abbr, lid0 in bas.SAMPLE_LEAGUES.items():
                if lid == lid0:
                    events_by_league[abbr] += 1
                    break
        packed = bas.aggregate_pitcher_count_mixes(kept)
        mix = packed.get(sid) or (next(iter(packed.values())) if packed else None)
        if not mix or not mix.get("pitches"):
            empty.append(
                {
                    "name": name,
                    "team": team,
                    "mlb_id": mid,
                    "synergy_id": sid,
                    "raw": len(raw),
                    "kept": len(kept),
                    "reason": "no_events_in_2025_2026_leagues",
                }
            )
            print(f"  EMPTY raw={len(raw)} kept={len(kept)}")
            continue
        mix = dict(mix)
        mix["synergy_id"] = sid
        mix["name"] = name
        mix["team"] = team
        if mid:
            mix["mlb_id"] = mid
        pitcher_mixes[sid] = mix
        cm = mix.get("count_mix") or {}
        hn = mix.get("hand_n") or {}
        print(
            f"  pitches={mix.get('pitches')} 0-0={sum(x.get('pitches') or 0 for x in (cm.get('0-0') or []))} "
            f"2K={sum(x.get('pitches') or 0 for x in (cm.get('2_strikes') or []))} "
            f"LHH={hn.get('vs_LHH')} RHH={hn.get('vs_RHH')} leagues={mix.get('leagues')}"
        )
        filled.append(
            {
                "name": name,
                "team": team,
                "mlb_id": mid,
                "synergy_id": sid,
                "pitches": mix.get("pitches"),
                "leagues": mix.get("leagues"),
            }
        )
        time.sleep(0.08)

    # Persist opposing pitchers + merge (PS movement preserved by merge_synergy_count_into_arsenals)
    prior_empty = []
    season_meta = None
    if ck.is_file():
        try:
            old = json.loads(ck.read_text())
            prior_empty = list(old.get("empty") or [])
            season_meta = old.get("season_meta")
        except Exception:  # noqa: BLE001
            pass
    empty_by = {str(e.get("synergy_id") or e.get("name")): e for e in prior_empty}
    for e in empty:
        empty_by[str(e.get("synergy_id") or e.get("name"))] = e
    ck.write_text(
        json.dumps(
            {
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": (
                    "Synergy events/filter pitcherId · PR roster + movement-only fill · "
                    "LBPRC + MLB/MiLB/LMB 2025–2026"
                ),
                "season_meta": season_meta,
                "pitchers": sorted(pitcher_mixes.values(), key=lambda r: -(r.get("pitches") or 0)),
                "empty": list(empty_by.values()),
                "note": "Movement-only fill via pitcherId after /api/players/search resolve.",
                "sample": {
                    "pitchers": len(pitcher_mixes),
                    "fill_filled": len(filled),
                    "fill_empty": len(empty),
                    "fill_unresolved": len(unresolved),
                    "events_by_league_fill": dict(events_by_league),
                },
            },
            indent=2,
        )
    )
    stats = bas.merge_synergy_count_into_arsenals(pitcher_mixes)
    report = {
        "filled": filled,
        "empty": empty,
        "unresolved": unresolved,
        "merge_stats": stats,
        "events_by_league": dict(events_by_league),
    }
    Path("runs").mkdir(exist_ok=True)
    (Path("runs") / "movement_only_fill_report.json").write_text(json.dumps(report, indent=2))
    print("REPORT filled", len(filled), "empty", len(empty), "unresolved", len(unresolved))
    print("merge", stats)


if __name__ == "__main__":
    main()

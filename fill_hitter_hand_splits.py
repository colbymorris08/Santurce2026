#!/usr/bin/env python3
"""Fill vs-R / vs-L (+ RISP×hand) pitch tables onto existing advance_opposing_hitters.json.

Uses Synergy pitcherInfo.pitchingSide. Skips roster rediscovery — reuses synergy_ids
already on disk. Does not rewrite pitcher advance files.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from build_advance_synergy import (
    OUT,
    SAMPLE_LEAGUES,
    WINTER_LEAGUE_IDS,
    aggregate_events,
    attach_summer_sb_bunts,
    event_league_id,
    fetch_events_pages,
    filter_events_by_leagues_years,
    load_season_games,
    login_token,
    merge_spray_into_pregame,
    select_all_aggregate,
    serialize_hitters,
    sample_stats,
    strip_hitter_chart_junk_pitches,
)

HAND_KEYS = (
    "vs_r_by_pitch_type",
    "vs_l_by_pitch_type",
    "risp_vs_r_by_pitch_type",
    "risp_vs_l_by_pitch_type",
)


def main() -> None:
    path = OUT / "advance_opposing_hitters.json"
    data = json.loads(path.read_text())
    hitters = list(data.get("hitters") or [])
    if not hitters:
        raise SystemExit("no hitters in advance_opposing_hitters.json")

    token = login_token(headed=False)
    print("Synergy login ok")
    year_set = set((data.get("season_meta") or {}).get("target_years") or [2025, 2026])
    if not year_set:
        year_set = {2025, 2026}
    target_league_ids = set(SAMPLE_LEAGUES.values())
    season_games = load_season_games()
    max_events = int(data.get("max_events_per_player") or 2500)

    all_raw: dict = {}
    winter_raw: dict = {}
    all_events: list = []
    winter_events: list = []
    events_by_league: dict[str, int] = defaultdict(int)

    for i, h in enumerate(hitters, 1):
        bid = str(h.get("synergy_id") or "")
        name = h.get("name") or bid
        team = h.get("team") or "?"
        if not bid:
            print(f"=== [{i}/{len(hitters)}] skip (no synergy_id) {name}")
            continue
        print(f"=== [{i}/{len(hitters)}] {team} {name}")
        raw_events = fetch_events_pages(token, batter_id=bid, max_events=max_events)
        kept = filter_events_by_leagues_years(
            raw_events, league_ids=target_league_ids, years=year_set
        )
        winter = filter_events_by_leagues_years(
            kept, league_ids=WINTER_LEAGUE_IDS, years=year_set
        )
        raw = aggregate_events(kept, team)
        wraw = aggregate_events(winter, team)
        if bid in raw:
            raw[bid]["name"] = name
            raw[bid]["team"] = team
        if bid in wraw:
            wraw[bid]["name"] = name
            wraw[bid]["team"] = team
        # merge into accumulators
        for src, dest in ((raw, all_raw), (wraw, winter_raw)):
            for k, v in src.items():
                if k not in dest:
                    dest[k] = v
                else:
                    # reuse merge from builder
                    from build_advance_synergy import merge_hitter_raw

                    merge_hitter_raw(dest, {k: v})
        all_events.extend(kept)
        winter_events.extend(winter)
        for ev in kept:
            lid = event_league_id(ev)
            for abbr, lid0 in SAMPLE_LEAGUES.items():
                if lid == lid0:
                    events_by_league[abbr] += 1
                    break
        # quick hand pitch counts for log
        if bid in raw:
            vr = sum(int(b.get("pitches") or 0) for p, b in raw[bid]["vs_r_by_pitch"].items() if not str(p).startswith("__"))
            vl = sum(int(b.get("pitches") or 0) for p, b in raw[bid]["vs_l_by_pitch"].items() if not str(p).startswith("__"))
            print(f"  kept={len(kept)} winter={len(winter)} vsR_pitches={vr} vsL_pitches={vl}")
        time.sleep(0.05)

    mlb_ids = {}
    for h in hitters:
        if h.get("mlb_id") and h.get("name"):
            from build_advance_synergy import _norm_person

            mlb_ids[_norm_person(h["name"])] = int(h["mlb_id"])

    fresh = serialize_hitters(all_raw, season_games, mlb_ids)
    by_id = {str(h.get("synergy_id")): h for h in fresh}

    # Preserve identity/SB fields from prior file; replace pitch + spray from rebuild
    out_hitters = []
    for old in hitters:
        bid = str(old.get("synergy_id") or "")
        neu = by_id.get(bid)
        if not neu:
            # keep old but clear empty hand keys so UI doesn't lie
            for k in HAND_KEYS:
                old.setdefault(k, [])
            out_hitters.append(old)
            continue
        # keep SB season fields from previous if present
        for k in (
            "sb_total", "sb_cs", "sb_per_162", "sb_source",
            "summer_sb", "summer_cs", "summer_bunts", "summer_games", "summer_sb_source",
            "mlb_id", "headshot_url", "team", "name", "bats",
        ):
            if old.get(k) is not None and not neu.get(k):
                neu[k] = old[k]
            elif old.get(k) is not None and k in (
                "sb_total", "sb_cs", "sb_per_162", "sb_source",
                "summer_sb", "summer_cs", "summer_bunts", "summer_games", "summer_sb_source",
            ):
                neu[k] = old[k]
        if old.get("mlb_id") and not neu.get("headshot_url"):
            neu["mlb_id"] = old["mlb_id"]
            neu["headshot_url"] = old.get("headshot_url")
        out_hitters.append(neu)

    n_summer = attach_summer_sb_bunts(out_hitters)
    n_junk = strip_hitter_chart_junk_pitches(out_hitters)
    print(f"summer_sb_bunts attached={n_summer} junk_pitch_rows_removed={n_junk}")

    select_all = select_all_aggregate(out_hitters)
    sample_full = sample_stats(out_hitters, all_events)
    winter_hitters = serialize_hitters(winter_raw, season_games, mlb_ids)
    sample_winter = sample_stats(winter_hitters, winter_events)
    sample_full.update(
        {
            "max_events_per_player": max_events,
            "season_years": sorted(year_set),
            "events_by_league": dict(events_by_league),
            "hand_filter": "pitcherInfo.pitchingSide",
        }
    )

    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data["hitters"] = out_hitters
    data["select_all"] = select_all
    data["sample"] = sample_full
    data["sample_winter_only"] = sample_winter
    data["events_by_league"] = dict(events_by_league)
    notes = list(data.get("notes") or [])
    note = "vs R / vs L (+ RISP×hand) from Synergy pitcherInfo.pitchingSide (Right/Left)."
    if note not in notes:
        notes.append(note)
    data["notes"] = notes
    path.write_text(json.dumps(data, indent=2))
    print(f"wrote {path} hitters={len(out_hitters)} pitches={sample_full.get('pitches')}")

    # Refresh synergy spray charts from rebuilt hitters
    try:
        merge_spray_into_pregame(out_hitters)
        print("merged spray into pregame_spray_charts.json")
    except Exception as exc:
        print("spray merge skipped:", exc)

    # summary
    both = 0
    sample = None
    for h in out_hitters:
        vr = sum(int(r.get("pitches") or 0) for r in (h.get("vs_r_by_pitch_type") or []))
        vl = sum(int(r.get("pitches") or 0) for r in (h.get("vs_l_by_pitch_type") or []))
        if vr and vl:
            both += 1
            if sample is None or min(vr, vl) > min(sample[2], sample[3]):
                sample = (h.get("name"), h.get("team"), vr, vl)
    print(f"hitters_with_both_hands={both} sample={sample}")
    print("DONE")


if __name__ == "__main__":
    main()

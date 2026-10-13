#!/usr/bin/env python3
"""Fill only movement-plot support gaps from targeted Synergy pitcherId pulls."""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import build_advance_synergy as bas

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
CHECKPOINT = RUNS / "pitcher_plot_support_checkpoint.json"
REPORT = RUNS / "pitcher_plot_support_fill_report.json"


def load(name: str):
    return json.loads((DATA / name).read_text())


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def movement_rows(row: dict | None) -> list[dict]:
    return [
        pitch
        for pitch in ((row or {}).get("arsenal") or [])
        if pitch.get("ivb") is not None
        and (
            pitch.get("hb_pitcher") is not None
            or pitch.get("hb_hitter") is not None
            or pitch.get("hb") is not None
        )
    ]


def side_has_ab(side: dict | None) -> bool:
    return bool(side and sum(int(value or 0) for value in (side.get("ab") or [])) > 0)


def count_complete(row: dict | None) -> bool:
    cm = (row or {}).get("count_mix") or {}
    ubc = (row or {}).get("usage_by_count") or {}
    zero = cm.get("0-0") or ubc.get("0-0") or []
    two = cm.get("2_strikes") or [
        pitch
        for count in ("0-2", "1-2", "2-2", "3-2")
        for pitch in (ubc.get(count) or [])
    ]
    return bool(zero and two)


def heat_key(player: dict, row: dict, heat: dict) -> str:
    for candidate in (
        player.get("id"),
        row.get("player_id"),
        row.get("mlb_id"),
        row.get("synergy_id"),
    ):
        if candidate is not None and str(candidate) in heat:
            return str(candidate)
    player_id = int(row.get("player_id") or 0)
    return str(player_id if player_id > 0 else row.get("synergy_id") or player_id)


def audit(roster: list[dict], arsenal_by: dict, heat: dict) -> dict:
    counts = {
        "roster": len(roster),
        "movement_plot": 0,
        "whiff_rate": 0,
        "whiff_all_arsenal_rows": 0,
        "plot_whiff_complete": 0,
        "count": 0,
        "heat_map": 0,
        "all": 0,
    }
    gaps = []
    for player in roster:
        row = arsenal_by.get((player["team"], norm(player["name"])))
        arsenal = (row or {}).get("arsenal") or []
        plotted = movement_rows(row)
        has_plot = bool(plotted)
        has_whiff = bool(plotted and all(pitch.get("whiff") is not None for pitch in plotted))
        has_count = count_complete(row)
        rec = heat.get(heat_key(player, row or {}, heat)) if row else None
        has_heat = bool(rec and side_has_ab(rec.get("vs_r")) and side_has_ab(rec.get("vs_l")))
        counts["movement_plot"] += int(has_plot)
        counts["whiff_rate"] += int(bool(arsenal and any(pitch.get("whiff") is not None for pitch in arsenal)))
        counts["whiff_all_arsenal_rows"] += int(
            bool(arsenal and all(pitch.get("whiff") is not None for pitch in arsenal))
        )
        counts["plot_whiff_complete"] += int(has_whiff)
        counts["count"] += int(has_count)
        counts["heat_map"] += int(has_heat)
        counts["all"] += int(has_plot and has_whiff and has_count and has_heat)
        if has_plot and not (has_whiff and has_count and has_heat):
            gaps.append(
                {
                    "name": player["name"],
                    "team": player["team"],
                    "player_id": player["id"],
                    "missing_whiff": not has_whiff,
                    "missing_count": not has_count,
                    "missing_heat": not has_heat,
                }
            )
    return {"counts": counts, "plot_gaps": gaps}


def pack_player(events: list[dict], scan: dict) -> dict:
    whiff = defaultdict(lambda: {"pitches": 0, "swings": 0, "whiffs": 0})
    for event in events:
        pitch = event.get("pitch") or {}
        code = bas._pitch_code(pitch.get("pitchKind"))
        if not code or code in bas._CHART_SKIP_CODES:
            continue
        item = whiff[code]
        item["pitches"] += 1
        result = pitch.get("pitchResult")
        item["swings"] += int(bas._is_swing(result))
        item["whiffs"] += int(bas._is_whiff(result))

    eligible = bas.filter_events_by_leagues_years(
        events,
        league_ids=set(bas.SAMPLE_LEAGUES.values()),
        years={2024, 2025, 2026},
    )
    packed_count = bas.aggregate_pitcher_count_mixes(eligible)
    mix = next(iter(packed_count.values()), {})
    windows = {
        "2026 summer": [
            event
            for event in events
            if bas.event_season_year(event) == 2026
            and bas.event_league_id(event) in bas.SUMMER_LEAGUE_IDS
        ],
        "2025": [
            event
            for event in events
            if bas.event_season_year(event) == 2025
            and bas.event_league_id(event) in (bas.SUMMER_LEAGUE_IDS | bas.WINTER_LEAGUE_IDS)
        ],
        "2024": [event for event in events if bas.event_season_year(event) == 2024],
    }
    return {
        "complete": bool(scan.get("complete")),
        "scanned": int(scan.get("scanned") or 0),
        "whiff": dict(whiff),
        "count_mix": mix.get("count_mix") or {},
        "heat": {
            label: {
                **bas.aggregate_location_heat(rows, split="batter_hand"),
                "sources": bas._located_sources(rows),
            }
            for label, rows in windows.items()
        },
    }


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    rosters = load("lbprc_2025_rosters.json")
    arsenal_doc = load("pregame_pitcher_arsenals.json")
    heat_doc = load("advance_heatmaps.json")
    heat = heat_doc.setdefault("pitchers", {})
    roster = [
        {"team": team.get("teamAbbrev"), "name": player.get("name"), "id": int(player.get("id") or 0)}
        for team in rosters.values()
        if team.get("teamAbbrev") != "SAN"
        for player in (team.get("players") or [])
        if player.get("position") == "P"
    ]
    arsenal_by = {
        (row.get("team"), norm(row.get("name"))): row
        for row in (arsenal_doc.get("pitchers") or [])
    }
    before = audit(roster, arsenal_by, heat)
    packet_keys = {
        (row.get("team"), norm(row.get("name")))
        for row in (load("advance_opposing_pitchers.json").get("pitchers") or [])
    }
    packet_roster = [player for player in roster if (player["team"], norm(player["name"])) in packet_keys]
    packet_before = audit(packet_roster, arsenal_by, heat)
    targets = before["plot_gaps"]
    checkpoint = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.is_file() else {"pitchers": {}}
    token = bas.login_token(headed=False)

    for index, target in enumerate(targets, 1):
        row = arsenal_by[(target["team"], norm(target["name"]))]
        sid = str(row.get("synergy_id") or "")
        if not sid:
            target["result"] = "missing Synergy pitcherId"
            continue
        cached = checkpoint["pitchers"].get(sid)
        if not cached or not cached.get("complete"):
            print(f"=== [{index}/{len(targets)}] {target['team']} {target['name']}", flush=True)
            scan: dict = {}
            events = bas.fetch_events_pages(
                token,
                pitcher_id=sid,
                max_events=7500,
                take=200,
                pause_s=0.02,
                timeout_s=20,
                max_attempts=3,
                stop_before_year=2024,
                retain=bas.event_in_heat_fallback_scan,
                max_scan=30000,
                scan_out=scan,
            )
            cached = pack_player(events, scan)
            cached.update({"name": target["name"], "team": target["team"], "synergy_id": sid})
            checkpoint["pitchers"][sid] = cached
            checkpoint["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            CHECKPOINT.write_text(json.dumps(checkpoint, indent=2) + "\n")

        filled_whiff = []
        whiff_by = cached.get("whiff") or {}
        for pitch in movement_rows(row):
            if pitch.get("whiff") is not None:
                continue
            code = bas._pitch_code(pitch.get("type") or pitch.get("code"))
            sample = whiff_by.get(code)
            if not sample:
                sample = bas._lookup_pitch_row(whiff_by, code)
            if not sample or int(sample.get("swings") or 0) < 1:
                continue
            swings = int(sample["swings"])
            value = round(100.0 * int(sample.get("whiffs") or 0) / swings, 1)
            pitch["whiff"] = value
            pitch["whiff_source"] = "Synergy pitcherId events · 2024–2026 fallback sample"
            filled_whiff.append({"type": code, "whiff": value, "swings": swings})

        filled_count = []
        if not count_complete(row):
            current = dict(row.get("count_mix") or {})
            pulled = cached.get("count_mix") or {}
            for bucket in ("0-0", "2_strikes"):
                if not current.get(bucket) and pulled.get(bucket):
                    current[bucket] = pulled[bucket]
                    filled_count.append(bucket)
            if filled_count:
                row["count_mix"] = current
                row["synergy_count_source"] = "Synergy events/filter pitcherId · targeted plot support fill"

        key = heat_key(
            {"id": target["player_id"], "name": target["name"], "team": target["team"]},
            row,
            heat,
        )
        rec = heat.get(key) or {
            "name": target["name"],
            "team": target["team"],
            "synergy_id": sid,
            "mlb_id": target["player_id"] if target["player_id"] > 0 else None,
        }
        filled_heat = []
        side_windows = dict(rec.get("side_windows") or {})
        for side in ("vs_r", "vs_l"):
            if side_has_ab(rec.get(side)):
                side_windows.setdefault(side, rec.get("window_used") or "existing")
                continue
            for label in ("2026 summer", "2025", "2024"):
                candidate = ((cached.get("heat") or {}).get(label) or {}).get(side)
                if side_has_ab(candidate):
                    rec[side] = candidate
                    side_windows[side] = label
                    filled_heat.append({"side": side, "window": label, "ab": sum(candidate["ab"])})
                    break
        if filled_heat:
            rec["located"] = sum(int((rec.get(side) or {}).get("located") or 0) for side in ("vs_r", "vs_l"))
            rec["side_windows"] = side_windows
            rec["fallback_checked"] = True
            rec["fallback_version"] = bas.HEAT_FALLBACK_VERSION
            rec["complete"] = bool(cached.get("complete"))
            rec["method_note"] = "Missing batter-hand side filled without replacing an existing good side."
            heat[key] = rec

        target["synergy_id"] = sid
        target["filled_whiff"] = filled_whiff
        target["filled_count"] = filled_count
        target["filled_heat"] = filled_heat

    (DATA / "pregame_pitcher_arsenals.json").write_text(
        json.dumps(arsenal_doc, indent=2, ensure_ascii=False) + "\n"
    )
    heat_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    temp = DATA / "advance_heatmaps.json.tmp"
    temp.write_text(json.dumps(heat_doc, separators=(",", ":"), ensure_ascii=False))
    temp.replace(DATA / "advance_heatmaps.json")
    after = audit(roster, arsenal_by, heat)
    packet_after = audit(packet_roster, arsenal_by, heat)
    report = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "definitions": {
            "movement_plot": "At least one arsenal row with IVB and HB, matching the advance page XY predicate.",
            "whiff_rate": "At least one arsenal row has a whiff rate; plot_whiff_complete requires every plotted row.",
            "count": "Both 0-0 and 2-strike pitch mixes are present.",
            "heat_map": "Both SLG vs R and SLG vs L have at least one terminal-pitch AB.",
        },
        "before": before,
        "site_packet_before": packet_before,
        "fills": targets,
        "after": after,
        "site_packet_after": packet_after,
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

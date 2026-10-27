#!/usr/bin/env python3
"""Fill blank pitcher whiff/miss fields only when existing Synergy events support them."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import build_advance_synergy as bas

ARSENAL_PATH = bas.OUT / "pregame_pitcher_arsenals.json"
REPORT_PATH = Path("runs/synergy_whiff_gap_fill_report.json")


def _row_total(rows: list[dict] | None) -> int:
    return sum(int(row.get("pitches") or 0) for row in rows or [])


def _has_counts(player: dict) -> bool:
    count_mix = player.get("count_mix") or player.get("usage_by_count") or {}
    return bool(_row_total(count_mix.get("0-0")) and _row_total(count_mix.get("2_strikes")))


def _has_whiff(player: dict) -> bool:
    return any(row.get("whiff") is not None for row in player.get("arsenal") or [])


def _select_window(events: list[dict]) -> tuple[list[dict], str]:
    primary = [
        event
        for event in events
        if (
            bas.event_season_year(event) == 2026
            and bas.event_league_id(event) in bas.SUMMER_LEAGUE_IDS
        )
        or (
            bas.event_season_year(event) == 2025
            and bas.event_league_id(event) in bas.WINTER_LEAGUE_IDS
        )
    ]
    fallbacks = [
        (
            "2025 fallback",
            [
                event
                for event in events
                if bas.event_season_year(event) == 2025
                and bas.event_league_id(event)
                in (bas.SUMMER_LEAGUE_IDS | bas.WINTER_LEAGUE_IDS)
            ],
        ),
        ("2024 fallback", [event for event in events if bas.event_season_year(event) == 2024]),
    ]
    for label, rows in [("2026 summer + LBPRC 2025", primary), *fallbacks]:
        if any(
            bas._pitch_code((event.get("pitch") or {}).get("pitchKind"))
            not in bas._CHART_SKIP_CODES
            for event in rows
        ):
            return rows, label
    return [], "none"


def _aggregate(events: list[dict]) -> dict[str, dict]:
    buckets: dict[str, dict] = defaultdict(
        lambda: {"pitches": 0, "swings": 0, "whiffs": 0, "L": [0, 0], "R": [0, 0]}
    )
    for event in events:
        pitch = event.get("pitch") or {}
        code = bas._pitch_code(pitch.get("pitchKind"))
        if not code or code in bas._CHART_SKIP_CODES:
            continue
        result = pitch.get("pitchResult")
        side = bas._bat_side(event)
        bucket = buckets[code]
        bucket["pitches"] += 1
        if bas._is_swing(result):
            bucket["swings"] += 1
            if side in ("L", "R"):
                bucket[side][1] += 1
            if bas._is_whiff(result):
                bucket["whiffs"] += 1
                if side in ("L", "R"):
                    bucket[side][0] += 1
    return dict(buckets)


def _lookup(stats: dict[str, dict], code: str) -> dict | None:
    normalized = bas._pitch_code(code)
    if normalized in stats:
        return stats[normalized]
    matches = [stats[alias] for alias in bas._PITCH_TYPE_ALIASES.get(normalized, ()) if alias in stats]
    return matches[0] if len(matches) == 1 else None


def main() -> None:
    document = json.loads(ARSENAL_PATH.read_text())
    targets = [
        player
        for player in document.get("pitchers") or []
        if _has_counts(player) and not _has_whiff(player) and player.get("synergy_id")
    ]
    token = bas.login_token(headed=False)
    report = {"updated": None, "targets": [], "filled": [], "empty": []}

    for index, player in enumerate(targets, 1):
        name = str(player.get("name") or "")
        synergy_id = str(player["synergy_id"])
        print(f"[{index}/{len(targets)}] {player.get('team')} {name} pitcherId={synergy_id}")
        scan: dict = {}
        events = bas.fetch_events_pages(
            token,
            pitcher_id=synergy_id,
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
        selected, window = _select_window(events)
        stats = _aggregate(selected)
        changed = []
        for arsenal_row in player.get("arsenal") or []:
            values = _lookup(stats, str(arsenal_row.get("type") or ""))
            if not values:
                continue
            if arsenal_row.get("whiff") is None and values["swings"]:
                arsenal_row["whiff"] = round(100 * values["whiffs"] / values["swings"], 1)
                changed.append(str(arsenal_row.get("type") or ""))
            for side, field in (("L", "miss_l"), ("R", "miss_r")):
                whiffs, swings = values[side]
                if arsenal_row.get(field) is None and swings:
                    arsenal_row[field] = round(100 * whiffs / swings, 1)
        entry = {
            "name": name,
            "team": player.get("team"),
            "synergy_id": synergy_id,
            "scanned": int(scan.get("scanned") or 0),
            "selected": len(selected),
            "window": window,
            "pitch_types_filled": changed,
        }
        report["targets"].append(entry)
        if changed:
            player["synergy_whiff_source"] = "Synergy events/filter pitcherId · whiffs/swings"
            player["synergy_whiff_window"] = window
            player["synergy_whiff_events"] = len(selected)
            report["filled"].append(entry)
        else:
            report["empty"].append(entry)
        report["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ARSENAL_PATH.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        REPORT_PATH.parent.mkdir(exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print(f"filled={len(report['filled'])}/{len(targets)} empty={len(report['empty'])}")


if __name__ == "__main__":
    main()

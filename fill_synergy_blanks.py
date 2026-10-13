#!/usr/bin/env python3
"""Fill blank Synergy pieces without replacing data already on the roster.

Pitchers: whiff/miss, 0-0 and 2K count mix, SLG heat vs R and vs L.
Hitters: empty advance-table splits (whiff lives on those rows), count splits,
and vs-R/vs-L SLG + whiff heat.

Windows, newest first: 2026 summer, else 2025, else 2024.
Missing count buckets prefer LBPRC winter 2025, then that same cascade.
"""

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
CHECKPOINT = RUNS / "synergy_blank_fill_checkpoint.json"
REPORT = RUNS / "synergy_blank_fill_report.json"

HITTER_TABLES = {
    "vs_r_by_pitch_type": ("vs_r_by_pitch", 5),
    "vs_l_by_pitch_type": ("vs_l_by_pitch", 5),
    "non_risp_vs_r_by_pitch_type": ("non_risp_vs_r_by_pitch", 5),
    "non_risp_vs_l_by_pitch_type": ("non_risp_vs_l_by_pitch", 5),
    "risp_vs_r_by_pitch_type": ("risp_vs_r_by_pitch", 3),
    "risp_vs_l_by_pitch_type": ("risp_vs_l_by_pitch", 3),
    "pre2k_vs_r_by_pitch_type": ("pre2k_vs_r_by_pitch", 5),
    "pre2k_vs_l_by_pitch_type": ("pre2k_vs_l_by_pitch", 5),
    "twok_vs_r_by_pitch_type": ("twok_vs_r_by_pitch", 3),
    "twok_vs_l_by_pitch_type": ("twok_vs_l_by_pitch", 3),
}
COUNT_TABLES = (
    "pre2k_vs_r_by_pitch_type",
    "pre2k_vs_l_by_pitch_type",
    "twok_vs_r_by_pitch_type",
    "twok_vs_l_by_pitch_type",
)
ADVANCE_TABLES = (
    "non_risp_vs_r_by_pitch_type",
    "non_risp_vs_l_by_pitch_type",
    "risp_vs_r_by_pitch_type",
    "risp_vs_l_by_pitch_type",
    *COUNT_TABLES,
)


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def load(name: str):
    return json.loads((DATA / name).read_text())


def pitches_of(rows) -> int:
    return sum(int((row or {}).get("pitches") or 0) for row in rows or [])


def side_sum(side: dict | None, field: str) -> int:
    if not side:
        return 0
    return sum(int(value or 0) for value in (side.get(field) or []))


def count_complete(row: dict | None) -> bool:
    cm = (row or {}).get("count_mix") or {}
    ubc = (row or {}).get("usage_by_count") or {}
    zero = cm.get("0-0") or ubc.get("0-0") or []
    two = list(cm.get("2_strikes") or [])
    if not pitches_of(two):
        two = [
            pitch
            for count in ("0-2", "1-2", "2-2", "3-2")
            for pitch in (ubc.get(count) or [])
        ]
    return pitches_of(zero) > 0 and pitches_of(two) > 0


def chartable(events: list[dict]) -> bool:
    return any(
        bas._pitch_code((event.get("pitch") or {}).get("pitchKind")) not in bas._CHART_SKIP_CODES
        for event in events
    )


def window_rows(events: list[dict]) -> dict[str, list[dict]]:
    summer_2026 = [
        event
        for event in events
        if bas.event_season_year(event) == 2026 and bas.event_league_id(event) in bas.SUMMER_LEAGUE_IDS
    ]
    year_2025 = [
        event
        for event in events
        if bas.event_season_year(event) == 2025
        and bas.event_league_id(event) in (bas.SUMMER_LEAGUE_IDS | bas.WINTER_LEAGUE_IDS)
    ]
    year_2024 = [event for event in events if bas.event_season_year(event) == 2024]
    winter = [
        event
        for event in events
        if bas.event_season_year(event) == 2025 and bas.event_league_id(event) in bas.WINTER_LEAGUE_IDS
    ]
    return {
        "2026 summer": summer_2026,
        "2025": year_2025,
        "2024": year_2024,
        "LBPRC winter": winter,
    }


def first_chart_window(windows: dict[str, list[dict]]) -> tuple[str, list[dict]]:
    for label in ("2026 summer", "2025", "2024"):
        rows = windows[label]
        if chartable(rows):
            return label, rows
    return "none", []


def whiff_stats(events: list[dict]) -> dict[str, dict]:
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


def lookup_whiff(stats: dict[str, dict], code: str) -> dict | None:
    normalized = bas._pitch_code(code)
    if normalized in stats:
        return stats[normalized]
    return bas._lookup_pitch_row(stats, normalized)


def count_from(events: list[dict]) -> dict:
    packed = bas.aggregate_pitcher_count_mixes(events)
    mix = next(iter(packed.values()), {})
    return mix.get("count_mix") or {}


def heat_sides(events: list[dict], split: str) -> dict:
    heat = bas.aggregate_location_heat(events, split=split)
    return {"vs_r": heat.get("vs_r"), "vs_l": heat.get("vs_l"), "sources": bas._located_sources(events)}


def side_ready(side: dict | None, *, hitter: bool) -> bool:
    if side_sum(side, "ab") <= 0:
        return False
    if hitter and side_sum(side, "swings") <= 0:
        return False
    return True


def pack_hitter_tables(events: list[dict], team: str) -> dict[str, list]:
    raw = bas.aggregate_events(events, team)
    hitter = next(iter(raw.values()), None)
    if not hitter:
        return {}
    packed: dict[str, list] = {}
    for dest, (src, minimum) in HITTER_TABLES.items():
        rows = []
        for pitch_type, bucket in (hitter.get(src) or {}).items():
            if str(pitch_type).startswith("__"):
                continue
            code = bas._pitch_code(pitch_type)
            if code in bas._CHART_SKIP_CODES:
                continue
            frozen = bas._freeze_bucket(bucket)
            if int(frozen.get("pitches") or 0) < minimum:
                continue
            rows.append({"pitch_type": pitch_type, **frozen})
        rows.sort(key=lambda row: -int(row.get("pitches") or 0))
        packed[dest] = rows
    overall = bas._pa_bucket()
    for pitch_type, bucket in (hitter.get("by_pitch") or {}).items():
        if str(pitch_type).startswith("__"):
            continue
        for field in (
            "pitches", "pa", "ab", "h", "tb", "bb", "hbp", "sf", "gb", "bip",
            "first_pitches", "first_pitch_swings", "swings", "whiffs", "bunts",
        ):
            overall[field] = int(overall.get(field) or 0) + int(bucket.get(field) or 0)
        overall["games"] |= set(bucket.get("games") or [])
    packed["_overall"] = bas._freeze_bucket(overall)
    return packed


def roster_players() -> tuple[list[dict], list[dict]]:
    rosters = load("lbprc_2025_rosters.json")
    pitchers, hitters = [], []
    for team in rosters.values():
        if team.get("teamAbbrev") == "SAN":
            continue
        for player in team.get("players") or []:
            row = {
                "team": team.get("teamAbbrev"),
                "name": player.get("name"),
                "id": int(player.get("id") or 0),
            }
            if player.get("position") == "P":
                pitchers.append(row)
            else:
                hitters.append(row)
    return pitchers, hitters


def player_key(row: dict) -> tuple[str, str]:
    return str(row.get("team") or ""), norm(str(row.get("name") or ""))


def heat_key(player: dict, row: dict, heat: dict) -> str:
    for candidate in (
        player.get("id"),
        (row or {}).get("player_id"),
        (row or {}).get("mlb_id"),
        (row or {}).get("synergy_id"),
    ):
        if candidate is not None and str(candidate) in heat:
            return str(candidate)
    player_id = int((row or {}).get("player_id") or player.get("id") or 0)
    if player_id > 0:
        return str(player_id)
    return str((row or {}).get("synergy_id") or player_id)


BROAD_LEAGUE_SCAN_IDS = {
    "67e6d014be559430bd271882",  # Haruse Narita (Japan / non-MLB Synergy leagues)
    "681d6486373abc5e4678b3f2",  # Keishin Saitoh
    "67d4c6f2a94c63becc93c932",  # Yuta Nakami
}


def _retain_for_synergy_id(synergy_id: str | None):
    if synergy_id in BROAD_LEAGUE_SCAN_IDS:
        return lambda ev: bas.event_season_year(ev) in {2024, 2025, 2026}
    return bas.event_in_heat_fallback_scan


def fetch_player(
    token: str,
    *,
    pitcher_id: str | None = None,
    batter_id: str | None = None,
    synergy_id: str | None = None,
) -> tuple[str, list[dict], dict]:
    retain = _retain_for_synergy_id(synergy_id or pitcher_id or batter_id)
    scan: dict = {}
    events = bas.fetch_events_pages(
        token,
        pitcher_id=pitcher_id,
        batter_id=batter_id,
        max_events=7500,
        take=200,
        pause_s=0.02,
        timeout_s=20,
        max_attempts=3,
        stop_before_year=2024,
        retain=retain,
        max_scan=30000,
        scan_out=scan,
    )
    if int(scan.get("scanned") or 0) == 0:
        print("  empty first pull — re-login and retry once", flush=True)
        tok_path = Path("/tmp/synergy_token.txt")
        if tok_path.exists():
            tok_path.unlink()
        token = bas.login_token(headed=False)
        scan = {}
        events = bas.fetch_events_pages(
            token,
            pitcher_id=pitcher_id,
            batter_id=batter_id,
            max_events=7500,
            take=200,
            pause_s=0.02,
            timeout_s=20,
            max_attempts=3,
            stop_before_year=2024,
            retain=retain,
            max_scan=30000,
            scan_out=scan,
        )
    return token, events, scan


def save_json(path: Path, doc: dict, *, compact: bool = False) -> None:
    if compact:
        text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def apply_pitcher(row: dict, player: dict, packed: dict, heat: dict) -> dict:
    filled = {"whiff": [], "miss": [], "count": [], "heat": []}
    stats = packed.get("whiff") or {}
    for pitch in row.get("arsenal") or []:
        code = str(pitch.get("type") or pitch.get("code") or "")
        values = lookup_whiff(stats, code)
        if not values:
            continue
        swings = int(values.get("swings") or 0)
        if pitch.get("whiff") is None and swings:
            pitch["whiff"] = round(100.0 * int(values.get("whiffs") or 0) / swings, 1)
            filled["whiff"].append(code)
        for side, field in (("L", "miss_l"), ("R", "miss_r")):
            whiffs, side_swings = values.get(side) or (0, 0)
            if pitch.get(field) is None and side_swings:
                pitch[field] = round(100.0 * int(whiffs) / int(side_swings), 1)
                filled["miss"].append(f"{code}:{field}")
    if filled["whiff"] or filled["miss"]:
        row["synergy_whiff_source"] = row.get("synergy_whiff_source") or (
            "Synergy events/filter pitcherId · whiffs/swings"
        )
        row["synergy_whiff_window"] = packed.get("stat_window")
        row["synergy_whiff_events"] = packed.get("stat_events")

    current = dict(row.get("count_mix") or {})
    arsenal_types = {
        str(pitch.get("type") or "").upper()
        for pitch in (row.get("arsenal") or [])
        if pitch.get("type")
    }
    sources = [packed.get("winter_count") or {}]
    sources.extend(packed.get("count_windows") or [])
    for bucket in ("0-0", "2_strikes", "overall"):
        if pitches_of(current.get(bucket)):
            continue
        for source in sources:
            rows = source.get(bucket) or []
            if not pitches_of(rows):
                continue
            current[bucket] = bas._remap_mix_types(rows, arsenal_types)
            filled["count"].append(bucket)
            break
    if filled["count"]:
        row["count_mix"] = {key: value for key, value in current.items() if value}
        usage = dict(row.get("usage_by_count") or {})
        for bucket in filled["count"]:
            if not pitches_of(usage.get(bucket)):
                usage[bucket] = row["count_mix"][bucket]
        row["usage_by_count"] = usage
        if not row.get("synergy_count_source"):
            row["synergy_count_source"] = "Synergy events/filter pitcherId · LBPRC winter else 2026/2025/2024"
        row["synergy_id"] = row.get("synergy_id") or packed.get("synergy_id")

    key = heat_key(player, row, heat)
    rec = heat.get(key) or {
        "name": player["name"],
        "team": player["team"],
        "synergy_id": packed.get("synergy_id"),
        "mlb_id": player["id"] if player["id"] > 0 else None,
        "window": "MLB/MiLB/LMB 2026 + LBPRC 2025",
    }
    side_windows = dict(rec.get("side_windows") or {})
    for side in ("vs_r", "vs_l"):
        if side_ready(rec.get(side), hitter=False):
            continue
        for label in ("2026 summer", "2025", "2024"):
            candidate = ((packed.get("heat") or {}).get(label) or {}).get(side)
            if side_ready(candidate, hitter=False):
                rec[side] = candidate
                side_windows[side] = label
                filled["heat"].append({"side": side, "window": label, "ab": side_sum(candidate, "ab")})
                break
    if filled["heat"]:
        rec["located"] = sum(int((rec.get(side) or {}).get("located") or 0) for side in ("vs_r", "vs_l"))
        rec["side_windows"] = side_windows
        rec["fallback_checked"] = True
        rec["fallback_version"] = bas.HEAT_FALLBACK_VERSION
        rec["method_note"] = "Blank batter-hand side filled. Existing sides were left in place."
        heat[key] = rec
    return filled


def apply_hitter(row: dict, packed: dict, heat: dict) -> dict:
    filled = {"tables": [], "overall": False, "heat": []}
    tables = packed.get("tables") or {}
    for key in HITTER_TABLES:
        if row.get(key):
            continue
        rows = tables.get(key) or []
        if rows:
            row[key] = rows
            filled["tables"].append(key)
    overall = (row.get("overall") or {})
    packed_overall = tables.get("_overall") or {}
    if not int(overall.get("pitches") or 0) and int(packed_overall.get("pitches") or 0):
        row["overall"] = packed_overall
        filled["overall"] = True
    if filled["tables"] or filled["overall"]:
        row["synergy_table_window"] = packed.get("stat_window")
        row["synergy_table_events"] = packed.get("stat_events")

    sid = str(row.get("synergy_id") or "")
    rec = heat.get(sid) or {
        "name": row.get("name"),
        "team": row.get("team"),
        "window": "MLB/MiLB/LMB 2026 + LBPRC 2025",
    }
    side_windows = dict(rec.get("side_windows") or {})
    for side in ("vs_r", "vs_l"):
        if side_ready(rec.get(side), hitter=True):
            continue
        for label in ("2026 summer", "2025", "2024"):
            candidate = ((packed.get("heat") or {}).get(label) or {}).get(side)
            if side_ready(candidate, hitter=True):
                rec[side] = candidate
                side_windows[side] = label
                filled["heat"].append({"side": side, "window": label, "ab": side_sum(candidate, "ab")})
                break
    if filled["heat"]:
        rec["located"] = sum(int((rec.get(side) or {}).get("located") or 0) for side in ("vs_r", "vs_l"))
        rec["side_windows"] = side_windows
        rec["fallback_checked"] = True
        rec["fallback_version"] = bas.HEAT_FALLBACK_VERSION
        rec["method_note"] = "Blank pitcher-hand side filled. Existing sides were left in place."
        heat[sid] = rec
    return filled


def pack_pitcher(events: list[dict], scan: dict, synergy_id: str) -> dict:
    if synergy_id in BROAD_LEAGUE_SCAN_IDS and events:
        label, selected = "2025 broad", events
        windows = window_rows(events)
    else:
        windows = window_rows(events)
        label, selected = first_chart_window(windows)
    counts = []
    for name in ("2026 summer", "2025", "2024"):
        mix = count_from(windows[name]) if chartable(windows[name]) else {}
        if mix:
            counts.append(mix)
    return {
        "complete": bool(scan.get("complete")),
        "scanned": int(scan.get("scanned") or 0),
        "synergy_id": synergy_id,
        "stat_window": label,
        "stat_events": len(selected),
        "whiff": whiff_stats(selected),
        "winter_count": count_from(windows["LBPRC winter"]) if chartable(windows["LBPRC winter"]) else {},
        "count_windows": counts,
        "heat": {
            name: heat_sides(rows, "batter_hand")
            for name, rows in windows.items()
            if name != "LBPRC winter"
        },
    }


def pack_hitter_pull(events: list[dict], scan: dict, team: str, *, synergy_id: str = "") -> dict:
    if synergy_id in BROAD_LEAGUE_SCAN_IDS and events:
        label, selected = "2025 broad", events
        windows = window_rows(events)
    else:
        windows = window_rows(events)
        label, selected = first_chart_window(windows)
    return {
        "complete": bool(scan.get("complete")),
        "scanned": int(scan.get("scanned") or 0),
        "stat_window": label,
        "stat_events": len(selected),
        "tables": pack_hitter_tables(selected, team) if selected else {},
        "heat": {
            name: heat_sides(rows, "pitcher_hand")
            for name, rows in windows.items()
            if name != "LBPRC winter"
        },
    }


def pitcher_needs(row: dict | None, heat_rec: dict | None) -> bool:
    if not row or not row.get("synergy_id"):
        return False
    arsenal = row.get("arsenal") or []
    missing_whiff = any(pitch.get("whiff") is None for pitch in arsenal)
    missing_heat = not (
        side_ready((heat_rec or {}).get("vs_r"), hitter=False)
        and side_ready((heat_rec or {}).get("vs_l"), hitter=False)
    )
    return bool(missing_whiff or not count_complete(row) or missing_heat)


def hitter_needs(row: dict | None, heat_rec: dict | None) -> bool:
    if not row or not row.get("synergy_id"):
        return False
    missing_table = any(not row.get(key) for key in ADVANCE_TABLES)
    missing_heat = not (
        side_ready((heat_rec or {}).get("vs_r"), hitter=True)
        and side_ready((heat_rec or {}).get("vs_l"), hitter=True)
    )
    return bool(missing_table or missing_heat)


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    checkpoint = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.is_file() else {"pitchers": {}, "hitters": {}}
    arsenal_doc = load("pregame_pitcher_arsenals.json")
    hitter_doc = load("advance_opposing_hitters.json")
    heat_doc = load("advance_heatmaps.json")
    heat_p = heat_doc.setdefault("pitchers", {})
    heat_h = heat_doc.setdefault("hitters", {})
    arsenal_by = {player_key(row): row for row in (arsenal_doc.get("pitchers") or [])}
    hitter_by = {player_key(row): row for row in (hitter_doc.get("hitters") or [])}
    pitchers, hitters = roster_players()
    report = {"filled_pitchers": [], "empty_pitchers": [], "filled_hitters": [], "empty_hitters": []}

    def pitcher_targets() -> list[dict]:
        rows = []
        for player in pitchers:
            row = arsenal_by.get(player_key(player))
            rec = heat_p.get(heat_key(player, row or {}, heat_p)) if row else None
            if pitcher_needs(row, rec):
                missing_count = not count_complete(row)
                missing_all_whiff = not any(
                    pitch.get("whiff") is not None for pitch in ((row or {}).get("arsenal") or [])
                )
                rows.append((0 if missing_count else 1 if missing_all_whiff else 2, player))
        rows.sort(key=lambda item: (item[0], item[1]["team"], item[1]["name"]))
        return [player for _, player in rows]

    def hitter_targets() -> list[dict]:
        return [
            player
            for player in hitters
            if hitter_needs(
                hitter_by.get(player_key(player)),
                heat_h.get(str((hitter_by.get(player_key(player)) or {}).get("synergy_id") or "")),
            )
        ]

    targets_p = pitcher_targets()
    targets_h = hitter_targets()
    print(f"pitcher targets={len(targets_p)} hitter targets={len(targets_h)}", flush=True)
    if not targets_p and not targets_h:
        print("nothing to fill")
        return
    token = bas.login_token(headed=False)

    for index, player in enumerate(targets_p, 1):
        row = arsenal_by[player_key(player)]
        sid = str(row.get("synergy_id") or "")
        print(f"=== pitcher [{index}/{len(targets_p)}] {player['team']} {player['name']}", flush=True)
        cached = (checkpoint.get("pitchers") or {}).get(sid)
        if not cached or not cached.get("complete"):
            token, events, scan = fetch_player(token, pitcher_id=sid, synergy_id=sid)
            cached = pack_pitcher(events, scan, sid)
            checkpoint.setdefault("pitchers", {})[sid] = cached
            checkpoint["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            CHECKPOINT.write_text(json.dumps(checkpoint) + "\n")
        filled = apply_pitcher(row, player, cached, heat_p)
        entry = {
            "name": player["name"],
            "team": player["team"],
            "synergy_id": sid,
            "scanned": cached.get("scanned"),
            "window": cached.get("stat_window"),
            "events": cached.get("stat_events"),
            "filled": filled,
        }
        bucket = "filled_pitchers" if any(filled.values()) else "empty_pitchers"
        report[bucket].append(entry)
        print(f"  {bucket} {json.dumps(filled)}", flush=True)
        save_json(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
        if filled["heat"]:
            heat_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            save_json(DATA / "advance_heatmaps.json", heat_doc, compact=True)
        REPORT.write_text(json.dumps(report, indent=2) + "\n")

    for index, player in enumerate(targets_h, 1):
        row = hitter_by[player_key(player)]
        sid = str(row.get("synergy_id") or "")
        print(f"=== hitter [{index}/{len(targets_h)}] {player['team']} {player['name']}", flush=True)
        cached = (checkpoint.get("hitters") or {}).get(sid)
        if not cached or not cached.get("complete"):
            token, events, scan = fetch_player(token, batter_id=sid, synergy_id=sid)
            cached = pack_hitter_pull(events, scan, player["team"], synergy_id=sid)
            checkpoint.setdefault("hitters", {})[sid] = cached
            checkpoint["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            CHECKPOINT.write_text(json.dumps(checkpoint) + "\n")
        filled = apply_hitter(row, cached, heat_h)
        entry = {
            "name": player["name"],
            "team": player["team"],
            "synergy_id": sid,
            "scanned": cached.get("scanned"),
            "window": cached.get("stat_window"),
            "events": cached.get("stat_events"),
            "filled": filled,
        }
        bucket = "filled_hitters" if filled["tables"] or filled["overall"] or filled["heat"] else "empty_hitters"
        report[bucket].append(entry)
        print(f"  {bucket} {json.dumps(filled)}", flush=True)
        if filled["tables"] or filled["overall"]:
            hitter_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            save_json(DATA / "advance_opposing_hitters.json", hitter_doc)
        if filled["heat"]:
            heat_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            save_json(DATA / "advance_heatmaps.json", heat_doc, compact=True)
        REPORT.write_text(json.dumps(report, indent=2) + "\n")

    print(
        f"done pitchers {len(report['filled_pitchers'])}/{len(targets_p)} "
        f"hitters {len(report['filled_hitters'])}/{len(targets_h)}",
        flush=True,
    )


if __name__ == "__main__":
    main()

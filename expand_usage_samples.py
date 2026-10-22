#!/usr/bin/env python3
"""Widen pitcher usage samples that are still short.

Pulls Synergy pitcherId events from 2022–2026 in LBPRC, MLB, MiLB, and LMB
until 800 in-window pitches, then keeps the mix only when it is larger than
the sample already on the arsenal. Movement fields are left alone.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import build_advance_synergy as bas
from fill_missing_count_synergy import resolve_sid

ROOT = Path(__file__).resolve().parent
ARSENAL = ROOT / "data" / "pregame_pitcher_arsenals.json"
CHECKPOINT = ROOT / "runs" / "usage_expand_checkpoint.json"
YEARS = set(range(2022, 2027))
MAX_EVENTS = 800
MIN_HAND = 80
MIN_TOTAL = 200


def hand_n(p: dict) -> tuple[int, int, int]:
    hn = p.get("synergy_hand_n") or {}
    total = int(p.get("synergy_count_pitches") or 0)
    return total, int(hn.get("vs_LHH") or 0), int(hn.get("vs_RHH") or 0)


def needs_expand(p: dict) -> bool:
    total, left, right = hand_n(p)
    if total < MIN_TOTAL or left < MIN_HAND or right < MIN_HAND:
        return True
    return False


def better(mix: dict, old_n: int, old_l: int, old_r: int) -> bool:
    hn = mix.get("hand_n") or {}
    n = int(mix.get("pitches") or 0)
    left = int(hn.get("vs_LHH") or 0)
    right = int(hn.get("vs_RHH") or 0)
    if n <= 0:
        return False
    if old_n <= 0:
        return True
    if n > old_n and min(left, right) >= min(old_l, old_r):
        return True
    if min(left, right) > min(old_l, old_r) and n >= int(old_n * 0.85):
        return True
    return False


def retain(ev: dict) -> bool:
    year = bas.event_season_year(ev) or 0
    return year in YEARS and bas.event_league_id(ev) in set(bas.SAMPLE_LEAGUES.values())


def main() -> None:
    doc = json.loads(ARSENAL.read_text())
    done = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.is_file() else {}
    token = bas.login_token(headed=False)
    print("Synergy login ok", flush=True)
    targets = [p for p in doc["pitchers"] if needs_expand(p)]
    print(f"thin pitchers {len(targets)} cached {len(done)}", flush=True)
    improved: dict[str, dict] = {}
    for i, p in enumerate(sorted(targets, key=lambda r: (r.get("team") or "", r.get("name") or "")), 1):
        name = p.get("name") or "?"
        key = f"{p.get('team')}|{name}|{p.get('player_id')}"
        if key in done:
            row = done[key]
            if row.get("adopt") and row.get("mix"):
                improved[row["mix"]["synergy_id"]] = row["mix"]
            print(f"[{i}/{len(targets)}] {name} cached", flush=True)
            continue
        try:
            mid = int(p.get("player_id") or 0) or None
        except (TypeError, ValueError):
            mid = None
        if mid is not None and mid <= 0:
            mid = None
        sid = str(p.get("synergy_id") or "") or (resolve_sid(token, name, mid) or "")
        old_n, old_l, old_r = hand_n(p)
        if not sid:
            done[key] = {"name": name, "adopt": False, "reason": "no synergy id"}
            CHECKPOINT.write_text(json.dumps(done))
            print(f"[{i}/{len(targets)}] {p.get('team')} {name} no id", flush=True)
            continue
        print(f"[{i}/{len(targets)}] {p.get('team')} {name} was {old_n} L{old_l} R{old_r}", flush=True)
        try:
            events = bas.fetch_events_pages(
                token,
                pitcher_id=sid,
                max_events=MAX_EVENTS,
                take=200,
                pause_s=0.05,
                timeout_s=90,
                max_attempts=2,
                stop_before_year=2022,
                retain=retain,
                max_scan=5000,
            )
        except Exception as exc:  # noqa: BLE001
            done[key] = {"name": name, "adopt": False, "reason": str(exc)}
            CHECKPOINT.write_text(json.dumps(done))
            print(f"  FAIL {exc}", flush=True)
            continue
        packed = bas.aggregate_pitcher_count_mixes(events)
        mix = packed.get(sid) or (next(iter(packed.values())) if packed else None)
        if not mix:
            done[key] = {"name": name, "synergy_id": sid, "adopt": False, "events": len(events)}
            CHECKPOINT.write_text(json.dumps(done))
            print(f"  empty events={len(events)}", flush=True)
            continue
        mix = dict(mix)
        mix["synergy_id"] = sid
        mix["name"] = name
        mix["team"] = p.get("team")
        if mid:
            mix["mlb_id"] = mid
        elif p.get("player_id"):
            mix["mlb_id"] = p.get("player_id")
        adopt = better(mix, old_n, old_l, old_r)
        hn = mix.get("hand_n") or {}
        print(
            f"  new {mix.get('pitches')} L{hn.get('vs_LHH')} R{hn.get('vs_RHH')} "
            f"years={mix.get('years')} adopt={adopt}",
            flush=True,
        )
        done[key] = {
            "name": name,
            "synergy_id": sid,
            "adopt": adopt,
            "pitches": mix.get("pitches"),
            "hand_n": hn,
            "years": mix.get("years"),
            "mix": mix if adopt else None,
        }
        CHECKPOINT.write_text(json.dumps(done))
        if adopt:
            improved[sid] = mix
        time.sleep(0.05)

    if improved:
        stats = bas.merge_synergy_count_into_arsenals(improved)
        print(f"merged {len(improved)} {stats}", flush=True)
    else:
        print("nothing larger to merge", flush=True)


if __name__ == "__main__":
    main()

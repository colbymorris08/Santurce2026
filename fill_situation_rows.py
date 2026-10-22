#!/usr/bin/env python3
"""Add hand-specific 0-0, hitter-ahead, 2K, 3-2, and RISP rows for every pitcher.

Hitter ahead is only the 2-0, 3-1, and 3-0 counts. Samples are Synergy
pitcherId events from 2022–2026 in LBPRC, MLB, MiLB, and LMB, capped at 800.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import build_advance_synergy as bas

ROOT = Path(__file__).resolve().parent
ARSENAL = ROOT / "data" / "pregame_pitcher_arsenals.json"
CHECKPOINT = ROOT / "runs" / "situation_rows_checkpoint.json"
YEARS = set(range(2022, 2027))
MAX_EVENTS = 800


def retain(ev: dict) -> bool:
    year = bas.event_season_year(ev) or 0
    return year in YEARS and bas.event_league_id(ev) in set(bas.SAMPLE_LEAGUES.values())


def main() -> None:
    doc = json.loads(ARSENAL.read_text())
    done = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.is_file() else {}
    token = bas.login_token(headed=False)
    print("Synergy login ok", flush=True)
    targets = [p for p in doc["pitchers"] if p.get("synergy_id")]
    print(f"pitchers {len(targets)} cached {len(done)}", flush=True)
    for i, p in enumerate(sorted(targets, key=lambda r: (r.get("team") or "", r.get("name") or "")), 1):
        sid = str(p["synergy_id"])
        name = p.get("name") or sid
        if sid in done and done[sid].get("mix"):
            print(f"[{i}/{len(targets)}] {name} cached", flush=True)
            continue
        print(f"[{i}/{len(targets)}] {p.get('team')} {name}", flush=True)
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
            done[sid] = {"name": name, "error": str(exc)}
            CHECKPOINT.write_text(json.dumps(done))
            print(f"  FAIL {exc}", flush=True)
            continue
        mix = bas.hand_situation_mix(events)
        summary = {
            key: {
                "L": sum(row["pitches"] for row in mix[key]["vs_LHB"]),
                "R": sum(row["pitches"] for row in mix[key]["vs_RHB"]),
            }
            for key in bas.SITUATION_KEYS
        }
        done[sid] = {"name": name, "events": len(events), "summary": summary, "mix": mix}
        CHECKPOINT.write_text(json.dumps(done))
        print(f"  events={len(events)} {summary}", flush=True)
        time.sleep(0.05)

    attached = 0
    by_sid = {str(p.get("synergy_id")): p for p in doc["pitchers"] if p.get("synergy_id")}
    for sid, row in done.items():
        mix = row.get("mix")
        pitcher = by_sid.get(sid)
        if not mix or not pitcher:
            continue
        pitcher["count_hand_mix"] = mix
        attached += 1
    ARSENAL.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"attached {attached}", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Patch BRef wOBA + hit-type counts onto advance_opposing_hitters.json.

Re-pulls Synergy events per hitter (same winter+summer window as the advance
builder), re-aggregates with singles/doubles/triples/hr, and merges those fields
(+ woba) into overall and every by_pitch_type table. Checkpoints after each
hitter so a mid-run 401 can resume.
"""

from __future__ import annotations

import json
import time
import urllib.error
from pathlib import Path

import build_advance_synergy as bas

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = DATA / "advance_opposing_hitters.json"
CKPT = ROOT / "runs" / "woba_backfill_checkpoint.json"
HIT_FIELDS = ("singles", "doubles", "triples", "hr", "woba", "tb", "h", "ab", "pa", "bb", "ibb", "hbp", "sf")
PITCH_LIST_KEYS = [
    "by_pitch_type",
    "risp_by_pitch_type",
    "vs_r_by_pitch_type",
    "vs_l_by_pitch_type",
    "risp_vs_r_by_pitch_type",
    "risp_vs_l_by_pitch_type",
    "non_risp_vs_r_by_pitch_type",
    "non_risp_vs_l_by_pitch_type",
    "pre2k_vs_r_by_pitch_type",
    "pre2k_vs_l_by_pitch_type",
    "twok_vs_r_by_pitch_type",
    "twok_vs_l_by_pitch_type",
]


def load_ckpt() -> dict:
    if CKPT.exists():
        return json.loads(CKPT.read_text())
    return {"done": [], "updated": None}


def save_ckpt(ckpt: dict) -> None:
    CKPT.parent.mkdir(parents=True, exist_ok=True)
    ckpt["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    CKPT.write_text(json.dumps(ckpt, indent=2) + "\n")


def merge_bucket(dst: dict, src: dict) -> None:
    if not isinstance(dst, dict) or not isinstance(src, dict):
        return
    for k in HIT_FIELDS:
        if k in src and src[k] is not None:
            dst[k] = src[k]


def merge_pitch_lists(dst_rows: list | None, src_rows: list | None) -> None:
    if not isinstance(dst_rows, list) or not isinstance(src_rows, list):
        return
    by = {str(r.get("pitch_type") or ""): r for r in src_rows if isinstance(r, dict)}
    for row in dst_rows:
        if not isinstance(row, dict):
            continue
        src = by.get(str(row.get("pitch_type") or ""))
        if src:
            merge_bucket(row, src)


def recount_one(token: str, hitter: dict, *, max_events: int, year_set: set[int]) -> tuple[str, dict | None]:
    bid = str(hitter.get("synergy_id") or "")
    team = str(hitter.get("team") or "?")
    name = hitter.get("name") or bid
    if not bid:
        return token, None
    try:
        raw_events = bas.fetch_events_pages(token, batter_id=bid, max_events=max_events)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        print(f"  HTTP 401 {name} — re-login")
        tok_path = Path("/tmp/synergy_token.txt")
        if tok_path.exists():
            tok_path.unlink()
        token = bas.login_token()
        raw_events = bas.fetch_events_pages(token, batter_id=bid, max_events=max_events)
    kept = bas.filter_events_by_leagues_years(
        raw_events,
        league_ids=set(bas.SAMPLE_LEAGUES.values()),
        years=year_set,
    )
    raw = bas.aggregate_events(kept, team)
    if bid in raw:
        raw[bid]["name"] = name
        raw[bid]["team"] = team
    rows = bas.serialize_hitters(raw, {}, {})
    fresh = next((r for r in rows if str(r.get("synergy_id")) == bid), None)
    print(f"  kept={len(kept)} raw={len(raw_events)} woba={(fresh or {}).get('overall', {}).get('woba')}")
    return token, fresh


def main() -> int:
    data = json.loads(OUT.read_text())
    hitters = data.get("hitters") or []
    ckpt = load_ckpt()
    done = set(ckpt.get("done") or [])
    year_set = {2025, 2026}
    max_events = 1200  # enough for chart wOBA; full 2500 is overnight-sized

    print(f"backfill wOBA hitters={len(hitters)} already_done={len(done)} max_events={max_events}", flush=True)
    token = bas.login_token()
    print("Synergy login ok", flush=True)

    for i, h in enumerate(hitters, 1):
        sid = str(h.get("synergy_id") or "")
        name = h.get("name") or sid
        if not sid:
            continue
        if sid in done and (h.get("overall") or {}).get("woba") is not None:
            continue
        print(f"=== [{i}/{len(hitters)}] {h.get('team')} {name}", flush=True)
        try:
            token, fresh = recount_one(token, h, max_events=max_events, year_set=year_set)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {name}: {exc}", flush=True)
            save_ckpt(ckpt)
            continue
        if not fresh:
            print("  no aggregate", flush=True)
            done.add(sid)
            ckpt["done"] = sorted(done)
            save_ckpt(ckpt)
            continue
        merge_bucket(h.setdefault("overall", {}), fresh.get("overall") or {})
        for key in PITCH_LIST_KEYS:
            merge_pitch_lists(h.get(key), fresh.get(key))
        # Keep summer_line woba (already attached via BRef/Stats API)
        done.add(sid)
        ckpt["done"] = sorted(done)
        save_ckpt(ckpt)
        # Persist after each hitter so a kill still ships progress
        data["woba_backfill_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data["updated"] = data["woba_backfill_updated"]
        OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n")
        print(f"  saved overall_woba={(h.get('overall') or {}).get('woba')} done={len(done)}", flush=True)
        time.sleep(0.05)

    # Coverage report
    ow = sum(1 for h in hitters if (h.get("overall") or {}).get("woba") is not None)
    pw = 0
    pt = 0
    for h in hitters:
        for r in h.get("by_pitch_type") or []:
            pt += 1
            if r.get("woba") is not None:
                pw += 1
    print(f"DONE overall_woba={ow}/{len(hitters)} pitch_woba={pw}/{pt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

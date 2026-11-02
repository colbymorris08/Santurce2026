#!/usr/bin/env python3
"""Fill empty vs-LHH / vs-RHH pitch frequency (platoon) via Synergy pitcherId.

Targets pitchers who already have a Synergy id (or resolvable name) but no
platoon splits in arsenals / advance. Merges into both caches and rebuilds
pitcher_one_page.json so the one-page sheet gets RHH/LHH frequency.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import fill_missing_count_synergy as fmc

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"

# Prefer these when --new-only (recent CAG/SAN adds)
NEW_IDS = {681154, 805731, 687825, 806064, 669111}  # Killgore, Veen, Santana, Echavarria, McMillon


def _mid(row: dict) -> int | None:
    for key in ("player_id", "mlb_id", "id"):
        try:
            val = int(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return val
    return None


def has_platoon(row: dict | None) -> bool:
    platoon = (row or {}).get("platoon") or {}
    return bool(platoon.get("vs_RHB") or platoon.get("vs_LHB"))


def targets(*, new_only: bool, names: set[str]) -> list[dict]:
    arsenals = json.loads((DATA / "pregame_pitcher_arsenals.json").read_text()).get("pitchers") or []
    advance = {
        _mid(r): r
        for r in (json.loads((DATA / "advance_opposing_pitchers.json").read_text()).get("pitchers") or [])
        if _mid(r)
    }
    out = []
    for row in arsenals:
        mid = _mid(row)
        name = row.get("name") or ""
        if names and name.lower() not in names and ligapr.norm_name(name) not in names:
            continue
        if new_only and mid not in NEW_IDS:
            continue
        adv = advance.get(mid) if mid else {}
        if has_platoon(row) or has_platoon(adv):
            continue
        sid = row.get("synergy_id") or (adv or {}).get("synergy_id")
        out.append(
            {
                "name": name,
                "team": row.get("team"),
                "player_id": mid,
                "synergy_id": sid,
                "arsenal": row,
                "advance": adv,
            }
        )
    out.sort(key=lambda r: (r.get("team") or "", r.get("name") or ""))
    return out


def sync_platoon_into_advance(pitcher_mixes: dict[str, dict]) -> int:
    """Copy Synergy platoon (+ count) onto advance_opposing_pitchers rows."""
    path = DATA / "advance_opposing_pitchers.json"
    doc = json.loads(path.read_text())
    rows = doc.get("pitchers") or []
    by_sid = {str(r.get("synergy_id") or ""): r for r in rows if r.get("synergy_id")}
    by_mid = {_mid(r): r for r in rows if _mid(r)}
    by_name = {ligapr.norm_name(r.get("name") or ""): r for r in rows}
    updated = 0
    for sid, mix in pitcher_mixes.items():
        row = by_sid.get(str(sid))
        mid = _mid(mix)
        if not row and mid:
            row = by_mid.get(mid)
        if not row:
            row = by_name.get(ligapr.norm_name(mix.get("name") or ""))
        if not row:
            row = {
                "name": mix.get("name"),
                "mlb_id": mid,
                "team": mix.get("team"),
                "throws": None,
                "synergy_id": sid,
                "pitches": 0,
                "summer_line": None,
                "platoon": {},
                "count_mix": {},
            }
            rows.append(row)
            by_sid[str(sid)] = row
            if mid:
                by_mid[mid] = row
        platoon = mix.get("platoon") or {}
        if platoon.get("vs_LHB") or platoon.get("vs_RHB"):
            row["platoon"] = platoon
            updated += 1
        if mix.get("count_mix"):
            row["count_mix"] = mix["count_mix"]
        if mix.get("pitches"):
            row["pitches"] = mix["pitches"]
        row["synergy_id"] = sid
        if mid:
            row["mlb_id"] = mid
        row["hand_n"] = mix.get("hand_n")
        row["leagues"] = mix.get("leagues") or row.get("leagues")
        row["years"] = mix.get("years") or row.get("years")
        row["source"] = mix.get("source") or row.get("source")
    doc["pitchers"] = rows
    doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return updated


def copy_arsenal_platoon_to_advance() -> int:
    """Backfill advance platoon from arsenals when advance is empty."""
    ars = json.loads((DATA / "pregame_pitcher_arsenals.json").read_text()).get("pitchers") or []
    path = DATA / "advance_opposing_pitchers.json"
    doc = json.loads(path.read_text())
    rows = doc.get("pitchers") or []
    by_mid = {_mid(r): r for r in rows if _mid(r)}
    by_name = {ligapr.norm_name(r.get("name") or ""): r for r in rows}
    n = 0
    for a in ars:
        if not has_platoon(a):
            continue
        mid = _mid(a)
        row = (by_mid.get(mid) if mid else None) or by_name.get(ligapr.norm_name(a.get("name") or ""))
        if not row or has_platoon(row):
            continue
        row["platoon"] = a.get("platoon")
        if a.get("synergy_id"):
            row["synergy_id"] = a["synergy_id"]
        if a.get("count_mix") and not row.get("count_mix"):
            row["count_mix"] = a["count_mix"]
        n += 1
    if n:
        doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path.write_text(json.dumps(doc, indent=2) + "\n")
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--new-only", action="store_true", help="Only recent CAG/SAN adds")
    ap.add_argument("--name", action="append", default=[], help="Filter by player name (repeatable)")
    ap.add_argument("--max-events", type=int, default=1500)
    ap.add_argument("--years", nargs="+", type=int, default=[2024, 2025, 2026])
    ap.add_argument("--sync-only", action="store_true", help="Only copy arsenal→advance platoon + rebuild one-page")
    args = ap.parse_args()

    names = {n.lower() for n in args.name} | {ligapr.norm_name(n) for n in args.name}
    copied = copy_arsenal_platoon_to_advance()
    print(f"copied arsenal platoon → advance: {copied}")

    if args.sync_only:
        one = ligapr.build_pitcher_one_page()
        ligapr.write_json("pitcher_one_page.json", one)
        print("one-page rebuilt (sync-only)")
        return

    want = targets(new_only=args.new_only, names=names)
    print(f"missing platoon targets={len(want)}")
    if not want:
        one = ligapr.build_pitcher_one_page()
        ligapr.write_json("pitcher_one_page.json", one)
        print("one-page rebuilt (nothing to pull)")
        return

    year_set = set(args.years)
    token = bas.login_token(headed=False)
    print("Synergy login ok")
    target_league_ids = set(bas.SAMPLE_LEAGUES.values())
    pitcher_mixes: dict[str, dict] = {}
    filled, empty, unresolved = [], [], []
    events_by_league: dict[str, int] = defaultdict(int)

    for i, p in enumerate(want, 1):
        name, team, mid = p["name"], p["team"], p["player_id"]
        sid = p.get("synergy_id") or fmc.resolve_sid(token, name, mid)
        if not sid:
            unresolved.append({"name": name, "team": team, "mlb_id": mid})
            print(f"[{i}/{len(want)}] {team} {name} UNRESOLVED")
            continue
        print(f"[{i}/{len(want)}] {team} {name} pitcherId={sid}")
        raw = bas.fetch_events_pages(token, pitcher_id=sid, max_events=args.max_events)
        kept = bas.filter_events_by_leagues_years(raw, league_ids=target_league_ids, years=year_set)
        if not kept and raw:
            kept = bas.filter_events_by_leagues_years(
                raw, league_ids=target_league_ids, years=set(range(2018, 2027))
            )
            if kept:
                print(f"  fallback 2018-2026 kept={len(kept)}")
        # Indie / non-sample leagues (e.g. American Association) — still usable for freq.
        if not kept and raw:
            kept = list(raw)
            print(f"  fallback unfiltered leagues kept={len(kept)}")
        for ev in kept:
            lid = bas.event_league_id(ev)
            for abbr, lid0 in bas.SAMPLE_LEAGUES.items():
                if lid == lid0:
                    events_by_league[abbr] += 1
                    break
        packed = bas.aggregate_pitcher_count_mixes(kept)
        mix = packed.get(sid) or (next(iter(packed.values())) if packed else None)
        platoon = (mix or {}).get("platoon") or {}
        has_hand = bool(platoon.get("vs_RHB") or platoon.get("vs_LHB"))
        if not mix or not mix.get("pitches"):
            print(f"  EMPTY raw={len(raw)} kept={len(kept)}")
            empty.append(
                {
                    "name": name,
                    "team": team,
                    "mlb_id": mid,
                    "synergy_id": sid,
                    "raw": len(raw),
                    "kept": len(kept),
                }
            )
            continue
        mix = dict(mix)
        mix["synergy_id"] = sid
        mix["name"] = name
        mix["team"] = team
        if mid:
            mix["mlb_id"] = mid
        pitcher_mixes[sid] = mix
        hn = mix.get("hand_n") or {}
        if not has_hand:
            print(
                f"  NO HAND SPLIT pitches={mix.get('pitches')} "
                f"hand_n={hn} kept={len(kept)}"
            )
            empty.append(
                {
                    "name": name,
                    "team": team,
                    "mlb_id": mid,
                    "synergy_id": sid,
                    "raw": len(raw),
                    "kept": len(kept),
                    "hand_n": hn,
                }
            )
        else:
            print(
                f"  pitches={mix.get('pitches')} LHH={hn.get('vs_LHH')} RHH={hn.get('vs_RHH')} "
                f"leagues={mix.get('leagues')}"
            )
            filled.append(
                {
                    "name": name,
                    "team": team,
                    "mlb_id": mid,
                    "synergy_id": sid,
                    "pitches": mix.get("pitches"),
                }
            )
        time.sleep(0.08)

    if pitcher_mixes:
        merge_stats = bas.merge_synergy_count_into_arsenals(pitcher_mixes)
        adv_n = sync_platoon_into_advance(pitcher_mixes)
        print("merge arsenals", merge_stats, "advance platoon rows", adv_n)
    else:
        merge_stats = {}

    RUNS.mkdir(exist_ok=True)
    (RUNS / "missing_platoon_fill_report.json").write_text(
        json.dumps(
            {
                "filled": filled,
                "empty": empty,
                "unresolved": unresolved,
                "merge_stats": merge_stats,
                "events_by_league": dict(events_by_league),
            },
            indent=2,
        )
        + "\n"
    )
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)
    print("REPORT filled", len(filled), "empty", len(empty), "unresolved", len(unresolved))
    print("one-page rebuilt")


if __name__ == "__main__":
    main()

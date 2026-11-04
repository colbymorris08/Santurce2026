#!/usr/bin/env python3
"""Fill every tab for pitchers/hitters that still lack Savant movement or Synergy.

Default path after roster adds:
  1. Prospect Savant / Statcast / Synergy IVB+HB movement plots
  2. Synergy usages, count, platoon, heat (when empty/thin)
  3. Rebuild pitcher one-page (velo bands + freqs)

Does not invent numbers — skips when sources have no tracked sample.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import synergy_dns_fix  # noqa: F401
import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import ensure_roster_player as erp
import fill_missing_platoon_synergy as fmp
import fill_movement_plot_batches as move
import fill_remaining_holes as holes
import refresh_roster_synergy as rrs

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
REPORT = RUNS / "all_tabs_complete_report.json"

# Newly added Nov-4 + Ponce official arms (always attempt even if partially filled).
PRIORITY_PITCHERS = {
    ("PON", "ryan murphy"),
    ("PON", "john aponte"),
    ("PON", "hemmanuel rosario"),
    ("PON", "jose rivera"),
    ("CAG", "blayne enlow"),
    ("CAG", "juan javier echavarria"),
    ("CAG", "tanner burns"),
    ("CAG", "aaron mishoulam"),
    ("CAR", "miguel ausua"),
    ("CAR", "jose acosta"),
    ("CAR", "gabriel cotto"),
    ("CAR", "luis rodriguez"),
    ("CAR", "albert oliva"),
    ("CAR", "jake gozzo"),
    ("CAR", "keishin nagamizu"),
    ("CAR", "kaito uchino"),
    ("CAR", "yusei tobita"),
}


def dump(path: Path, doc) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def norm(name: str) -> str:
    return ligapr.norm_name(name or "")


def roster_pitchers(*, teams: set[str] | None) -> list[dict]:
    # holes.roster_players already splits by roster position == "P"
    players, _ = holes.roster_players(include_santurce=True)
    out = []
    for p in players:
        if teams and str(p.get("team") or "").upper() not in teams:
            continue
        # ensure fill helpers see pitcher role
        p = {**p, "position": p.get("position") or "P"}
        out.append(p)
    return out


def roster_hitters(*, teams: set[str] | None) -> list[dict]:
    _, hitters = holes.roster_players(include_santurce=True)
    out = []
    for p in hitters:
        if teams and str(p.get("team") or "").upper() not in teams:
            continue
        out.append(p)
    return out


def needs_movement(row: dict | None) -> bool:
    return not holes.has_movement(row)


def needs_synergy_pitcher(row: dict | None, adv: dict | None) -> bool:
    pitches = int((row or {}).get("pitches") or (adv or {}).get("pitches") or 0)
    arsenal = (row or {}).get("arsenal") or []
    return pitches < 10 or not arsenal


def needs_synergy_hitter(row: dict | None) -> bool:
    if not row:
        return True
    ov = row.get("overall") or {}
    return not int(ov.get("pitches") or 0) and not row.get("vs_r_by_pitch_type")


def fill_movement_for(
    player: dict,
    arsenal_by: dict,
    arsenal_doc: dict,
    resolved: dict,
    token_box: dict,
) -> dict:
    key = holes.player_key(player)
    row = move.ensure_row(arsenal_doc, player, arsenal_by)
    if holes.has_movement(row):
        return {"name": player["name"], "team": player["team"], "status": "already", "source": row.get("ps_source") or row.get("movement_source")}
    found = move.lookup_pitcher(player, row, resolved, token_box)
    added = False
    if found["pitches"]:
        added = move.merge_plot_only(
            row,
            found["pitches"],
            source=found["source"],
            season=found["season"],
            throws=found["throws"] or player.get("throws") or "",
        )
    # Clear stub note when we have real movement or usage
    note = str(row.get("note") or "")
    if added and ("stub" in note.lower() or "scaffold" in note.lower() or "run Synergy" in note):
        row["note"] = None
        row.pop("note", None)
    return {
        "name": player["name"],
        "team": player["team"],
        "mlb_id": player.get("id"),
        "status": "filled" if added else "no_plot",
        "source": found.get("source") or None,
        "season": found.get("season"),
        "why": None if added else move.best_why(found.get("attempts") or []),
        "attempts": found.get("attempts") or [],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--team", action="append", default=[], help="Limit to team(s)")
    ap.add_argument("--skip-synergy", action="store_true")
    ap.add_argument("--skip-movement", action="store_true")
    ap.add_argument("--skip-hitters", action="store_true")
    ap.add_argument(
        "--mlb-id-only",
        action="store_true",
        help="Movement pass only for positive MLBAM ids (Prospect Savant/Statcast first; skip Synergy login hangs)",
    )
    ap.add_argument("--priority-only", action="store_true", help="Only Nov-4 / Murphy set")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    RUNS.mkdir(exist_ok=True)

    teams = {t.upper() for t in args.team} or None
    holes_ckpt = json.loads(holes.CHECKPOINT.read_text()) if holes.CHECKPOINT.is_file() else {}
    resolved = holes_ckpt.get("resolved_ids") or {}
    arsenal_doc = holes.load("pregame_pitcher_arsenals.json")
    arsenal_by = holes.index_rows(arsenal_doc.get("pitchers") or [])
    adv_doc = holes.load("advance_opposing_pitchers.json")
    adv_by = {(r.get("team"), norm(r.get("name") or "")): r for r in adv_doc.get("pitchers") or []}
    hit_doc = holes.load("advance_opposing_hitters.json")
    hit_by = {rrs.row_key(r): r for r in hit_doc.get("hitters") or []}

    pitchers = roster_pitchers(teams=teams)
    if args.priority_only:
        pitchers = [
            p
            for p in pitchers
            if (str(p.get("team") or "").upper(), (p.get("name") or "").lower()) in PRIORITY_PITCHERS
        ]

    # Always put Murphy first
    pitchers.sort(
        key=lambda p: (
            0 if (p.get("team") == "PON" and norm(p.get("name")) == "ryanmurphy") else 1,
            0 if (str(p.get("team") or "").upper(), (p.get("name") or "").lower()) in PRIORITY_PITCHERS else 1,
            p.get("team") or "",
            p.get("name") or "",
        )
    )

    move_targets = []
    syn_targets = []
    for p in pitchers:
        key = holes.player_key(p)
        row = arsenal_by.get(key)
        adv = adv_by.get((p.get("team"), norm(p.get("name") or "")))
        mid = int(p.get("id") or 0)
        if needs_movement(row):
            if args.mlb_id_only and mid <= 0:
                continue
            move_targets.append(p)
        if needs_synergy_pitcher(row, adv):
            syn_targets.append(p)

    if args.limit:
        move_targets = move_targets[: args.limit]
        syn_targets = syn_targets[: args.limit]

    report = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "movement": [],
        "synergy_pitchers": [],
        "synergy_hitters": [],
        "summary": {},
    }
    token_box: dict = {"keys": set()}

    if not args.skip_movement:
        print(f"MOVEMENT targets={len(move_targets)}", flush=True)
        for i, player in enumerate(move_targets, 1):
            print(f"[{i}/{len(move_targets)}] MOVE {player['team']} {player['name']}", flush=True)
            try:
                rec = fill_movement_for(player, arsenal_by, arsenal_doc, resolved, token_box)
            except Exception as exc:  # noqa: BLE001
                rec = {
                    "name": player["name"],
                    "team": player["team"],
                    "status": "error",
                    "why": f"{type(exc).__name__}: {exc}",
                }
            report["movement"].append(rec)
            print(f"  {rec['status']} {rec.get('source') or rec.get('why')}", flush=True)
            # Persist after each so a crash keeps progress
            arsenal_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            holes.dump(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
            time.sleep(0.05)

    if not args.skip_synergy:
        print(f"SYNERGY pitcher targets={len(syn_targets)}", flush=True)
        for i, player in enumerate(syn_targets, 1):
            print(f"[{i}/{len(syn_targets)}] SYN {player['team']} {player['name']}", flush=True)
            try:
                result = erp.fill_synergy_now(player)
            except Exception as exc:  # noqa: BLE001
                result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            report["synergy_pitchers"].append(
                {"name": player["name"], "team": player["team"], "id": player.get("id"), **result}
            )
            print(f"  {result.get('status')} pitches={result.get('pitches')}", flush=True)
            time.sleep(0.15)

        if not args.skip_hitters:
            hitters = roster_hitters(teams=teams)
            thin = []
            for p in hitters:
                key = rrs.row_key({"team": p["team"], "name": p["name"]})
                if needs_synergy_hitter(hit_by.get(key)):
                    thin.append(p)
            if args.limit:
                thin = thin[: args.limit]
            print(f"SYNERGY hitter targets={len(thin)}", flush=True)
            for i, player in enumerate(thin, 1):
                print(f"[{i}/{len(thin)}] SYN-H {player['team']} {player['name']}", flush=True)
                try:
                    result = erp.fill_synergy_now(player)
                except Exception as exc:  # noqa: BLE001
                    result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                report["synergy_hitters"].append(
                    {"name": player["name"], "team": player["team"], "id": player.get("id"), **result}
                )
                print(f"  {result.get('status')} pitches={result.get('pitches')}", flush=True)
                time.sleep(0.15)

    # Refresh arsenal index + platoon copy + one-page
    arsenal_doc = holes.load("pregame_pitcher_arsenals.json")
    try:
        fmp.copy_arsenal_platoon_to_advance()
    except Exception as exc:  # noqa: BLE001
        print(f"platoon copy warn: {exc}", flush=True)
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)

    # Coverage summary
    arsenal_by = holes.index_rows(arsenal_doc.get("pitchers") or [])
    all_p = roster_pitchers(teams=teams)
    with_move = sum(1 for p in all_p if holes.has_movement(arsenal_by.get(holes.player_key(p))))
    with_usage = sum(
        1
        for p in all_p
        if int((arsenal_by.get(holes.player_key(p)) or {}).get("pitches") or 0) >= 10
    )
    move_filled = [r for r in report["movement"] if r.get("status") == "filled"]
    move_miss = [r for r in report["movement"] if r.get("status") == "no_plot"]
    syn_ok = [r for r in report["synergy_pitchers"] if r.get("status") == "populated"]
    syn_empty = [r for r in report["synergy_pitchers"] if r.get("status") in ("empty", "unresolved", "no_events", "error")]
    report["summary"] = {
        "pitchers_roster": len(all_p),
        "with_movement": with_move,
        "with_usage_10plus": with_usage,
        "movement_filled_this_run": len(move_filled),
        "movement_no_plot": len(move_miss),
        "synergy_populated": len(syn_ok),
        "synergy_still_empty": len(syn_empty),
        "murphy": next(
            (
                {
                    "movement": holes.has_movement(arsenal_by.get(holes.player_key(p))),
                    "pitches": (arsenal_by.get(holes.player_key(p)) or {}).get("pitches"),
                    "ps_source": (arsenal_by.get(holes.player_key(p)) or {}).get("ps_source"),
                    "arsenal": [
                        {
                            "type": a.get("type"),
                            "n": a.get("pitches"),
                            "velo": a.get("velo"),
                            "ivb": a.get("ivb"),
                            "hb": a.get("hb_pitcher"),
                        }
                        for a in ((arsenal_by.get(holes.player_key(p)) or {}).get("arsenal") or [])
                    ],
                    "summer": (arsenal_by.get(holes.player_key(p)) or {}).get("summer_line"),
                }
                for p in all_p
                if norm(p.get("name")) == "ryanmurphy" and p.get("team") == "PON"
            ),
            None,
        ),
        "no_plot_names": [
            f"{r['team']} {r['name']}: {r.get('why')}" for r in move_miss[:40]
        ],
        "synergy_empty_names": [
            f"{r['team']} {r['name']}: {r.get('status')}" for r in syn_empty[:40]
        ],
    }
    dump(REPORT, report)
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False), flush=True)
    print(f"report → {REPORT}", flush=True)


if __name__ == "__main__":
    main()

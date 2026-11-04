#!/usr/bin/env python3
"""Add missing Leones de Ponce card-sheet players (bench / available) and wire tabs.

From Nov 2026 lineup card (starters already mostly on roster):
  Jo. Ortiz #37 → Jose Ortiz OF 806176
  Rosario #31  → Eddie Rosario OF 592696 (LHB / red)
  Rivera #9    → Emmanuel Rivera 3B 656896
  Ja Ortiz #13 → Jankel Ortiz SS 703638
  Marcano #49  → Juan Marcano RHP 807620
  Aponte #57   → Aponte P (synthetic; no MLBAM match)

Also stamps jersey #s from the card onto existing PON roster rows.
Does not delete strategy-card localStorage / published note overrides.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import ensure_roster_player as erp
import fill_remaining_holes as holes
from update_2026_27_rosters import stable_id

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

ADDITIONS = [
    {
        "id": 806176,
        "name": "Jose Ortiz",
        "position": "OF",
        "jersey": "37",
        "bats": "R",
        "throws": "R",
    },
    {
        "id": 592696,
        "name": "Eddie Rosario",
        "position": "OF",
        "jersey": "31",
        "bats": "L",
        "throws": "R",
    },
    {
        "id": 656896,
        "name": "Emmanuel Rivera",
        "position": "3B",
        "jersey": "9",
        "bats": "R",
        "throws": "R",
    },
    {
        "id": 703638,
        "name": "Jankel Ortiz",
        "position": "SS",
        "jersey": "13",
        "bats": "S",
        "throws": "R",
    },
    {
        "id": 807620,
        "name": "Juan Marcano",
        "position": "P",
        "jersey": "49",
        "bats": "R",
        "throws": "R",
    },
    {
        "id": stable_id("PON", "Aponte"),
        "name": "Aponte",
        "position": "P",
        "jersey": "57",
        "bats": "R",
        "throws": "R",
    },
]

# Jersey stamps from the physical card (starters + available already on roster).
JERSEY_BY_NAME = {
    "John Bay": "44",
    "Ezequiel Pagan": "25",
    "Jacob Reimer": "42",
    "Austin Murr": "55",
    "Colton Becker": "32",
    "Jesmuel Valentin": "2",
    "Chris Arroyo": "10",
    "William Simoneit": "33",
    "Jose Torres": "0",
    "Kai Yokogawa": "43",
    "Juan Montero": "8",
    "Gabriel Rosado": "72",
    "Kevin Santa": "3",
    "Aldemar Burgos": "16",
    "Yeniel Laboy": "5",
    "Keito Matsuura": "46",
    "Kyohei Yamashiro": "48",
    "Harold Cortijo": "35",
    "Eric Garcia": "53",
    "Alex Claudio": "58",
    "Gabriel Rodriguez": "19",
    "Julio Torres": "50",
    "Andrew Marrero": "63",
    "Lenny Torres": "45",
}


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc) -> None:
    (DATA / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def add_to_roster() -> list[dict]:
    rost = load("lbprc_2025_rosters.json")
    pon = next(t for t in rost.values() if t.get("teamAbbrev") == "PON")
    by_id = {int(p["id"]): p for p in pon["players"] if p.get("id") is not None}
    by_name = {erp.ligapr.norm_name(p.get("name") or ""): p for p in pon["players"]}
    added = []
    for row in ADDITIONS:
        key = erp.ligapr.norm_name(row["name"])
        if row["id"] in by_id or key in by_name:
            existing = by_id.get(row["id"]) or by_name[key]
            if row.get("jersey"):
                existing["jersey"] = row["jersey"]
            print(f"roster already has {row['name']} — jersey={existing.get('jersey')}")
            continue
        player = {
            "id": row["id"],
            "name": row["name"],
            "position": row["position"],
            "jersey": row.get("jersey") or "",
            "status": "Active",
        }
        pon["players"].append(player)
        added.append(player)
        print(f"roster + {row['name']} ({row['id']}) #{row.get('jersey')}")

    # Stamp jerseys on existing PON rows from the card (skip if blank / unknown).
    for p in pon["players"]:
        nm = p.get("name") or ""
        if nm in JERSEY_BY_NAME and JERSEY_BY_NAME[nm]:
            p["jersey"] = JERSEY_BY_NAME[nm]
        for row in ADDITIONS:
            if int(p.get("id") or 0) == int(row["id"]) and row.get("jersey"):
                p["jersey"] = row["jersey"]

    dump("lbprc_2025_rosters.json", rost)
    return added


def ensure_all(players: list[dict]) -> None:
    for row in players:
        player = erp.find_roster(row["name"], row["id"], "PON")
        if not player:
            raise SystemExit(f"missing after roster write: {row}")
        if row.get("bats"):
            player["bats"] = row["bats"]
        if row.get("throws"):
            player["throws"] = row["throws"]
        if row.get("jersey"):
            player["jersey"] = row["jersey"]
        actions = erp.ensure_shared(player)
        if erp.is_pitcher(player):
            actions.update(erp.ensure_pitcher(player))
        else:
            actions.update(erp.ensure_hitter(player))
        print(f"ensure {player['name']}: {actions}")


def seed_summer_lines(players: list[dict]) -> None:
    ckpt = json.loads(holes.CHECKPOINT.read_text()) if holes.CHECKPOINT.is_file() else {}
    pit_rows, hit_rows = holes.roster_players()
    want = {int(p["id"]) for p in players}
    targets_p = [p for p in pit_rows if int(p.get("id") or 0) in want]
    targets_h = [h for h in hit_rows if int(h.get("id") or 0) in want]
    holes.resolve_ids([p for p in targets_h if p["id"] > 0], ckpt, pitcher=False)
    holes.resolve_ids([p for p in targets_p if p["id"] > 0], ckpt, pitcher=True)

    hitter_doc = holes.load("advance_opposing_hitters.json")
    pitcher_doc = holes.load("advance_opposing_pitchers.json")
    hitter_by = holes.index_rows(hitter_doc.get("hitters") or [])
    pitcher_by = holes.index_rows(pitcher_doc.get("pitchers") or [])

    h_ids = [h["id"] for h in targets_h if h["id"] > 0]
    summer_2026 = holes.hitting_lines(h_ids, "2026") if h_ids else {}
    summer_2025 = holes.hitting_lines([i for i in h_ids if i not in summer_2026], "2025") if h_ids else {}
    local_by_id, local_by_name = holes.local_hitting_index()
    for player in targets_h:
        row = hitter_by.get(holes.player_key(player))
        if not row:
            continue
        # Prefer PON team label for tomorrow's opponent card.
        row["team"] = "PON"
        row["name"] = player["name"]
        line = summer_2026.get(player["id"]) or summer_2025.get(player["id"])
        if not line:
            line = holes.local_hitting_for(player, ckpt, local_by_id, local_by_name)
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(f"hitter bar {player['name']}: {(line or {}).get('season')} {(line or {}).get('src')}")
        if player.get("id") and player["id"] > 0:
            row["mlb_id"] = player["id"]
        bats = next((a.get("bats") for a in ADDITIONS if a["id"] == player["id"]), None)
        if bats:
            row["bats"] = row.get("bats") or bats
    holes.dump(DATA / "advance_opposing_hitters.json", hitter_doc)

    p_ids = [p["id"] for p in targets_p if p["id"] > 0]
    pitcher_2026 = {}
    pitcher_2025 = {}
    if p_ids:
        try:
            import build_advance_synergy as bas
            import backfill_pitcher_prior_seasons as prior

            pitcher_2026 = bas.fetch_2026_pitcher_summer_lines(p_ids)
            need = [i for i in p_ids if i not in pitcher_2026]
            pitcher_2025 = prior.summer_2025_lines(need) if need else {}
        except Exception as exc:  # noqa: BLE001
            print(f"pitcher summer lines: {exc}")
    for player in targets_p:
        row = pitcher_by.get(holes.player_key(player))
        if not row:
            continue
        row["team"] = "PON"
        row["name"] = player["name"]
        line = pitcher_2026.get(player["id"]) or pitcher_2025.get(player["id"])
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(f"pitcher bar {player['name']}: {line.get('season')} {line.get('src') or line.get('source')}")
        throws = next((a.get("throws") for a in ADDITIONS if a["id"] == player["id"]), None)
        if throws:
            row["throws"] = row.get("throws") or throws
        if player["id"] < 0:
            row["mlb_id"] = player["id"]
        elif player["id"] > 0:
            row["mlb_id"] = player["id"]
    holes.dump(DATA / "advance_opposing_pitchers.json", pitcher_doc)
    holes.save_checkpoint(ckpt)


def main() -> None:
    added = add_to_roster()
    ensure_all(ADDITIONS)
    seed_summer_lines(ADDITIONS)
    print(f"done. newly rostered={len(added)} stubs wired for {len(ADDITIONS)}")
    print("added:", ", ".join(p["name"] for p in added) or "(none new)")
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


if __name__ == "__main__":
    main()

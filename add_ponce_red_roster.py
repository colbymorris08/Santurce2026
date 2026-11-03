#!/usr/bin/env python3
"""Add Leones de Ponce red-roster imports and seed every pregame tab.

Confirmed Nov 2026 card (red names):
  Will Simoneit     C     686766   (already on PON as William Simoneit)
  Colton Becker     INF   813851
  Jose Torres       INF   686958   (already on PON)
  Jacob Reimer      INF   702544   (already on PON)
  Austin Murr       OF    689456
  John Bay          OF    700083
  Keito Matsuura    P     synthetic (NPB LHP, Yomiuri)
  Kyohei Yamashiro  P     synthetic (NPB LHP, Yomiuri)
  Kai Yokogawa      P     synthetic (NPB LHP, Yomiuri)
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

ALREADY = [
    {"id": 686766, "name": "William Simoneit", "position": "C", "jersey": "33", "bats": "R", "throws": "R"},
    {"id": 686958, "name": "Jose Torres", "position": "INF", "jersey": "", "bats": None, "throws": None},
    {"id": 702544, "name": "Jacob Reimer", "position": "INF", "jersey": "", "bats": "R", "throws": "R"},
]

ADDITIONS = [
    {"id": 813851, "name": "Colton Becker", "position": "INF", "jersey": "", "bats": "R", "throws": "R"},
    {"id": 689456, "name": "Austin Murr", "position": "OF", "jersey": "", "bats": "L", "throws": "L"},
    {"id": 700083, "name": "John Bay", "position": "OF", "jersey": "", "bats": "R", "throws": "R"},
    {
        "id": stable_id("PON", "Keito Matsuura"),
        "name": "Keito Matsuura",
        "position": "P",
        "jersey": "",
        "throws": "L",
        "bats": "L",
    },
    {
        "id": stable_id("PON", "Kyohei Yamashiro"),
        "name": "Kyohei Yamashiro",
        "position": "P",
        "jersey": "",
        "throws": "L",
        "bats": "L",
    },
    {
        "id": stable_id("PON", "Kai Yokogawa"),
        "name": "Kai Yokogawa",
        "position": "P",
        "jersey": "",
        "throws": "L",
        "bats": "L",
    },
]


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
        if row["id"] in by_id or erp.ligapr.norm_name(row["name"]) in by_name:
            print(f"roster already has {row['name']}")
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
        print(f"roster + {row['name']} ({row['id']})")
    dump("lbprc_2025_rosters.json", rost)
    return added


def ensure_all(players: list[dict]) -> None:
    for row in players:
        player = erp.find_roster(row["name"], row["id"], "PON")
        if not player:
            raise SystemExit(f"missing after roster write: {row}")
        # Carry bats/throws when known for stubs
        if row.get("bats"):
            player["bats"] = row["bats"]
        if row.get("throws"):
            player["throws"] = row["throws"]
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
    # Positive MLBAM only for StatsAPI
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
        line = summer_2026.get(player["id"]) or summer_2025.get(player["id"])
        if not line:
            line = holes.local_hitting_for(player, ckpt, local_by_id, local_by_name)
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(f"hitter bar {player['name']}: {(line or {}).get('season')} {(line or {}).get('src')}")
        if player.get("id") and not row.get("mlb_id"):
            row["mlb_id"] = player["id"] if player["id"] > 0 else row.get("mlb_id")
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
        line = pitcher_2026.get(player["id"]) or pitcher_2025.get(player["id"])
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(f"pitcher bar {player['name']}: {line.get('season')} {line.get('src') or line.get('source')}")
        row["throws"] = row.get("throws") or next(
            (a.get("throws") for a in ADDITIONS + ALREADY if a["id"] == player["id"]), None
        )
        if player["id"] < 0:
            row["mlb_id"] = player["id"]
    holes.dump(DATA / "advance_opposing_pitchers.json", pitcher_doc)
    holes.save_checkpoint(ckpt)


def main() -> None:
    added = add_to_roster()
    ensure_all(ADDITIONS + ALREADY)
    seed_summer_lines(ADDITIONS + ALREADY)
    print(f"done. newly rostered={len(added)} stubs wired for {len(ADDITIONS)+len(ALREADY)}")
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


if __name__ == "__main__":
    main()

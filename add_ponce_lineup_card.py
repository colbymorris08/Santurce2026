#!/usr/bin/env python3
"""Add missing Leones de Ponce names from the Nov 2026 lineup card.

Starting 9 + P already partly on roster; this fills available-list gaps and
jersey numbers from the physical card. Additive only — does not touch
strategy-card localStorage or published override JSON.
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

# Names missing from PON roster (or present elsewhere) — card available / lineup.
ADDITIONS = [
    {
        "id": 700083,
        "name": "John Bay",
        "position": "OF",
        "jersey": "44",
        "bats": "R",
        "throws": "R",
    },
    {
        "id": 681868,
        "name": "Ezequiel Pagan",
        "position": "CF",
        "jersey": "25",
        "bats": "L",
        "throws": "R",
    },
    {
        "id": 702544,
        "name": "Jacob Reimer",
        "position": "INF",
        "jersey": "42",
        "bats": "R",
        "throws": "R",
    },
    {
        "id": 689456,
        "name": "Austin Murr",
        "position": "OF",
        "jersey": "55",
        "bats": "L",
        "throws": "L",
    },
    {
        "id": 813851,
        "name": "Colton Becker",
        "position": "INF",
        "jersey": "32",
        "bats": "R",
        "throws": "R",
    },
    {
        "id": 621044,
        "name": "Jesmuel Valentin",
        "position": "2B",
        "jersey": "2",
        "bats": "S",
        "throws": "R",
    },
    {
        "id": 701360,
        "name": "Chris Arroyo",
        "position": "1B",
        "jersey": "10",
        "bats": "L",
        "throws": "L",
    },
    {
        "id": 686766,
        "name": "William Simoneit",
        "position": "C",
        "jersey": "33",
        "bats": "R",
        "throws": "R",
    },
    {
        "id": 686958,
        "name": "Jose Torres",
        "position": "INF",
        "jersey": "0",
        "bats": None,
        "throws": None,
    },
    {
        "id": stable_id("PON", "Kai Yokogawa"),
        "name": "Kai Yokogawa",
        "position": "P",
        "jersey": "43",
        "throws": "L",
        "bats": "L",
    },
    # Available list — previously missing
    {
        "id": 806176,
        "name": "Jose Ortiz",
        "position": "OF",
        "jersey": "37",
        "bats": "R",
        "throws": "R",
    },  # Jo. Ortiz
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
    },  # Ja Ortiz
    {
        "id": 807620,
        "name": "Juan Marcano",
        "position": "P",
        "jersey": "49",
        "throws": "R",
        "bats": "R",
    },
    {
        "id": stable_id("PON", "Aponte"),
        "name": "Aponte",
        "position": "P",
        "jersey": "57",
        "throws": "R",
        "bats": "R",
    },
    {
        "id": stable_id("PON", "Keito Matsuura"),
        "name": "Keito Matsuura",
        "position": "P",
        "jersey": "46",
        "throws": "L",
        "bats": "L",
    },
    {
        "id": stable_id("PON", "Kyohei Yamashiro"),
        "name": "Kyohei Yamashiro",
        "position": "P",
        "jersey": "48",
        "throws": "L",
        "bats": "L",
    },
]

# Jersey-only updates for players already on PON (card numbers).
JERSEY_ONLY = {
    695467: "8",  # Montero
    -173185958: "72",  # Rosado
    667576: "3",  # Santa
    664757: "16",  # Burgos
    702596: "5",  # Laboy
    676435: "35",  # Cortijo
    806586: "53",  # Garcia
    592222: "58",  # Claudio
    676082: "19",  # Rodriguez
    687817: "63",  # Marrero
    678013: "45",  # L. Torres
    692937: "50",  # J. Torres (Julio)
}


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc) -> None:
    (DATA / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def upsert_roster() -> tuple[list[dict], list[dict]]:
    rost = load("lbprc_2025_rosters.json")
    pon = next(t for t in rost.values() if t.get("teamAbbrev") == "PON")
    by_id = {int(p["id"]): p for p in pon["players"] if p.get("id") is not None}
    by_name = {erp.ligapr.norm_name(p.get("name") or ""): p for p in pon["players"]}
    added = []
    touched = []
    for row in ADDITIONS:
        existing = by_id.get(row["id"]) or by_name.get(erp.ligapr.norm_name(row["name"]))
        if existing:
            if row.get("jersey"):
                existing["jersey"] = str(row["jersey"])
            if row.get("position") and not existing.get("position"):
                existing["position"] = row["position"]
            print(f"roster update {existing['name']} jersey={existing.get('jersey')}")
            touched.append(row)
            continue
        player = {
            "id": row["id"],
            "name": row["name"],
            "position": row["position"],
            "jersey": str(row.get("jersey") or ""),
            "status": "Active",
        }
        pon["players"].append(player)
        by_id[row["id"]] = player
        added.append(player)
        touched.append(row)
        print(f"roster + {row['name']} ({row['id']})")

    for pid, jersey in JERSEY_ONLY.items():
        p = by_id.get(pid)
        if p is not None:
            p["jersey"] = jersey
            print(f"jersey {p['name']} -> {jersey}")

    dump("lbprc_2025_rosters.json", rost)
    return added, touched


def ensure_all(rows: list[dict]) -> None:
    for row in rows:
        player = erp.find_roster(row["name"], row["id"], "PON")
        if not player:
            raise SystemExit(f"missing after roster write: {row}")
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
        row["team"] = "PON"
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
            import backfill_pitcher_prior_seasons as prior
            import build_advance_synergy as bas

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
        line = pitcher_2026.get(player["id"]) or pitcher_2025.get(player["id"])
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(f"pitcher bar {player['name']}: {line.get('season')} {line.get('src') or line.get('source')}")
        meta = next((a for a in players if a["id"] == player["id"]), None)
        if meta and meta.get("throws"):
            row["throws"] = row.get("throws") or meta["throws"]
        if player["id"] < 0:
            row["mlb_id"] = player["id"]
    holes.dump(DATA / "advance_opposing_pitchers.json", pitcher_doc)
    holes.save_checkpoint(ckpt)


def main() -> None:
    added, touched = upsert_roster()
    ensure_all(touched)
    seed_summer_lines(touched)
    print(f"done. newly rostered={len(added)} wired={len(touched)}")
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


if __name__ == "__main__":
    main()

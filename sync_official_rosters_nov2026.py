#!/usr/bin/env python3
"""Sync official Nov 4 2026 active rosters for CAG/CAR/MAY/SJU.

Additive only: add missing players, patch jersey/name/position aliases,
wire every pregame tab via ensure_roster_player. Does NOT wipe strategy-card,
game_day_state, or existing TTH overrides (TTH stubs only fill null rows).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import build_ligapr_daily as ligapr
import ensure_roster_player as erp
import fill_remaining_holes as holes
from update_2026_27_rosters import stable_id

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

# True new active-roster adds (not already on team under any alias).
ADDITIONS: dict[str, list[dict]] = {
    "CAG": [
        {"id": 669416, "name": "Blayne Enlow", "position": "P", "jersey": "46", "throws": "R", "bats": "R"},
        {
            "id": stable_id("CAG", "Juan Javier Echavarria"),
            "name": "Juan Javier Echavarria",
            "position": "P",
            "jersey": "66",
            "throws": "R",
            "bats": "R",
        },
        {"id": 669142, "name": "Tanner Burns", "position": "P", "jersey": "63", "throws": "R", "bats": "R"},
        {
            "id": stable_id("CAG", "Aaron Mishoulam"),
            "name": "Aaron Mishoulam",
            "position": "P",
            "jersey": "62",
            "throws": "R",
            "bats": "R",
        },
    ],
    "CAR": [
        {"id": 828227, "name": "Diego Rosa", "position": "C", "jersey": "45", "bats": "R", "throws": "R"},
        {"id": 813844, "name": "Will Bush", "position": "C", "jersey": "26", "bats": "L", "throws": "R"},
        {"id": 691724, "name": "Steven Ondina", "position": "INF", "jersey": "1", "bats": "R", "throws": "R"},
        {"id": 805125, "name": "Giullianno Allende", "position": "OF", "jersey": "61", "bats": "L", "throws": "L"},
        {"id": 682118, "name": "Miguel Ausua", "position": "P", "jersey": "18", "throws": "L", "bats": "L"},
        {
            "id": stable_id("CAR", "Jose Acosta"),
            "name": "Jose Acosta",
            "position": "P",
            "jersey": "39",
            "throws": "L",
            "bats": "L",
        },
        {"id": 681813, "name": "Gabriel Cotto", "position": "P", "jersey": "44", "throws": "L", "bats": "L"},
        {"id": 693543, "name": "Luis Rodriguez", "position": "P", "jersey": "28", "throws": "R", "bats": "R"},
        {"id": 806309, "name": "Albert Oliva", "position": "P", "jersey": "5", "throws": "R", "bats": "R"},
        {"id": 689214, "name": "Jake Gozzo", "position": "P", "jersey": "38", "throws": "R", "bats": "R"},
        {
            "id": stable_id("CAR", "Keishin Nagamizu"),
            "name": "Keishin Nagamizu",
            "position": "P",
            "jersey": "62",
            "throws": "L",
            "bats": "L",
        },
        {
            "id": stable_id("CAR", "Kaito Uchino"),
            "name": "Kaito Uchino",
            "position": "P",
            "jersey": "67",
            "throws": "R",
            "bats": "R",
        },
        {
            "id": stable_id("CAR", "Yusei Tobita"),
            "name": "Yusei Tobita",
            "position": "P",
            "jersey": "59",
            "throws": "R",
            "bats": "R",
        },
    ],
    "MAY": [
        {
            "id": stable_id("MAY", "Luis Ramos"),
            "name": "Luis Ramos",
            "position": "P",
            "jersey": "39",
            "throws": "L",
            "bats": "L",
        },
        {
            "id": stable_id("MAY", "Shadai Colon"),
            "name": "Shadai Colon",
            "position": "P",
            "jersey": "69",
            "throws": "L",
            "bats": "L",
        },
        {
            "id": stable_id("MAY", "Sebastian Bentz"),
            "name": "Sebastian Bentz",
            "position": "P",
            "jersey": "46",
            "throws": "L",
            "bats": "L",
        },
        {"id": 826141, "name": "Sean Barnett", "position": "P", "jersey": "72", "throws": "R", "bats": "R"},
        {"id": 684320, "name": "Yariel Rodriguez", "position": "P", "jersey": "45", "throws": "R", "bats": "R"},
    ],
    "SJU": [
        {"id": 687436, "name": "Victor Torres", "position": "C", "jersey": "59", "bats": "R", "throws": "R"},
        {"id": 670582, "name": "Yariel Gonzalez", "position": "3B", "jersey": "5", "bats": "S", "throws": "R"},
        {"id": 806128, "name": "Antonio Jimenez", "position": "SS", "jersey": "61", "bats": "R", "throws": "R"},
        {
            "id": stable_id("SJU", "Santino Rosso"),
            "name": "Santino Rosso",
            "position": "INF",
            "jersey": "4",
            "bats": None,
            "throws": None,
        },
        {"id": 693049, "name": "Oscar Colas", "position": "INF", "jersey": "55", "bats": "L", "throws": "L"},
        {"id": 571681, "name": "Reymond Fuentes", "position": "OF", "jersey": "17", "bats": "L", "throws": "L"},
        {"id": 543803, "name": "Giovanni Soto", "position": "P", "jersey": "67", "throws": "L", "bats": "L"},
        {
            "id": stable_id("SJU", "Matthew Rivera"),
            "name": "Matthew Rivera",
            "position": "P",
            "jersey": "95",
            "throws": "R",
            "bats": "R",
        },
        {"id": 656924, "name": "Ryder Ryan", "position": "P", "jersey": "54", "throws": "R", "bats": "R"},
    ],
}

# Existing roster rows: rename / jersey / position / real-ID upgrades (aliases from docs).
PATCHES: dict[str, list[dict]] = {
    "CAG": [
        {"match_name": "Jarret Whorf", "name": "Jarret Whorff", "jersey": "60", "position": "P"},
        {"match_name": "Jaime Ferrer", "position": "INF", "jersey": "57"},
        {"match_name": "Edison Mora", "position": "OF", "jersey": "39"},
        {"match_name": "Carson Demartini", "name": "Carson DeMartini", "jersey": "41"},
    ],
    "CAR": [],
    "MAY": [
        {
            "match_name": "Luis Cruz",
            "name": "Luis Leroy Cruz",
            "id": 543071,
            "jersey": "41",
            "position": "P",
            "throws": "L",
        },
        {"match_name": "Eddy Demurias", "id": 681493, "jersey": "60", "position": "P", "throws": "R"},
        {"match_name": "Jonathan Haab", "jersey": "64", "position": "P"},
        {"match_name": "Darren Baker", "position": "INF", "jersey": "2"},
        {"match_name": "Ruben Castro", "position": "OF", "jersey": "4"},
        {"match_name": "Carter Trice", "position": "OF", "jersey": "63"},
    ],
    "SJU": [
        {"match_name": "Roberto Enriquez", "jersey": "27"},
        {"match_name": "Emmanuel Garcia", "jersey": "69"},
        {"match_name": "Julio Ortiz", "name": "Julio Cesar Ortiz", "jersey": "36", "position": "P"},
        {"match_name": "Ivan Houellemont", "position": "P", "jersey": "56", "throws": "R"},
        {"match_name": "Ruben Ramirez", "position": "P", "jersey": "52", "throws": "R"},
        {"match_name": "Freddy Cabrera", "jersey": "46"},
        {"match_name": "Yuta Nakami", "jersey": "14"},
        {"match_name": "Keishin Saito", "jersey": "96"},
        {"match_name": "Haruse Narita", "jersey": "92"},
    ],
}


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc) -> None:
    (DATA / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def add_and_patch() -> tuple[list[dict], list[dict]]:
    rost = load("lbprc_2025_rosters.json")
    added: list[dict] = []
    wired: list[dict] = []

    for team, rows in ADDITIONS.items():
        pack = next(t for t in rost.values() if t.get("teamAbbrev") == team)
        by_id = {int(p["id"]): p for p in pack["players"] if p.get("id") is not None}
        by_name = {erp.ligapr.norm_name(p.get("name") or ""): p for p in pack["players"]}
        for row in rows:
            if row["id"] in by_id or erp.ligapr.norm_name(row["name"]) in by_name:
                print(f"{team} already has {row['name']}")
                continue
            player = {
                "id": row["id"],
                "name": row["name"],
                "position": row["position"],
                "jersey": str(row.get("jersey") or ""),
                "status": "Active",
            }
            pack["players"].append(player)
            added.append({**row, "team": team})
            wired.append({**row, "team": team})
            print(f"{team} + {row['name']} ({row['id']}) {row['position']}")

    for team, patches in PATCHES.items():
        pack = next(t for t in rost.values() if t.get("teamAbbrev") == team)
        by_name = {erp.ligapr.norm_name(p.get("name") or ""): p for p in pack["players"]}
        for patch in patches:
            key = erp.ligapr.norm_name(patch["match_name"])
            existing = by_name.get(key)
            if not existing:
                print(f"{team} patch miss {patch['match_name']}")
                continue
            if patch.get("name"):
                existing["name"] = patch["name"]
            if patch.get("jersey") is not None:
                existing["jersey"] = str(patch["jersey"])
            if patch.get("position"):
                existing["position"] = patch["position"]
            if patch.get("id") is not None:
                existing["id"] = patch["id"]
            meta = {
                "id": existing["id"],
                "name": existing["name"],
                "position": existing.get("position"),
                "jersey": existing.get("jersey") or "",
                "team": team,
                "throws": patch.get("throws"),
                "bats": patch.get("bats"),
            }
            wired.append(meta)
            print(f"{team} patch {patch['match_name']} -> {existing['name']} id={existing['id']} pos={existing.get('position')}")

    dump("lbprc_2025_rosters.json", rost)
    return added, wired


def ensure_all(rows: list[dict]) -> None:
    seen = set()
    for row in rows:
        key = (row["team"], int(row["id"]))
        if key in seen:
            continue
        seen.add(key)
        player = erp.find_roster(row["name"], row["id"], row["team"])
        if not player:
            # after rename, find by id
            player = erp.find_roster(None, row["id"], row["team"])
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
        print(f"ensure {row['team']} {player['name']}: {actions}")


def seed_summer_lines(players: list[dict]) -> None:
    ckpt = json.loads(holes.CHECKPOINT.read_text()) if holes.CHECKPOINT.is_file() else {}
    pit_rows, hit_rows = holes.roster_players()
    want = {(p["team"], int(p["id"])) for p in players}
    targets_p = [p for p in pit_rows if (p.get("team"), int(p.get("id") or 0)) in want]
    targets_h = [h for h in hit_rows if (h.get("team"), int(h.get("id") or 0)) in want]
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
            print(f"hitter bar {player['team']} {player['name']}: {(line or {}).get('season')} {(line or {}).get('src')}")
        if player.get("id") and player["id"] > 0:
            row["mlb_id"] = player["id"]
    holes.dump(DATA / "advance_opposing_hitters.json", hitter_doc)

    p_ids = [p["id"] for p in targets_p if p["id"] > 0]
    pitcher_2026: dict = {}
    pitcher_2025: dict = {}
    if p_ids:
        try:
            import backfill_pitcher_prior_seasons as prior
            import build_advance_synergy as bas

            pitcher_2026 = bas.fetch_2026_pitcher_summer_lines(p_ids)
            need = [i for i in p_ids if i not in pitcher_2026]
            pitcher_2025 = prior.summer_2025_lines(need) if need else {}
        except Exception as exc:  # noqa: BLE001
            print(f"pitcher summer lines: {exc}")
    throws_by_id = {int(p["id"]): p.get("throws") for p in players if p.get("throws")}
    for player in targets_p:
        row = pitcher_by.get(holes.player_key(player))
        if not row:
            continue
        line = pitcher_2026.get(player["id"]) or pitcher_2025.get(player["id"])
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(
                f"pitcher bar {player['team']} {player['name']}: "
                f"{line.get('season')} {line.get('src') or line.get('source')}"
            )
        if throws_by_id.get(player["id"]):
            row["throws"] = row.get("throws") or throws_by_id[player["id"]]
        if player["id"] < 0:
            row["mlb_id"] = player["id"]
        elif player["id"] > 0:
            row["mlb_id"] = player["id"]
    holes.dump(DATA / "advance_opposing_pitchers.json", pitcher_doc)
    holes.save_checkpoint(ckpt)


def main() -> None:
    added, wired = add_and_patch()
    # Also wire pure additions list in case patch-only rows empty for a team
    ensure_all(wired)
    seed_summer_lines(wired)

    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)
    print("one-page rebuilt")

    # Seed INF/OF positioning defaults for new hitters (preserves golden_locked cells).
    import fill_positioning_from_spray as pos

    pos.main()

    report = {
        "added": [
            {
                "team": a["team"],
                "name": a["name"],
                "position": a["position"],
                "id": a["id"],
                "jersey": a.get("jersey"),
                "throws": a.get("throws"),
                "bats": a.get("bats"),
            }
            for a in added
        ],
        "wired": len(wired),
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (DATA / "roster_sync_nov2026_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"newly_rostered": len(added), "wired": len(wired)}, indent=2))


if __name__ == "__main__":
    main()

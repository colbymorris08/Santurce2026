#!/usr/bin/env python3
"""Add missing CAG/CAR/MAY/SJU players from Nov 4 2026 official roster docs.

Additive only — does not wipe strategy-card / game_day / TTH overrides.
Wires roster + advance/spray/BR/arsenal/stuff/TTH stubs via ensure_roster_player,
seeds positioning defaults for new hitters, and rebuilds pitcher one-page.
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

# True missing actives from official docs (aliases handled separately).
ADDITIONS: list[dict] = [
    # CAG pitchers
    {"team": "CAG", "id": 669416, "name": "Blayne Enlow", "position": "P", "jersey": "46", "throws": "R", "bats": "R"},
    {
        "team": "CAG",
        "id": stable_id("CAG", "Juan Javier Echavarria"),
        "name": "Juan Javier Echavarria",
        "position": "P",
        "jersey": "66",
        "throws": "R",
        "bats": "R",
    },
    {"team": "CAG", "id": 669142, "name": "Tanner Burns", "position": "P", "jersey": "63", "throws": "R", "bats": "R"},
    {
        "team": "CAG",
        "id": stable_id("CAG", "Aaron Mishoulam"),
        "name": "Aaron Mishoulam",
        "position": "P",
        "jersey": "62",
        "throws": "R",
        "bats": "R",
    },
    # CAR hitters / pitchers
    {"team": "CAR", "id": 828227, "name": "Diego Rosa", "position": "C", "jersey": "45", "bats": "R", "throws": "R"},
    {"team": "CAR", "id": 813844, "name": "Will Bush", "position": "C", "jersey": "26", "bats": "L", "throws": "R"},
    {"team": "CAR", "id": 691724, "name": "Steven Ondina", "position": "INF", "jersey": "1", "bats": "R", "throws": "R"},
    {"team": "CAR", "id": 805125, "name": "Giullianno Allende", "position": "OF", "jersey": "61", "bats": "L", "throws": "L"},
    {"team": "CAR", "id": 682118, "name": "Miguel Ausua", "position": "P", "jersey": "18", "throws": "L", "bats": "L"},
    {
        "team": "CAR",
        "id": stable_id("CAR", "Jose Acosta"),
        "name": "Jose Acosta",
        "position": "P",
        "jersey": "39",
        "throws": "L",
        "bats": "L",
    },
    {"team": "CAR", "id": 681813, "name": "Gabriel Cotto", "position": "P", "jersey": "44", "throws": "L", "bats": "L"},
    {"team": "CAR", "id": 693543, "name": "Luis Rodriguez", "position": "P", "jersey": "28", "throws": "R", "bats": "R"},
    {"team": "CAR", "id": 806309, "name": "Albert Oliva", "position": "P", "jersey": "5", "throws": "R", "bats": "R"},
    {"team": "CAR", "id": 689214, "name": "Jake Gozzo", "position": "P", "jersey": "38", "throws": "R", "bats": "R"},
    {
        "team": "CAR",
        "id": stable_id("CAR", "Keishin Nagamizu"),
        "name": "Keishin Nagamizu",
        "position": "P",
        "jersey": "62",
        "throws": "L",
        "bats": "L",
    },
    {
        "team": "CAR",
        "id": stable_id("CAR", "Kaito Uchino"),
        "name": "Kaito Uchino",
        "position": "P",
        "jersey": "67",
        "throws": "R",
        "bats": "R",
    },
    {
        "team": "CAR",
        "id": stable_id("CAR", "Yusei Tobita"),
        "name": "Yusei Tobita",
        "position": "P",
        "jersey": "59",
        "throws": "R",
        "bats": "R",
    },
    # MAY pitchers
    {
        "team": "MAY",
        "id": stable_id("MAY", "Luis Ramos"),
        "name": "Luis Ramos",
        "position": "P",
        "jersey": "39",
        "throws": "L",
        "bats": "L",
    },
    {
        "team": "MAY",
        "id": stable_id("MAY", "Shadai Colon"),
        "name": "Shadai Colon",
        "position": "P",
        "jersey": "69",
        "throws": "L",
        "bats": "L",
    },
    {
        "team": "MAY",
        "id": stable_id("MAY", "Sebastian Bentz"),
        "name": "Sebastian Bentz",
        "position": "P",
        "jersey": "46",
        "throws": "L",
        "bats": "L",
    },
    {"team": "MAY", "id": 826141, "name": "Sean Barnett", "position": "P", "jersey": "72", "throws": "R", "bats": "R"},
    {"team": "MAY", "id": 684320, "name": "Yariel Rodriguez", "position": "P", "jersey": "45", "throws": "R", "bats": "R"},
    # SJU
    {"team": "SJU", "id": 687436, "name": "Victor Torres", "position": "C", "jersey": "59", "bats": "R", "throws": "R"},
    {"team": "SJU", "id": 670582, "name": "Yariel Gonzalez", "position": "3B", "jersey": "5", "bats": "S", "throws": "R"},
    {"team": "SJU", "id": 806128, "name": "Antonio Jimenez", "position": "SS", "jersey": "61", "bats": "R", "throws": "R"},
    {
        "team": "SJU",
        "id": stable_id("SJU", "Santino Rosso"),
        "name": "Santino Rosso",
        "position": "INF",
        "jersey": "4",
        "bats": "R",
        "throws": "R",
    },
    {"team": "SJU", "id": 693049, "name": "Oscar Colas", "position": "INF", "jersey": "55", "bats": "L", "throws": "L"},
    {"team": "SJU", "id": 571681, "name": "Reymond Fuentes", "position": "OF", "jersey": "17", "bats": "L", "throws": "L"},
    {"team": "SJU", "id": 543803, "name": "Giovanni Soto", "position": "P", "jersey": "67", "throws": "L", "bats": "L"},
    {
        "team": "SJU",
        "id": stable_id("SJU", "Matthew Rivera"),
        "name": "Matthew Rivera",
        "position": "P",
        "jersey": "95",
        "throws": "R",
        "bats": "R",
    },
    {"team": "SJU", "id": 656924, "name": "Ryder Ryan", "position": "P", "jersey": "54", "throws": "R", "bats": "R"},
]

# Existing roster rows that are the same person — rename / jersey only.
ALIASES = [
    {"team": "CAG", "match_id": 806337, "name": "Jarrett Whorff", "jersey": "60", "position": "P"},
    {"team": "MAY", "match_id": -163227368, "name": "Luis Leroy Cruz", "jersey": "41", "position": "P"},
    {"team": "MAY", "match_id": 805248, "name": "Jonathan Happ", "jersey": "64", "position": "P"},
    {"team": "MAY", "match_id": 681493, "name": "Eddie Demurias", "jersey": "60", "position": "P"},
    {"team": "SJU", "match_id": 690702, "name": "Robbie Enriquez", "jersey": "27", "position": "OF"},
    {"team": "SJU", "match_id": -413913713, "name": "Manny Garcia", "jersey": "69", "position": "OF"},
    {"team": "SJU", "match_id": -324713460, "name": "Julio Cesar Ortiz", "jersey": "36", "position": "P"},
]


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc) -> None:
    (DATA / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def add_to_rosters() -> list[dict]:
    rost = load("lbprc_2025_rosters.json")
    by_team = {t.get("teamAbbrev"): t for t in rost.values()}
    added: list[dict] = []

    for row in ALIASES:
        pack = by_team[row["team"]]
        hit = next((p for p in pack["players"] if int(p.get("id") or 0) == row["match_id"]), None)
        if not hit:
            print(f"alias miss {row}")
            continue
        old = hit.get("name")
        hit["name"] = row["name"]
        hit["jersey"] = str(row.get("jersey") or hit.get("jersey") or "")
        if row.get("position"):
            hit["position"] = row["position"]
        print(f"alias {row['team']} {old} -> {hit['name']} jersey={hit['jersey']}")

    for row in ADDITIONS:
        pack = by_team[row["team"]]
        by_id = {int(p["id"]): p for p in pack["players"] if p.get("id") is not None}
        by_name = {erp.ligapr.norm_name(p.get("name") or ""): p for p in pack["players"]}
        if row["id"] in by_id or erp.ligapr.norm_name(row["name"]) in by_name:
            print(f"roster already has {row['team']} {row['name']}")
            continue
        player = {
            "id": row["id"],
            "name": row["name"],
            "position": row["position"],
            "jersey": str(row.get("jersey") or ""),
            "status": "Active",
        }
        pack["players"].append(player)
        added.append({**row, **player})
        print(f"roster + {row['team']} {row['name']} ({row['id']})")

    dump("lbprc_2025_rosters.json", rost)
    return added


def ensure_all(players: list[dict]) -> None:
    for row in players:
        player = erp.find_roster(row["name"], row["id"], row["team"])
        if not player:
            raise SystemExit(f"missing after roster write: {row}")
        if row.get("bats"):
            player["bats"] = row["bats"]
        if row.get("throws"):
            player["throws"] = row["throws"]
        result = erp.ensure_and_fill(player)
        print(
            f"ensure+fill {player['team']} {player['name']}: {result.get('actions')} "
            f"fill={(result.get('fill') or {}).get('status')}"
        )


def seed_positioning(hitters: list[dict]) -> None:
    """Append STRAIGHT/N defaults for new hitters; preserve existing cells + golden lock."""
    if not hitters:
        return
    path = DATA / "positioning_v2.json"
    doc = json.loads(path.read_text())
    of_map = doc.setdefault("of", {})
    inf_map = doc.setdefault("inf", {})
    hands = doc.setdefault("hands", {})
    of_pos = ["LF", "CF", "RF"]
    of_counts = ["PRE", "2K"]
    inf_pos = ["1B", "2B", "SS", "3B"]
    inf_sits = ["EMPTY", "RISP", "DP"]
    added_cells = 0
    for h in hitters:
        team = h["team"]
        name = h["name"]
        key_name = name.lower()
        bats = (h.get("bats") or "R").upper()[:1]
        if bats in ("R", "L", "S"):
            hands[f"{team}|{key_name}"] = bats
        for hand in ("L", "R"):
            for count in of_counts:
                for pos in of_pos:
                    k = f"{team}|{key_name}|{pos}|{count}|{hand}"
                    if k not in of_map:
                        of_map[k] = "STRAIGHT"
                        added_cells += 1
            for pos in inf_pos:
                for sit in inf_sits:
                    if sit == "DP" and pos == "3B":
                        code = "H"
                    elif sit == "DP" and pos == "1B":
                        code = "BAG"
                    else:
                        code = "N"
                    k = f"{team}|{key_name}|{pos}|{sit}|{hand}"
                    if k not in inf_map:
                        inf_map[k] = code
                        added_cells += 1
    meta = doc.setdefault("meta", {})
    meta["players_filled"] = int(meta.get("players_filled") or 0) + len(hitters)
    meta["of_cells"] = len(of_map)
    meta["inf_cells"] = len(inf_map)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"positioning seeded cells={added_cells} for {len(hitters)} hitters")


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
    meta_by_id = {int(p["id"]): p for p in players}
    for player in targets_h:
        row = hitter_by.get(holes.player_key(player))
        if not row:
            continue
        meta = meta_by_id.get(int(player["id"]), {})
        row["team"] = meta.get("team") or player.get("team") or row.get("team")
        if meta.get("bats") and not row.get("bats"):
            row["bats"] = meta["bats"]
        if meta.get("throws") and not row.get("throws"):
            row["throws"] = meta["throws"]
        if meta.get("position") and not row.get("position"):
            row["position"] = meta["position"]
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
        meta = meta_by_id.get(int(player["id"]), {})
        row["team"] = meta.get("team") or player.get("team") or row.get("team")
        line = pitcher_2026.get(player["id"]) or pitcher_2025.get(player["id"])
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(f"pitcher bar {player['name']}: {line.get('season')} {line.get('src') or line.get('source')}")
        if meta.get("throws"):
            row["throws"] = row.get("throws") or meta["throws"]
        if player["id"] < 0:
            row["mlb_id"] = player["id"]
    holes.dump(DATA / "advance_opposing_pitchers.json", pitcher_doc)
    holes.save_checkpoint(ckpt)


def bump_pos_seed_version() -> None:
    path = ROOT / "positioning.js"
    text = path.read_text()
    old = "const SEED_VERSION = '2026-11-03f';"
    new = "const SEED_VERSION = '2026-11-04a';"
    if old in text:
        path.write_text(text.replace(old, new, 1))
        print("bumped positioning SEED_VERSION -> 2026-11-04a")
    else:
        print("SEED_VERSION bump skipped (unexpected current value)")


def main() -> None:
    added = add_to_rosters()
    ensure_all(added)
    hitters = [p for p in added if str(p.get("position") or "").upper() not in erp.PITCHER_POS]
    seed_positioning(hitters)
    seed_summer_lines(added)
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)
    print("one-page rebuilt")
    bump_pos_seed_version()

    report = {"added": added, "aliases": ALIASES, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (DATA / "roster_official_2026_11_04_added.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"done. newly rostered={len(added)}")
    for team in ("CAG", "CAR", "MAY", "SJU"):
        rows = [p for p in added if p["team"] == team]
        print(f"\n=== {team} added {len(rows)} ===")
        for p in rows:
            print(f"  {p['name']}\t{p['position']}\t{p['id']}")


if __name__ == "__main__":
    main()

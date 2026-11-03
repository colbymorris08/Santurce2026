#!/usr/bin/env python3
"""Add Caguas card-sheet players and seed arsenal / card caches.

Players (confirmed Nov 2026):
  John McMillon   669111  RHP
  Zach Veen       805731  LHP   (not Zac Veen OF 691728)
  Tyler Santana   687825  RHP
  Steven Echavarria 806064 RHP
  Jonathan Morales 664966 C
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import build_ligapr_daily as ligapr
import build_pregame_arsenal as bpa
import fill_remaining_holes as holes

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

ADDITIONS = [
    {"id": 669111, "name": "John McMillon", "position": "P", "jersey": "", "throws": "R"},
    {"id": 805731, "name": "Zach Veen", "position": "P", "jersey": "", "throws": "L"},
    {"id": 687825, "name": "Tyler Santana", "position": "P", "jersey": "", "throws": "R"},
    {"id": 806064, "name": "Steven Echavarria", "position": "P", "jersey": "", "throws": "R"},
    {"id": 664966, "name": "Jonathan Morales", "position": "C", "jersey": "", "throws": None},
]


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc) -> None:
    path = DATA / name
    if name.endswith(".json") and name in {"pregame_card_stats.json", "advance_heatmaps.json"}:
        path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n")
    else:
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def add_to_roster() -> list[dict]:
    rost = load("lbprc_2025_rosters.json")
    cag = next(t for t in rost.values() if t.get("teamAbbrev") == "CAG")
    by_id = {int(p["id"]): p for p in cag["players"] if p.get("id")}
    added = []
    for row in ADDITIONS:
        if row["id"] in by_id:
            print(f"roster already has {row['name']}")
            continue
        player = {
            "id": row["id"],
            "name": row["name"],
            "position": row["position"],
            "jersey": row["jersey"],
            "status": "Active",
        }
        cag["players"].append(player)
        added.append(player)
        print(f"roster + {row['name']} ({row['id']})")
    dump("lbprc_2025_rosters.json", rost)
    return added


def upsert_list(rows: list[dict], player: dict, id_key: str = "player_id") -> dict:
    pid = int(player["id"])
    for row in rows:
        if int(row.get(id_key) or 0) == pid:
            row["name"] = player["name"]
            row["team"] = "CAG"
            if "player_id" in row or id_key == "player_id":
                row["player_id"] = pid
            return row
    stub = {
        "name": player["name"],
        "player_id": pid,
        "team": "CAG",
        "mlb_debut": None,
        "birth_country": None,
        "stuff_plus": None,
        "pitches": None,
        "arsenal": [],
        "source": "roster-add",
        "level_hint": None,
    }
    rows.append(stub)
    return stub


def seed_stuff_and_arsenals(pitchers: list[dict]) -> None:
    stuff = load("pregame_opposing_stuff.json")
    if isinstance(stuff, dict):
        stuff_rows = stuff.get("pitchers") or stuff.get("players") or []
    else:
        stuff_rows = stuff

    ar_doc = load("pregame_pitcher_arsenals.json")
    ar_rows = ar_doc.get("pitchers") or []
    ar_by = {int(r["player_id"]): r for r in ar_rows if r.get("player_id")}

    throws = bpa.fetch_throws([p["id"] for p in pitchers])
    for player in pitchers:
        base = upsert_list(stuff_rows, player)
        base["throws"] = throws.get(player["id"]) or player.get("throws")
        print(f"arsenal fetch {player['name']} ({player['id']})", flush=True)
        # McMillon: prefer 2026 then 2025 (user rule). Others newest-first.
        seasons = (2026, 2025, 2024, 2023, 2022)
        ps_pitches, ps_season, ps_source = bpa.fetch_ps_arsenal(player["id"], seasons=seasons)
        if player["id"] == 669111 and ps_season == 2026 and not ps_pitches:
            ps_pitches, ps_season, ps_source = bpa.fetch_ps_arsenal(player["id"], seasons=(2025, 2024, 2023))
        print(f"  PS season={ps_season} pitches={len(ps_pitches)} src={ps_source}", flush=True)
        sc = bpa.fetch_statcast_arsenal(player["id"])
        print(
            f"  Statcast pitches={sc.get('statcast_pitches')} seasons={sc.get('seasons')}",
            flush=True,
        )
        row = bpa.build_row(base, ps_pitches, ps_season, ps_source, sc)
        row["team"] = "CAG"
        if player.get("throws") and not row.get("throws"):
            row["throws"] = player["throws"]
        ar_by[player["id"]] = row
        time.sleep(0.15)

    ar_doc["pitchers"] = list(ar_by.values())
    ar_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dump("pregame_pitcher_arsenals.json", ar_doc)
    if isinstance(stuff, dict):
        stuff["pitchers"] = stuff_rows
        dump("pregame_opposing_stuff.json", stuff)
    else:
        dump("pregame_opposing_stuff.json", stuff_rows)


def ensure_advance_pitchers(pitchers: list[dict]) -> None:
    doc = load("advance_opposing_pitchers.json")
    rows = doc.get("pitchers") or []
    by_id = {int(r["mlb_id"]): r for r in rows if r.get("mlb_id")}
    by_name = {holes.norm(r.get("name") or ""): r for r in rows}
    for player in pitchers:
        row = by_id.get(player["id"]) or by_name.get(holes.norm(player["name"]))
        if not row:
            row = {
                "name": player["name"],
                "mlb_id": player["id"],
                "team": "CAG",
                "throws": player.get("throws"),
                "synergy_id": None,
                "pitches": 0,
                "summer_line": None,
                "platoon": {},
                "count_mix": {},
            }
            rows.append(row)
            print(f"advance pitcher stub {player['name']}")
        else:
            row["team"] = "CAG"
            row["mlb_id"] = player["id"]
            row["name"] = player["name"]
    doc["pitchers"] = rows
    dump("advance_opposing_pitchers.json", doc)


def ensure_advance_hitter(hitter: dict) -> None:
    doc = load("advance_opposing_hitters.json")
    rows = doc.get("hitters") or []
    by_id = {int(r["mlb_id"]): r for r in rows if r.get("mlb_id")}
    row = by_id.get(hitter["id"])
    if not row:
        row = {
            "name": hitter["name"],
            "mlb_id": hitter["id"],
            "team": "CAG",
            "bats": "R",
            "throws": "R",
            "position": "C",
            "summer_line": None,
            "synergy_id": None,
        }
        rows.append(row)
        print(f"advance hitter stub {hitter['name']}")
    else:
        row["team"] = "CAG"
        row["name"] = hitter["name"]
        row["mlb_id"] = hitter["id"]
    doc["hitters"] = rows
    dump("advance_opposing_hitters.json", doc)


def seed_tth(players: list[dict]) -> None:
    """Pitchers + catchers only on the TTH / pop sheet."""
    pitcher_pos = {"P", "RHP", "LHP", "RHR", "LHR", "RHS", "LHS"}
    doc = load("pregame_tth_pop.json")
    rows = doc.get("players") or []
    by_id = {int(r["player_id"]): r for r in rows if r.get("player_id")}
    for player in players:
        pos = str(player.get("position") or "").upper()
        if pos not in pitcher_pos and pos != "C":
            continue
        sheet_pos = "P" if pos in pitcher_pos else "C"
        if player["id"] in by_id:
            by_id[player["id"]]["team"] = "CAG"
            by_id[player["id"]]["name"] = player["name"]
            by_id[player["id"]]["position"] = sheet_pos
            continue
        rows.append(
            {
                "player_id": player["id"],
                "name": player["name"],
                "team": "CAG",
                "position": sheet_pos,
                "jersey": player.get("jersey") or "",
                "status": "Active",
                "time_to_home": None,
                "catcher_pop_time": None,
                "catcher_arm": None,
                "notes": "",
            }
        )
    doc["players"] = rows
    dump("pregame_tth_pop.json", doc)


def main() -> None:
    add_to_roster()
    pitchers = [p for p in ADDITIONS if p["position"] == "P"]
    hitter = next(p for p in ADDITIONS if p["position"] != "P")
    seed_stuff_and_arsenals(pitchers)
    ensure_advance_pitchers(pitchers)
    ensure_advance_hitter(hitter)
    seed_tth(ADDITIONS)

    # Summer / winter bars for new roster members via remaining-holes helpers
    ckpt = json.loads(holes.CHECKPOINT.read_text()) if holes.CHECKPOINT.is_file() else {}
    pit_rows, hit_rows = holes.roster_players()
    want_p = {p["id"] for p in pitchers}
    want_h = {hitter["id"]}
    targets_p = [p for p in pit_rows if int(p.get("id") or 0) in want_p]
    targets_h = [h for h in hit_rows if int(h.get("id") or 0) in want_h]
    holes.resolve_ids(targets_h, ckpt, pitcher=False)
    holes.resolve_ids(targets_p, ckpt, pitcher=True)

    hitter_doc = holes.load("advance_opposing_hitters.json")
    pitcher_doc = holes.load("advance_opposing_pitchers.json")
    hitter_by = holes.index_rows(hitter_doc.get("hitters") or [])
    pitcher_by = holes.index_rows(pitcher_doc.get("pitchers") or [])

    import backfill_pitcher_prior_seasons as prior

    p_ids = [p["id"] for p in targets_p]
    pitcher_2026 = {}
    pitcher_2025 = {}
    try:
        import build_advance_synergy as bas

        pitcher_2026 = bas.fetch_2026_pitcher_summer_lines(p_ids)
    except Exception as exc:  # noqa: BLE001
        print(f"2026 pitcher lines: {exc}")
    try:
        need_2025 = [i for i in p_ids if i not in pitcher_2026]
        # McMillon: if 2026 sample exists keep it; else force 2025
        pitcher_2025 = prior.summer_2025_lines(need_2025) if need_2025 else {}
        if 669111 in pitcher_2026:
            print("McMillon using 2026 summer line")
        elif 669111 in pitcher_2025:
            print("McMillon no usable 2026 — using 2025")
        elif 669111 not in pitcher_2026:
            # Explicit 2025 pull for McMillon even if 2026 returned empty-ish
            extra = prior.summer_2025_lines([669111])
            pitcher_2025.update(extra)
            if 669111 in extra:
                print("McMillon forced 2025 summer line")
    except Exception as exc:  # noqa: BLE001
        print(f"2025 pitcher lines: {exc}")

    for player in targets_p:
        row = pitcher_by.get(holes.player_key(player))
        if not row:
            continue
        line = pitcher_2026.get(player["id"]) or pitcher_2025.get(player["id"])
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(f"pitcher bar {player['name']}: {line.get('season')} {line.get('src') or line.get('source')}")
    holes.dump(DATA / "advance_opposing_pitchers.json", pitcher_doc)

    h_ids = [hitter["id"]]
    summer_2026 = holes.hitting_lines(h_ids, "2026")
    summer_2025 = holes.hitting_lines([i for i in h_ids if i not in summer_2026], "2025")
    local_by_id, local_by_name = holes.local_hitting_index()
    for player in targets_h:
        row = hitter_by.get(holes.player_key(player))
        if not row:
            continue
        line = summer_2026.get(player["id"]) or summer_2025.get(player["id"])
        if not line:
            line = holes.local_hitting_for(player, ckpt, local_by_id, local_by_name)
        if line:
            row["summer_line"] = line
            print(f"hitter bar {player['name']}: {(line or {}).get('season')} {(line or {}).get('src')}")
    holes.dump(DATA / "advance_opposing_hitters.json", hitter_doc)
    holes.save_checkpoint(ckpt)

    one_page = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one_page)
    print("one-page rebuilt")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Add official Leones de Ponce 2025-26 active roster gaps + publish one-page notes.

Source: ROSTER EQUIPO DE PONCE 2025-26.docx (active table) + lineup card IMG_6993
for today's PON strategy lineup/bench. Pitcher notes from
"11-4 Leones de Ponce Pitcher One Page.pdf".

Does not wipe existing SAN/CAG strategy selections or scout note overrides.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import build_ligapr_daily as ligapr
import ensure_roster_player as erp
import fill_remaining_holes as holes
from update_2026_27_rosters import stable_id

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

# Active-roster adds / renames from official doc (first+last + jersey).
ADDITIONS = [
    {
        "id": stable_id("PON", "Hemmanuel Rosario"),
        "name": "Hemmanuel Rosario",
        "position": "C",
        "jersey": "31",
        "bats": "R",
        "throws": "R",
    },
    {
        # Stats API Jose Rivera 572076 is DOB 1990 — wrong. Official DOB 1999-10-07.
        "id": stable_id("PON", "Jose Rivera"),
        "name": "Jose Rivera",
        "position": "INF",
        "jersey": "9",
        "bats": "R",
        "throws": "R",
    },
    {
        "id": stable_id("PON", "Ryan Murphy"),
        "name": "Ryan Murphy",
        "position": "P",
        "jersey": "40",
        "bats": "R",
        "throws": "R",
    },
]

# Rename / jersey / id fixes on existing PON rows.
PATCHES = [
    {
        "match_name": "Aponte",
        "name": "John Aponte",
        # Keep synthetic until Stats API exposes him; first+last for Synergy search.
        "id": stable_id("PON", "John Aponte"),
        "jersey": "57",
        "position": "P",
        "throws": "R",
        "bats": "R",
    },
    {"match_name": "Jonathan Bermudez", "jersey": "41"},
    {"match_name": "Hector Quinones", "jersey": "29"},
    {"match_name": "Eli Serrano", "jersey": "51"},
    # Card last-name guesses that collide with official jersey holders.
    {"match_name": "Eddie Rosario", "jersey": ""},
    {"match_name": "Emmanuel Rivera", "jersey": ""},
]

# Lineup card 11-4 (starters + available). Official first+last where known.
LINEUP = [
    {"name": "John Bay", "pos": "8"},  # CF
    {"name": "Ezequiel Pagan", "pos": "9"},  # RF
    {"name": "Jacob Reimer", "pos": "5"},  # 3B
    {"name": "Austin Murr", "pos": "3"},  # 1B
    {"name": "Colton Becker", "pos": "7"},  # LF
    {"name": "Jesmuel Valentin", "pos": "4"},  # 2B
    {"name": "Chris Arroyo", "pos": "1"},  # DH
    {"name": "William Simoneit", "pos": "2"},  # C
    {"name": "Jose Torres", "pos": "6"},  # SS
]
SP_NAME = "Kai Yokogawa"

# Available / bench from card — map last names to official first+last.
BENCH_NAMES = [
    "Juan Montero",
    "Gabriel Rosado",
    "Jose Ortiz",
    "Hemmanuel Rosario",
    "Kevin Santa",
    "Jose Rivera",
    "Aldemar Burgos",
    "Yeniel Laboy",
    "Jankel Ortiz",
    "Keito Matsuura",
    "Kyohei Yamashiro",
    "Harold Cortijo",
    "Eric Garcia",
    "Alex Claudio",
    "Juan Marcano",
    "Gabriel Rodriguez",
    "Julio Torres",
    "Andrew Marrero",
    "Lenny Torres",
    "John Aponte",
    "Ryan Murphy",  # official active not on card — include as available
    "Eli Serrano",
    "Jonathan Bermudez",
    "Hector Quinones",
]

# Notes column from 11-4 Leones de Ponce Pitcher One Page.pdf (pdftotext -layout).
PDF_NOTES = {
    "jonathanbermudez": (
        "FB average shape, primary offspeed big sweeper, average CH, "
        "flashes SL/CT shape vs LHH"
    ),
    "rickycastro": (
        "Ride on FB, high release. CH has some depth, CT is primary BB. "
        "More SL to RHH, CH to LHH"
    ),
    "alexclaudio": (
        "Heavy low slot sinker, CH mimics SNK movement, SL is cuttery. "
        "CH to RHH, SL to LHH"
    ),
    "haroldcortijo": (
        "FB does not have ride, SL is gyro shape, CH has good depth and "
        "throws only to LHH"
    ),
    "ericgarcia": "4S and SL only - 4S no ride. SL is a short slurve. 75% FB",
    "jorhanlaboy": "Upper 80's LHP, CB and CH are offspeed pitches",
    "juanmarcano": ">50% FB, leans on SL to RHH, some CH to LHH",
    "andrewmarrero": (
        "Avg. ride on 4S, high release. SL has some sweep, 50/50 FB SL to RHH"
    ),
    "keitomatsuura": "High FB usage, low 90's",
    "omarmelendez": "High release, leans on FB, CH to RHH, spin to LHH",
    "michaelmorales": (
        "Avg. ride, some cut to 4S. Good extension. Sweeper to RHH, "
        "CB/CT to LHH. CB has depth and sweep"
    ),
    "noelpinto": "Tons of FB, CH to RHH. Damage down in zone",
    "hectorquinones": "Damage down, average release height, SL to RHH, SP to LHH",
    "erikrivera": "Some ride on FB, short slider, high extension. Damage in to RHH.",
    "gabrielrodriguez": (
        "Ride on FB, low spin on splitter, wobbles - throws to RHH. high "
        "release. Short slider to LHH"
    ),
    "alexsantos": (
        "Avg FB movement, good sweeper with lift and sweep. Cutter to "
        "LHH SW to RHH. Wide release"
    ),
    "charleesoto": (
        "Ride and run, short slider, most pitches move arm side. Damage up "
        "and in to LHH, middle to RHH."
    ),
    "erictorres": "Run on FB, Sweeper to LHH, low/wide release",
    "juliotorres": "High FB usage, damage middle and in to RHH. Low velocity.",
    "lennytorres": (
        "Some ride and run, high velocity. short slider to RHH. Some SNK "
        "with some run no sink. CH has some depth."
    ),
    "kaiyokogawa": (
        "Damage away from RHH, down to LHH. Works CT CB SL and SP. "
        "Leans fb but throws all pitches"
    ),
    "mariozabala": "Good extension, some ride and short slider. High FB usage. See it up.",
}


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc) -> None:
    (DATA / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def note_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def add_and_patch() -> tuple[list[dict], list[dict]]:
    rost = load("lbprc_2025_rosters.json")
    pon = next(t for t in rost.values() if t.get("teamAbbrev") == "PON")
    by_id = {int(p["id"]): p for p in pon["players"] if p.get("id") is not None}
    by_name = {erp.ligapr.norm_name(p.get("name") or ""): p for p in pon["players"]}
    added: list[dict] = []
    wired: list[dict] = []

    for row in ADDITIONS:
        key = erp.ligapr.norm_name(row["name"])
        if row["id"] in by_id or key in by_name:
            existing = by_id.get(row["id"]) or by_name[key]
            if row.get("jersey") is not None:
                existing["jersey"] = str(row["jersey"])
            print(f"PON already has {row['name']}")
            wired.append({**row, "team": "PON", "id": existing["id"]})
            continue
        player = {
            "id": row["id"],
            "name": row["name"],
            "position": row["position"],
            "jersey": str(row.get("jersey") or ""),
            "status": "Active",
        }
        if row.get("bats"):
            player["bats"] = row["bats"]
        if row.get("throws"):
            player["throws"] = row["throws"]
        pon["players"].append(player)
        by_id[row["id"]] = player
        by_name[key] = player
        added.append({**row, "team": "PON"})
        wired.append({**row, "team": "PON"})
        print(f"PON + {row['name']} ({row['id']}) #{row.get('jersey')} {row['position']}")

    for patch in PATCHES:
        key = erp.ligapr.norm_name(patch["match_name"])
        existing = by_name.get(key)
        if not existing:
            # Also try current "Aponte" after first pass
            print(f"PON patch miss {patch['match_name']}")
            continue
        old_name = existing.get("name")
        old_id = existing.get("id")
        if patch.get("name"):
            existing["name"] = patch["name"]
        if "jersey" in patch:
            existing["jersey"] = str(patch["jersey"])
        if patch.get("position"):
            existing["position"] = patch["position"]
        if patch.get("id") is not None and int(patch["id"]) != int(old_id or 0):
            existing["id"] = patch["id"]
        if patch.get("bats"):
            existing["bats"] = patch["bats"]
        if patch.get("throws"):
            existing["throws"] = patch["throws"]
        # Refresh name index after rename
        by_name.pop(key, None)
        by_name[erp.ligapr.norm_name(existing["name"])] = existing
        meta = {
            "id": existing["id"],
            "name": existing["name"],
            "position": existing.get("position"),
            "jersey": existing.get("jersey") or "",
            "team": "PON",
            "throws": patch.get("throws") or existing.get("throws"),
            "bats": patch.get("bats") or existing.get("bats"),
            "old_id": old_id,
            "old_name": old_name,
        }
        wired.append(meta)
        print(
            f"PON patch {patch['match_name']} -> {existing['name']} "
            f"id={existing['id']} jersey={existing.get('jersey')!r}"
        )
        # Propagate rename/id into advance/arsenal stubs keyed by old identity.
        _retarget_caches(old_name, old_id, existing)

    dump("lbprc_2025_rosters.json", rost)
    return added, wired


def _retarget_caches(old_name: str | None, old_id, player: dict) -> None:
    """Update name/id on existing cache rows when we rename Aponte → John Aponte."""
    mid = int(player.get("id") or 0)
    old_mid = int(old_id or 0)
    old_key = erp.ligapr.norm_name(old_name or "")
    new_key = erp.ligapr.norm_name(player.get("name") or "")

    def touch_list(rows: list, id_keys: tuple[str, ...]) -> None:
        for row in rows:
            hit = False
            for ik in id_keys:
                try:
                    if old_mid and int(row.get(ik) or 0) == old_mid:
                        hit = True
                except (TypeError, ValueError):
                    pass
            if old_key and erp.ligapr.norm_name(row.get("name") or "") == old_key:
                hit = True
            if not hit:
                continue
            row["name"] = player["name"]
            row["team"] = "PON"
            for ik in id_keys:
                if ik in row or ik.endswith("_id"):
                    row[ik] = mid if mid else row.get(ik)
            if "throws" in row and player.get("throws"):
                row["throws"] = player["throws"]

    ar = load("pregame_pitcher_arsenals.json")
    touch_list(ar.get("pitchers") or [], ("player_id", "mlb_id"))
    dump("pregame_pitcher_arsenals.json", ar)

    adv = load("advance_opposing_pitchers.json")
    touch_list(adv.get("pitchers") or [], ("mlb_id", "player_id"))
    dump("advance_opposing_pitchers.json", adv)

    stuff = load("pregame_opposing_stuff.json")
    if isinstance(stuff, list):
        touch_list(stuff, ("player_id", "mlb_id"))
        dump("pregame_opposing_stuff.json", stuff)
    else:
        touch_list(stuff.get("pitchers") or [], ("player_id", "mlb_id"))
        dump("pregame_opposing_stuff.json", stuff)

    tth = load("pregame_tth_pop.json")
    touch_list(tth.get("players") or [], ("player_id",))
    dump("pregame_tth_pop.json", tth)

    # one-page rebuilt later; notes keyed by new name


def ensure_all(rows: list[dict]) -> None:
    seen = set()
    for row in rows:
        key = (row["team"], int(row["id"]))
        if key in seen:
            continue
        seen.add(key)
        player = erp.find_roster(row["name"], row["id"], "PON")
        if not player:
            player = erp.find_roster(None, row["id"], "PON")
        if not player:
            raise SystemExit(f"missing after roster write: {row}")
        if row.get("bats"):
            player["bats"] = row["bats"]
        if row.get("throws"):
            player["throws"] = row["throws"]
        if row.get("jersey") is not None:
            player["jersey"] = str(row["jersey"])
        result = erp.ensure_and_fill(player)
        print(
            f"ensure+fill PON {player['name']}: {result.get('actions')} "
            f"fill={(result.get('fill') or {}).get('status')}"
        )


def publish_notes() -> dict:
    notes = {k: v for k, v in PDF_NOTES.items() if v}
    # Also alias Aponte → johnaponte if any leftover
    if "johnaponte" not in notes and notes.get("aponte"):
        notes["johnaponte"] = notes["aponte"]
    doc = {
        "updated": now(),
        "note": (
            "Scout notes for Pitcher One Page Sheet. Keys are normalized names "
            "(lowercase alphanumeric) or exact display names. HTML not allowed — "
            "plain text; the UI keeps line breaks."
        ),
        "notes": notes,
        "published_from": "11-4 Leones de Ponce Pitcher One Page.pdf",
    }
    dump("pitcher_one_page_notes.json", doc)
    print(f"published {len(notes)} pitcher notes")
    return doc


def seed_pon_strategy(notes_doc: dict) -> None:
    """Put today's card lineup + available on PON strategy without wiping SAN/CAG."""
    rost = load("lbprc_2025_rosters.json")
    pon = next(t for t in rost.values() if t.get("teamAbbrev") == "PON")
    by_name = {erp.ligapr.norm_name(p.get("name") or ""): p for p in pon["players"]}

    def pid(name: str) -> int | None:
        p = by_name.get(erp.ligapr.norm_name(name))
        return int(p["id"]) if p and p.get("id") is not None else None

    lineup = []
    for slot in LINEUP:
        mid = pid(slot["name"])
        if mid is None:
            print(f"strategy lineup miss {slot['name']}")
            continue
        lineup.append({"id": mid, "pos": slot["pos"]})
    while len(lineup) < 9:
        lineup.append(None)

    in_line = {r["id"] for r in lineup if r}
    bench = []
    for name in BENCH_NAMES:
        mid = pid(name)
        if mid is None:
            print(f"strategy bench miss {name}")
            continue
        if mid in in_line:
            continue
        if any(b["id"] == mid for b in bench):
            continue
        p = by_name[erp.ligapr.norm_name(name)]
        pos = str(p.get("position") or "DH")
        # Map rough role → strategy pos codes used by cards.js
        pos_map = {
            "C": "2",
            "1B": "3",
            "2B": "4",
            "3B": "5",
            "SS": "6",
            "INF": "4",
            "OF": "7",
            "CF": "8",
            "LF": "7",
            "RF": "9",
            "P": "1",
            "RHP": "1",
            "LHP": "1",
            "DH": "1",
        }
        bench.append({"id": mid, "pos": pos_map.get(pos.upper(), "1" if erp.is_pitcher(p) else "DH")})

    sp = pid(SP_NAME)

    gd = load("game_day_state.json")
    strat = gd.setdefault("strategy", {})
    teams = strat.setdefault("teams", {})
    # Preserve SAN/CAG entirely; only set/merge PON.
    existing = teams.get("PON") or {"sp": None, "pen": [], "lineup": [], "bench": []}
    # Keep any prior pen selections; replace lineup/bench/sp from card for today's sheet.
    pen = list(existing.get("pen") or [])
    teams["PON"] = {
        "sp": sp if sp is not None else existing.get("sp"),
        "pen": pen,
        "lineup": lineup,
        "bench": bench,
    }
    # Do NOT change opponent if already set for a live game — only add PON pack.
    gd["one_page_notes"] = notes_doc.get("notes") or {}
    gd["updated"] = now()
    gd["label"] = gd.get("label") or "11-4"
    gd["date_key"] = "2026-11-04"
    gd["note"] = (
        (gd.get("note") or "")
        + " | PON official roster + card lineup/bench + pitcher one-page notes 11-4."
    ).strip(" |")
    dump("game_day_state.json", gd)
    print(f"PON strategy: lineup={sum(1 for r in lineup if r)} bench={len(bench)} sp={sp}")


def seed_summer(players: list[dict]) -> None:
    ckpt = json.loads(holes.CHECKPOINT.read_text()) if holes.CHECKPOINT.is_file() else {}
    pit_rows, hit_rows = holes.roster_players()
    want = {int(p["id"]) for p in players}
    targets_p = [p for p in pit_rows if int(p.get("id") or 0) in want and p.get("team") == "PON"]
    targets_h = [h for h in hit_rows if int(h.get("id") or 0) in want and h.get("team") == "PON"]
    # Only resolve positive MLB ids
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
        row["name"] = player["name"]
        line = summer_2026.get(player["id"]) or summer_2025.get(player["id"])
        if not line:
            line = holes.local_hitting_for(player, ckpt, local_by_id, local_by_name)
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(f"hitter bar {player['name']}: {(line or {}).get('season')}")
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
            print(f"pitcher summer: {exc}")
    for player in targets_p:
        row = pitcher_by.get(holes.player_key(player))
        if not row:
            continue
        row["team"] = "PON"
        row["name"] = player["name"]
        line = pitcher_2026.get(player["id"]) or pitcher_2025.get(player["id"])
        if line and not row.get("summer_line"):
            row["summer_line"] = line
            print(f"pitcher bar {player['name']}: {line.get('season')}")
        if player.get("throws"):
            row["throws"] = row.get("throws") or player["throws"]
        row["mlb_id"] = player["id"]
    holes.dump(DATA / "advance_opposing_pitchers.json", pitcher_doc)
    holes.save_checkpoint(ckpt)


def main() -> None:
    added, wired = add_and_patch()
    ensure_all(wired)
    seed_summer(wired)
    notes = publish_notes()
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)
    print("one-page rebuilt")
    seed_pon_strategy(notes)

    report = {
        "added": added,
        "wired": [
            {"name": w["name"], "id": w["id"], "jersey": w.get("jersey"), "position": w.get("position")}
            for w in wired
        ],
        "notes": len(notes.get("notes") or {}),
        "updated": now(),
    }
    (DATA / "ponce_official_nov4_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"newly_rostered": [a["name"] for a in added], "notes": report["notes"]}, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Refresh scout-sheet seeds + card/BR caches from current lbprc_2025_rosters.json."""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

OF_POS = {"OF", "LF", "CF", "RF", "UT", "UTL", "DH"}
PITCHER_POS = {"P", "RHP", "LHP", "RHR", "LHR", "RHS", "LHS"}
CATCHER_POS = {"C"}


def load(name: str):
    return json.loads((DATA / name).read_text())


def write(name: str, value, *, compact: bool = False) -> None:
    kwargs = {"ensure_ascii": False, "indent": None if compact else 2}
    if compact:
        kwargs["separators"] = (",", ":")
    text = json.dumps(value, **kwargs)
    if not compact:
        text += "\n"
    (DATA / name).write_text(text)


def roster_players() -> list[dict]:
    rost = load("lbprc_2025_rosters.json")
    out = []
    for team in rost.values():
        abbr = team.get("teamAbbrev")
        tname = team.get("teamName")
        for p in team.get("players") or []:
            pos = str(p.get("position") or "").upper()
            role = "P" if pos in PITCHER_POS else "H"
            out.append(
                {
                    "player_id": p.get("id"),
                    "name": p.get("name"),
                    "team": abbr,
                    "team_name": tname,
                    "position": pos or ("P" if role == "P" else "IF"),
                    "jersey": p.get("jersey") or "",
                    "status": p.get("status") or "Active",
                    "role": role,
                }
            )
    return out


def sync_tth(players: list[dict]) -> None:
    prev = {}
    if (DATA / "pregame_tth_pop.json").exists():
        old = load("pregame_tth_pop.json")
        for row in old.get("players") or []:
            key = f"{row.get('team')}|{str(row.get('name') or '').lower()}"
            prev[key] = row
            if row.get("player_id") is not None:
                prev[str(row["player_id"])] = row
    rows = []
    for p in players:
        if p["role"] == "P" or p["position"] in CATCHER_POS:
            key = f"{p['team']}|{p['name'].lower()}"
            old = prev.get(str(p["player_id"])) or prev.get(key) or {}
            rows.append(
                {
                    "player_id": p["player_id"],
                    "name": p["name"],
                    "team": p["team"],
                    "position": "P" if p["role"] == "P" else "C",
                    "jersey": p["jersey"],
                    "status": p["status"],
                    "time_to_home": old.get("time_to_home"),
                    "catcher_pop_time": old.get("catcher_pop_time"),
                    "catcher_arm": old.get("catcher_arm"),
                    "notes": old.get("notes") or "",
                }
            )
    teams = sorted({(p["team"], p["team_name"]) for p in players})
    write(
        "pregame_tth_pop.json",
        {
            "meta": {
                "season": "2026-27 LBPRC rosters",
                "note": "Pitcher time-to-home + catcher pop/arm. Site auto-saves overrides to GitHub when a PAT is set.",
                "units": "seconds (times) · 20–80 grades (arm)",
                "storage_key": "santurce_tth_pop_overrides_v1",
                "repo_path": "data/pregame_tth_pop.json",
            },
            "teams": [{"abbrev": a, "name": n} for a, n in teams],
            "players": rows,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    print(f"tth players={len(rows)}")


def sync_of_arms(players: list[dict]) -> None:
    prev = {}
    path = DATA / "pregame_of_arms.json"
    if path.exists():
        old = json.loads(path.read_text())
        for row in old.get("players") or []:
            key = f"{row.get('team')}|{str(row.get('name') or '').lower()}"
            prev[key] = row
            if row.get("player_id") is not None:
                prev[str(row["player_id"])] = row
    rows = []
    for p in players:
        if p["role"] == "P":
            continue
        pos = p["position"]
        if pos not in OF_POS and pos not in {"LF", "CF", "RF", "OF"}:
            continue
        key = f"{p['team']}|{p['name'].lower()}"
        old = prev.get(str(p["player_id"])) or prev.get(key) or {}
        rows.append(
            {
                "player_id": p["player_id"],
                "name": p["name"],
                "team": p["team"],
                "position": pos,
                "jersey": p["jersey"],
                "status": p["status"],
                "jump": old.get("jump"),
                "route": old.get("route"),
                "arm_strength": old.get("arm_strength"),
                "arm_accuracy": old.get("arm_accuracy"),
                "notes": old.get("notes") or "",
            }
        )
    teams = sorted({(p["team"], p["team_name"]) for p in players})
    write(
        "pregame_of_arms.json",
        {
            "meta": {
                "season": "2026-27 LBPRC rosters",
                "note": "OF jump / route / arm grades. Site auto-saves to GitHub when a PAT is set.",
                "units": "20–80 scout grades",
                "storage_key": "santurce_of_arms_overrides_v1",
                "repo_path": "data/pregame_of_arms.json",
            },
            "teams": [{"abbrev": a, "name": n} for a, n in teams],
            "players": rows,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    print(f"of_arms players={len(rows)}")


def sync_br(players: list[dict]) -> None:
    """Ensure every opposing hitter has a BR/small-ball row (preserve rates when present)."""
    old_rows = load("pregame_baserunning_bunting.json") if (DATA / "pregame_baserunning_bunting.json").exists() else []
    by_id = {int(r["player_id"]): r for r in old_rows if r.get("player_id") is not None}
    by_name = {f"{r.get('team')}|{str(r.get('name') or '').lower()}": r for r in old_rows}
    out = []
    for p in players:
        if p["role"] == "P":
            continue
        if p["team"] == "SAN":
            continue  # small-ball card is opposing-focused; SAN uses strategy card
        pid = p["player_id"]
        key = f"{p['team']}|{p['name'].lower()}"
        old = (by_id.get(int(pid)) if pid is not None and int(pid) in by_id else None) or by_name.get(key) or {}
        row = dict(old)
        row.update(
            {
                "player_id": pid,
                "name": p["name"],
                "team": p["team"],
                "team_name": p["team_name"],
                "position": p["position"],
            }
        )
        out.append(row)
    write("pregame_baserunning_bunting.json", out)
    print(f"br players={len(out)}")


def main() -> int:
    players = roster_players()
    print(f"roster players={len(players)}")
    sync_tth(players)
    sync_of_arms(players)
    sync_br(players)
    # Rebuild strategy/small-ball card cache from the same roster file
    import build_pregame_cards as cards

    cards.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

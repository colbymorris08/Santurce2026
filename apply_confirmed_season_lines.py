#!/usr/bin/env python3
"""Attach confirmed register season lines without a full search crawl."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import research_season_bar_gaps as r

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

CONFIRMED = [
    ("hitter", "SJU", "Emmanuel Garcia", "https://www.baseball-reference.com/register/player.fcgi?id=garcia020man"),
    ("pitcher", "CAG", "Sebastian Rodriguez", "https://www.baseball-reference.com/register/player.fcgi?id=rodrig001seb"),
    ("pitcher", "MAY", "Devin Rivera", "https://www.baseball-reference.com/register/player.fcgi?id=rivera002dev"),
    ("pitcher", "MAY", "Antonio Velez", "https://www.baseball-reference.com/register/player.fcgi?id=velez-001ant"),
    ("pitcher", "MAY", "Nelvin Fuentes", "https://www.baseball-reference.com/register/player.fcgi?id=fuente002nel"),
    ("pitcher", "PON", "Hector Quinones", "https://www.baseball-reference.com/register/player.fcgi?id=quinon002hec"),
    ("pitcher", "SJU", "Alexuan Vega", "https://www.baseball-reference.com/register/player.fcgi?id=vega--000ale"),
]


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def attach(role: str, team: str, name: str, url: str) -> dict | None:
    page, final = r.bref_fetch(url)
    line = (
        r.extended_pitching_choice(page, final)
        if role == "pitcher"
        else r.extended_hitting_choice(page, final)
    )
    if not line:
        return None
    path = DATA / ("advance_opposing_pitchers.json" if role == "pitcher" else "advance_opposing_hitters.json")
    doc = json.loads(path.read_text())
    rows = doc.get("pitchers" if role == "pitcher" else "hitters") or []
    for row in rows:
        if row.get("team") == team and norm(str(row.get("name") or "")) == norm(name):
            row["summer_line"] = line
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
            return line
    if role == "pitcher" and name == "Sebastian Rodriguez":
        rows.append(
            {
                "synergy_id": None,
                "name": "Sebastian Rodriguez",
                "team": "CAG",
                "mlb_id": None,
                "throws": "R",
                "summer_line": line,
                "source": "Season line attached for a roster arm that has no Synergy packet",
            }
        )
        doc["pitchers"] = rows
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        return line
    return None


def main() -> None:
    for role, team, name, url in CONFIRMED:
        line = attach(role, team, name, url)
        print(
            f"{team} {name}: "
            f"{(line or {}).get('season') if line else 'NO LINE'} "
            f"pa={(line or {}).get('pa')} ip={(line or {}).get('ip')}",
            flush=True,
        )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fill strategy-card WH from a player-level Synergy pull.

The opposing advance file skips Santurce, and a team pull of the newest
pitches misses players whose swings are 2026 MLB/MiLB. This looks up each
blank roster player, keeps the newest window that has swings, and writes
data/card_whiff.json. build_pregame_cards.py reads that file on every rebuild.
"""

from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path

import build_advance_synergy as bas
import refresh_roster_synergy as roster

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE_PATH = DATA / "card_whiff.json"
CARD_PATH = DATA / "pregame_card_stats.json"
REPORT_PATH = ROOT / "runs" / "card_whiff_report.json"
MIN_SWINGS = 5

NAME_VARIANTS = {
    "tsung che cheng": ["Tsung-Che Cheng", "Che Cheng"],
    "jatniel mccloud": ["Jatniel McCloud"],
    "brian navaretto": ["Brian Navarreto"],
    "johneswy fargas": ["John Fargas"],
    "keishin saito": ["Keishin Saitoh", "Keishin Saito"],
    "haruse narita": ["Haruse Narita"],
    "kazuto taguchi": ["Kazuto Taguchi"],
    "yuta nakami": ["Yuta Nakami", "Yuta Nakami"],
    "yamvier carrier": ["Yamil Carrier", "Yanvier Carrier", "Jamvier Carrier"],
    "bryan gonzalez garcia": ["Bryan Gonzalez"],
    "keishin saito": ["Keishin Saitoh"],
    "haruse narita": ["Haruse Narita"],
}


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.lower().split())


def _league_name(event: dict) -> str:
    game = event.get("game") or {}
    league = game.get("league") if isinstance(game, dict) else None
    if isinstance(league, dict):
        return str(league.get("name") or league.get("abbreviation") or "other")
    return "other"


def recent_event(event: dict) -> bool:
    """Any Synergy baseball from 2025 or 2026, not only MLB/MiLB/LBPRC.

    Santurce roster players also swing in NPB, the Pioneer League, college,
    and winter ball. Those pitches were being dropped, which left real 2026
    seasons blank.
    """
    return bas.event_season_year(event) in (2025, 2026)


def choose_window(events: list[dict]) -> tuple[list[dict], str]:
    def rows(pred) -> list[dict]:
        return [event for event in events if pred(event)]

    def other_label(year: int, group: list[dict]) -> str:
        names = sorted({_league_name(event) for event in group if _league_name(event) != "other"})
        league = ", ".join(names[:3]) if names else "other"
        return f"{year} {league}"

    windows = (
        (
            "2026 summer",
            rows(lambda event: bas.event_season_year(event) == 2026 and bas.event_league_id(event) in bas.SUMMER_LEAGUE_IDS),
        ),
        (
            "LBPRC",
            rows(lambda event: bas.event_league_id(event) == bas.LEAGUE_LBPRC and bas.event_season_year(event) in (2025, 2026)),
        ),
        (
            "2025 summer",
            rows(lambda event: bas.event_season_year(event) == 2025 and bas.event_league_id(event) in bas.SUMMER_LEAGUE_IDS),
        ),
    )
    extra = []
    for year in (2026, 2025):
        group = rows(lambda event, year=year: bas.event_season_year(event) == year)
        extra.append((other_label(year, group), group))
    windows = windows + tuple(extra)
    counted = [(label, group, swing_count(group)) for label, group in windows]
    for label, group, swings in counted:
        if swings >= MIN_SWINGS:
            return group, label
    label, group, swings = max(counted, key=lambda item: item[2])
    if swings:
        return group, label
    return [], "none"


def swing_count(events: list[dict]) -> int:
    return sum(1 for event in events if bas._is_swing(((event.get("pitch") or {}).get("pitchResult"))))


def rates(events: list[dict], *, hitter: bool) -> dict:
    buckets = {"all": [0, 0], "L": [0, 0], "R": [0, 0]}
    for event in events:
        result = (event.get("pitch") or {}).get("pitchResult")
        if not bas._is_swing(result):
            continue
        whiff = int(bool(bas._is_whiff(result)))
        side = bas._pitcher_throws(event) if hitter else bas._bat_side(event)
        buckets["all"][0] += whiff
        buckets["all"][1] += 1
        if side in ("L", "R"):
            buckets[side][0] += whiff
            buckets[side][1] += 1

    def pct(pair: list[int]) -> float | None:
        whiffs, swings = pair
        if swings < MIN_SWINGS:
            return None
        return round(100.0 * whiffs / swings, 1)

    overall = pct(buckets["all"])
    return {
        "wh": overall,
        "wh_l": pct(buckets["L"]),
        "wh_r": pct(buckets["R"]),
        "swings": buckets["all"][1],
        "swings_l": buckets["L"][1],
        "swings_r": buckets["R"][1],
    }


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {"players": []}


def save_cache(cache: dict) -> None:
    cache["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cache["rule"] = (
        "Synergy whiffs/swings. Newest window with swings: 2026 summer, then LBPRC, then 2025 summer. "
        "No rate is invented."
    )
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n")


def seed_existing(cache: dict, card: dict) -> None:
    by_id = {int(row["id"]): row for row in cache.get("players") or []}
    for player in card.get("players") or []:
        block = player.get("hitter") if player.get("role") == "H" else player.get("pitcher")
        if not block or block.get("wh") is None:
            continue
        if int(player["id"]) in by_id:
            continue
        by_id[int(player["id"])] = {
            "id": int(player["id"]),
            "name": player["name"],
            "team": player["team"],
            "role": player["role"],
            "wh": block.get("wh"),
            "wh_l": block.get("wh_l"),
            "wh_r": block.get("wh_r"),
            "swings": block.get("wh_l_pa") or block.get("wh_r_pa"),
            "swings_l": block.get("wh_l_pa"),
            "swings_r": block.get("wh_r_pa"),
            "window": "already on card",
            "synergy_id": None,
        }
    cache["players"] = list(by_id.values())


def blanks(card: dict) -> list[dict]:
    rows = []
    for player in card.get("players") or []:
        block = player.get("hitter") if player.get("role") == "H" else player.get("pitcher")
        if not block:
            continue
        if block.get("wh") is not None or block.get("wh_l") is not None or block.get("wh_r") is not None:
            continue
        rows.append(player)
    stars = {"carlos correa", "javier baez", "riley greene", "heliot ramos", "neftali soto"}
    rows.sort(key=lambda player: (
        norm(player.get("name") or "") not in stars,
        player.get("team") != "SAN",
        player.get("role") != "H",
        player.get("name") or "",
    ))
    return rows


def accept_row(player: dict, rows: list[dict]) -> dict | None:
    """Match the MLB id when Synergy has one. Otherwise require one exact full name.

    A different person who shares the name and carries a different MLB id is ignored.
    """
    mlb_id = int(player["id"]) if int(player["id"]) > 0 else None
    if mlb_id:
        matched = []
        for row in rows:
            raw = row.get("mlbProfileId") or row.get("ids")
            try:
                if int(raw or 0) == mlb_id:
                    matched.append(row)
            except (TypeError, ValueError):
                continue
        if len(matched) == 1:
            return matched[0]
        rows = [
            row for row in rows
            if not str(row.get("mlbProfileId") or row.get("ids") or "").isdigit()
            or int(row.get("mlbProfileId") or row.get("ids") or 0) == mlb_id
        ]
    exact = [row for row in rows if norm(row.get("name") or "") == norm(player["name"])]
    if len(exact) == 1:
        return exact[0]
    roster_last = (norm(player["name"]).split() or [""])[-1].rstrip("h")
    close = []
    for row in rows:
        got = (norm(row.get("name") or "").split() or [""])[-1].rstrip("h")
        if got and got == roster_last:
            close.append(row)
    return close[0] if len(close) == 1 else None


def search_player(syn: roster.Synergy, player: dict):
    variants = [player["name"]] + NAME_VARIANTS.get(norm(player["name"]), [])
    last_rows: list[dict] = []
    mlb_id = int(player["id"]) if int(player["id"]) > 0 else None
    for variant in variants:
        sid, _row, rows = syn.search({"name": variant, "mlb_id": mlb_id})
        last_rows = rows or last_rows
        chosen = accept_row(player, rows)
        if chosen:
            return str(chosen["id"]), chosen, rows
        if sid and not mlb_id:
            return None, None, rows
    return None, None, last_rows


def patch_card(card: dict, row: dict) -> None:
    for player in card.get("players") or []:
        if int(player["id"]) != int(row["id"]):
            continue
        block = player.get("hitter") if player.get("role") == "H" else player.get("pitcher")
        if not block:
            return
        block["wh"] = row.get("wh")
        block["wh_l"] = row.get("wh_l")
        block["wh_r"] = row.get("wh_r")
        if player.get("role") == "H":
            block["wh_l_pa"] = row.get("swings_l")
            block["wh_r_pa"] = row.get("swings_r")
        return


def main() -> None:
    card = json.loads(CARD_PATH.read_text())
    cache = load_cache()
    seed_existing(cache, card)
    save_cache(cache)
    known = {int(row["id"]) for row in cache["players"] if row.get("wh") is not None}
    targets = [player for player in blanks(card) if int(player["id"]) not in known]
    print(f"targets {len(targets)}", flush=True)
    syn = roster.Synergy()
    report = {"filled": [], "empty": [], "ambiguous": []}
    by_id = {int(row["id"]): row for row in cache["players"]}

    for index, player in enumerate(targets, 1):
        name = player["name"]
        print(f"[{index}/{len(targets)}] {player['team']} {player['role']} {name}", flush=True)
        sid, match, rows = search_player(syn, player)
        entry = {"id": player["id"], "name": name, "team": player["team"], "role": player["role"]}
        if not sid:
            entry["candidates"] = [norm(row.get("name") or "") for row in rows[:8]]
            report["ambiguous"].append(entry)
            print(f"  no unique synergy player ({len(rows)} candidates)", flush=True)
            continue
        events = bas.fetch_events_pages(
            syn.token,
            batter_id=None if player["role"] == "P" else sid,
            pitcher_id=sid if player["role"] == "P" else None,
            max_events=3000,
            take=200,
            pause_s=0.03,
            timeout_s=45,
            max_attempts=2,
            stop_before_year=2025,
            retain=recent_event,
            max_scan=12000,
        )
        selected, window = choose_window(events)
        result = rates(selected, hitter=player["role"] == "H")
        entry.update(result)
        entry["window"] = window
        entry["synergy_id"] = sid
        entry["events"] = len(selected)
        if result.get("wh") is None:
            report["empty"].append(entry)
            print(f"  no swings in 2025-2026 ({window})", flush=True)
            continue
        saved = {
            "id": int(player["id"]),
            "name": name,
            "team": player["team"],
            "role": player["role"],
            "wh": result["wh"],
            "wh_l": result["wh_l"],
            "wh_r": result["wh_r"],
            "swings": result["swings"],
            "swings_l": result["swings_l"],
            "swings_r": result["swings_r"],
            "window": window,
            "synergy_id": sid,
        }
        by_id[int(player["id"])] = saved
        cache["players"] = list(by_id.values())
        save_cache(cache)
        patch_card(card, saved)
        CARD_PATH.write_text(json.dumps(card, ensure_ascii=False, separators=(",", ":")))
        report["filled"].append(entry)
        print(
            f"  {window} wh {result['wh']} L {result['wh_l']} R {result['wh_r']} swings {result['swings']}",
            flush=True,
        )
        REPORT_PATH.parent.mkdir(exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")

    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"filled {len(report['filled'])} empty {len(report['empty'])} ambiguous {len(report['ambiguous'])}",
        flush=True,
    )


JAPAN_NAMES = {"neftali soto", "keishin saito", "haruse narita", "kazuto taguchi", "yuta nakami"}


def npb_window(events: list[dict]) -> tuple[list[dict], str] | None:
    group = [
        event for event in events
        if bas.event_season_year(event) == 2026 and "npb" in _league_name(event).lower()
    ]
    if swing_count(group) >= MIN_SWINGS:
        return group, "2026 NPB"
    return None


def refresh_full(targets: list[dict]) -> None:
    """Recompute WH from every 2025-2026 pitch, using 2026 NPB when that is the season."""
    card = json.loads(CARD_PATH.read_text())
    cache = load_cache()
    by_id = {int(row["id"]): row for row in cache.get("players") or []}
    syn = roster.Synergy()
    for index, player in enumerate(targets, 1):
        print(f"[{index}/{len(targets)}] {player['team']} {player['role']} {player['name']}", flush=True)
        sid, _match, rows = search_player(syn, player)
        if not sid:
            print(f"  no unique synergy player ({len(rows)} candidates)", flush=True)
            continue
        events = bas.fetch_events_pages(
            syn.token,
            batter_id=None if player["role"] == "P" else sid,
            pitcher_id=sid if player["role"] == "P" else None,
            max_events=8000,
            take=200,
            pause_s=0.02,
            timeout_s=45,
            max_attempts=2,
            stop_before_year=2025,
            retain=recent_event,
            max_scan=20000,
        )
        japan = npb_window(events) if norm(player["name"]) in JAPAN_NAMES else None
        if japan:
            selected, window = japan
        else:
            selected, window = choose_window(events)
        result = rates(selected, hitter=player["role"] == "H")
        if result.get("wh") is None:
            print(f"  no swings ({window})", flush=True)
            continue
        saved = {
            "id": int(player["id"]),
            "name": player["name"],
            "team": player["team"],
            "role": player["role"],
            "wh": result["wh"],
            "wh_l": result["wh_l"],
            "wh_r": result["wh_r"],
            "swings": result["swings"],
            "swings_l": result["swings_l"],
            "swings_r": result["swings_r"],
            "window": window,
            "synergy_id": sid,
        }
        by_id[int(player["id"])] = saved
        cache["players"] = list(by_id.values())
        save_cache(cache)
        patch_card(card, saved)
        CARD_PATH.write_text(json.dumps(card, ensure_ascii=False, separators=(",", ":")))
        print(
            f"  {window} wh {result['wh']} L {result['wh_l']} R {result['wh_r']} swings {result['swings']}",
            flush=True,
        )


if __name__ == "__main__":
    import sys
    if "--full-league" in sys.argv:
        card = json.loads(CARD_PATH.read_text())
        japan = [player for player in card["players"] if norm(player["name"]) in JAPAN_NAMES]
        santurce = [player for player in card["players"] if player.get("team") == "SAN"]
        seen = set()
        ordered = []
        for player in japan + santurce:
            if player["id"] in seen:
                continue
            seen.add(player["id"])
            ordered.append(player)
        refresh_full(ordered)
    else:
        main()

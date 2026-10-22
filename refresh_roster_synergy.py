#!/usr/bin/env python3
"""Fill missing opposing-roster scouting records from Synergy, one player at a time.

The pull is deliberately paced to at most 30 roster players per minute and writes
all site JSON plus a run checkpoint after every player.
"""

from __future__ import annotations

import json
import time
import urllib.error
from collections import defaultdict
from pathlib import Path
from typing import Any

import build_advance_synergy as bas

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
CHECKPOINT = RUNS / "roster_synergy_checkpoint.json"
REPORT = DATA / "roster_2026_27_synergy_audit.json"
MIN_PLAYER_SECONDS = 2.05
MAX_EVENTS = 1500

NAME_VARIANTS = {
    "keishin saito": ["Keishin Saitoh"],
    "freddy cabrera": ["Freddie Cabrera", "Fred Cabrera", "Frederick Cabrera"],
}


def norm(value: str) -> str:
    return bas._norm_person(value)


def row_key(row: dict) -> tuple[str, str]:
    return str(row.get("team") or ""), norm(row.get("name") or "")


def load(name: str) -> Any:
    return json.loads((DATA / name).read_text())


def atomic_write(path: Path, value: Any, *, compact: bool = False) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    if compact:
        tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    else:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def roster_players() -> tuple[list[dict], list[dict]]:
    hitters, pitchers = [], []
    for team in load("lbprc_2025_rosters.json").values():
        abbr = str(team.get("teamAbbrev") or "")
        # Santurce is not an opposing advance roster. Its strategy-card whiff
        # is stored in data/card_whiff.json by fill_card_whiff.py.
        if abbr not in bas.OPP_TEAMS:
            continue
        for player in team.get("players") or []:
            row = {
                "name": player.get("name"),
                "team": abbr,
                "position": player.get("position"),
                "mlb_id": int(player["id"]) if int(player.get("id") or 0) > 0 else None,
                "player_id": int(player.get("id") or 0),
            }
            (pitchers if player.get("position") == "P" else hitters).append(row)
    return hitters, pitchers


class Synergy:
    def __init__(self) -> None:
        self.token = bas.login_token()

    def relogin(self) -> None:
        token_path = Path("/tmp/synergy_token.txt")
        if token_path.exists():
            token_path.unlink()
        self.token = bas.login_token()

    def get(self, path: str, params: dict) -> Any:
        for attempt in range(3):
            try:
                url = f"{bas.SPORT_API}{path}"
                if params:
                    import urllib.parse
                    import urllib.request

                    url += "?" + urllib.parse.urlencode(params, doseq=True)
                    req = urllib.request.Request(
                        url,
                        headers={
                            "Authorization": f"Bearer {self.token}",
                            "Accept": "application/json",
                            "User-Agent": "SanturceAdvance/1.0",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=20) as response:
                        return json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt < 2:
                    print("  search HTTP 401 — re-login")
                    self.relogin()
                    continue
                raise
            except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
                if attempt == 2:
                    raise
                print(f"  search retry {attempt + 1}/2: {exc}")
                time.sleep(0.5)
        return {}

    def search(self, player: dict) -> tuple[str | None, dict | None, list[dict]]:
        names = [str(player["name"])] + NAME_VARIANTS.get(norm(player["name"]), [])
        candidates: dict[str, dict] = {}
        for full_name in names:
            parts = full_name.split()
            if not parts:
                continue
            doc = self.get(
                "/api/players/search",
                {
                    "firstName": parts[0],
                    "lastName": parts[-1],
                    "sportId": bas.SPORT_ID_BASEBALL,
                },
            )
            for row in (doc.get("result") or []) if isinstance(doc, dict) else []:
                if row.get("id"):
                    candidates[str(row["id"])] = row
            if candidates:
                break
        rows = list(candidates.values())
        mlb_id = player.get("mlb_id")
        if mlb_id:
            for row in rows:
                raw = row.get("mlbProfileId") or row.get("ids")
                try:
                    if int(raw or 0) == int(mlb_id):
                        return str(row["id"]), row, rows
                except (TypeError, ValueError):
                    pass
        exact_names = {norm(x) for x in names}
        exact = [row for row in rows if norm(row.get("name") or "") in exact_names]
        if len(exact) == 1:
            return str(exact[0]["id"]), exact[0], rows
        return None, None, rows

    def events(self, *, sid: str, pitcher: bool) -> list[dict]:
        kwargs = {"pitcher_id": sid} if pitcher else {"batter_id": sid}
        return bas.fetch_events_pages(
            self.token,
            **kwargs,
            max_events=MAX_EVENTS,
            take=200,
            pause_s=0.04,
            timeout_s=20,
            max_attempts=3,
            stop_before_year=2024,
            retain=bas.event_in_heat_fallback_scan,
            max_scan=8000,
        )


def select_stats_window(events: list[dict]) -> tuple[list[dict], str]:
    windows = (
        (bas.HEAT_PRIMARY_WINDOW, [e for e in events if bas.event_in_heat_window(e)]),
        (
            bas.HEAT_2025_FALLBACK,
            [
                e
                for e in events
                if bas.event_season_year(e) == 2025
                and bas.event_league_id(e) in (bas.SUMMER_LEAGUE_IDS | bas.WINTER_LEAGUE_IDS)
            ],
        ),
        (bas.HEAT_2024_FALLBACK, [e for e in events if bas.event_season_year(e) == 2024]),
    )
    for label, rows in windows:
        if rows:
            return rows, label
    return [], "none"


def has_hitter_stats(row: dict) -> bool:
    return bool(
        int((row.get("overall") or {}).get("pitches") or 0)
        and row.get("vs_r_by_pitch_type")
        and row.get("vs_l_by_pitch_type")
    )


def has_heat(row: dict | None) -> bool:
    return bas._heat_has_map(row)


def arsenal_flags(row: dict) -> dict[str, bool]:
    pitches = row.get("arsenal") or []
    return {
        "types": bool(pitches),
        "velocity": bool(pitches) and all(p.get("velo") is not None for p in pitches),
        "movement": any(
            p.get("ivb") is not None
            and (p.get("hb_hitter") is not None or p.get("hb") is not None)
            for p in pitches
        ),
        "whiff": bool(pitches) and all(p.get("whiff") is not None for p in pitches),
    }


def merge_hitter(old: dict, player: dict, sid: str, events: list[dict], window: str) -> dict:
    raw = bas.aggregate_events(events, player["team"])
    if sid not in raw and raw:
        raw = {sid: next(iter(raw.values()))}
    rows = bas.serialize_hitters(raw, bas.load_season_games(), bas.load_mlb_id_map())
    if not rows:
        return old
    new = rows[0]
    keep = {
        key: old.get(key)
        for key in ("bats", "summer_line", "summer_sb", "summer_cs", "summer_bunts", "summer_games", "summer_sb_source")
        if old.get(key) is not None
    }
    new.update(keep)
    new.update(
        {
            "name": player["name"],
            "team": player["team"],
            "mlb_id": player.get("mlb_id"),
            "synergy_id": sid,
            "window_used": window,
        }
    )
    bas.strip_hitter_chart_junk_pitches([new])
    return new


def aggregate_synergy_arsenal(events: list[dict]) -> tuple[list[dict], str | None]:
    by: dict[str, dict] = defaultdict(lambda: {"pitches": 0, "speeds": [], "swings": 0, "whiffs": 0})
    throws = None
    for event in events:
        pitch = event.get("pitch") or {}
        code = bas._pitch_code(pitch.get("pitchKind"))
        if not code or code in bas._CHART_SKIP_CODES:
            continue
        rec = by[code]
        rec["pitches"] += 1
        try:
            rec["speeds"].append(float(pitch["pitchSpeedMph"]))
        except (KeyError, TypeError, ValueError):
            pass
        result = pitch.get("pitchResult")
        rec["swings"] += int(bas._is_swing(result))
        rec["whiffs"] += int(bas._is_whiff(result))
        throws = throws or bas._pitcher_throws(event)
    total = sum(row["pitches"] for row in by.values())
    out = []
    for code, rec in by.items():
        out.append(
            {
                "type": code,
                "name": code,
                "usage": round(100 * rec["pitches"] / total, 1) if total else None,
                "pitches": rec["pitches"],
                "velo": round(sum(rec["speeds"]) / len(rec["speeds"]), 1) if rec["speeds"] else None,
                "spin": None,
                "ivb": None,
                "hb_pitcher": None,
                "hb_hitter": None,
                "ps_stuff": None,
                "whiff": round(100 * rec["whiffs"] / rec["swings"], 1) if rec["swings"] else None,
                "xwoba": None,
            }
        )
    out.sort(key=lambda row: -(row["pitches"] or 0))
    return out, throws


def merge_arsenal(old: dict, mix: dict, events: list[dict]) -> dict:
    synergy_rows, throws = aggregate_synergy_arsenal(events)
    old_by = {bas._pitch_code(row.get("type") or row.get("code")): dict(row) for row in old.get("arsenal") or []}
    merged = []
    for syn in synergy_rows:
        row = old_by.pop(syn["type"], {})
        for key, value in syn.items():
            if key in ("usage", "pitches") or row.get(key) is None:
                row[key] = value
        merged.append(row)
    merged.extend(old_by.values())
    merged.sort(key=lambda row: -(row.get("pitches") or 0))
    old["arsenal"] = merged
    old["throws"] = old.get("throws") or throws
    old["pitches"] = mix.get("pitches")
    old["synergy_id"] = mix.get("synergy_id")
    old["synergy_usage_source"] = mix.get("source")
    return old


def empty_heat(name: str, team: str, sid: str, player_id: int | None, pitcher: bool) -> dict:
    row = bas._heat_blank()
    row.update(
        {
            "name": name,
            "team": team,
            "window": "MLB/MiLB/LMB 2026 + LBPRC 2025",
            "window_used": "none",
            "raw": 0,
            "kept": 0,
            "located": 0,
            "complete": True,
            "fallback_checked": True,
            "fallback_version": bas.HEAT_FALLBACK_VERSION,
            "sources": {},
            "empty_reason": "No Synergy events in the 2026+2025, 2025, or 2024 fallback windows",
        }
    )
    if pitcher:
        row.update({"synergy_id": sid, "mlb_id": player_id})
    return row


def make_heat(player: dict, sid: str, raw: list[dict], pitcher: bool) -> dict:
    kept, window = bas.select_heat_window(raw)
    heat = bas.aggregate_location_heat(kept, split="batter_hand" if pitcher else "pitcher_hand")
    heat.update(
        {
            "name": player["name"],
            "team": player["team"],
            "window": "MLB/MiLB/LMB 2026 + LBPRC 2025",
            "window_used": window,
            "raw": len(raw),
            "kept": len(kept),
            "complete": True,
            "fallback_checked": True,
            "fallback_version": bas.HEAT_FALLBACK_VERSION,
            "sources": bas._located_sources(kept),
        }
    )
    if pitcher:
        heat.update({"synergy_id": sid, "mlb_id": player.get("mlb_id")})
    if not heat.get("located"):
        heat["empty_reason"] = "No located pitches in eligible 2026+2025, 2025, or 2024 Synergy windows"
    return heat


def main() -> None:
    RUNS.mkdir(exist_ok=True)
    hitters, pitchers = roster_players()
    hitter_doc = load("advance_opposing_hitters.json")
    pitcher_doc = load("advance_opposing_pitchers.json")
    arsenal_doc = load("pregame_pitcher_arsenals.json")
    heat_doc = load("advance_heatmaps.json")
    hitter_by = {row_key(row): row for row in hitter_doc.get("hitters") or []}
    pitcher_by = {row_key(row): row for row in pitcher_doc.get("pitchers") or []}
    arsenal_by = {row_key(row): row for row in arsenal_doc.get("pitchers") or []}
    h_heat = dict(heat_doc.get("hitters") or {})
    p_heat = dict(heat_doc.get("pitchers") or {})
    state = {"completed": {}, "results": []}
    if CHECKPOINT.exists():
        try:
            state = json.loads(CHECKPOINT.read_text())
        except json.JSONDecodeError:
            pass
    client = Synergy()

    def persist() -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        hitter_doc["hitters"] = sorted(hitter_by.values(), key=lambda row: (row.get("team") or "", row.get("name") or ""))
        hitter_doc["select_all"] = bas.select_all_aggregate(hitter_doc["hitters"])
        hitter_doc["updated"] = now
        pitcher_doc["pitchers"] = sorted(pitcher_by.values(), key=lambda row: (row.get("team") or "", row.get("name") or ""))
        pitcher_doc["updated"] = now
        arsenal_doc["pitchers"] = sorted(arsenal_by.values(), key=lambda row: (row.get("team") or "", row.get("name") or ""))
        arsenal_doc["updated"] = now
        heat_doc["hitters"], heat_doc["pitchers"], heat_doc["updated"] = h_heat, p_heat, now
        heat_doc["coverage"] = {
            "hitters": len(hitters),
            "hitters_with_maps": sum(has_heat(h_heat.get(str(row.get("synergy_id") or ""))) for row in hitter_by.values()),
            "pitchers": len(pitchers),
            "pitchers_with_maps": sum(
                has_heat(p_heat.get(str(row.get("mlb_id") or row.get("synergy_id") or "")))
                for row in pitcher_by.values()
            ),
            "located_by_source": bas._source_rollup(list(h_heat.values()) + list(p_heat.values())),
        }
        atomic_write(DATA / "advance_opposing_hitters.json", hitter_doc)
        atomic_write(DATA / "advance_opposing_pitchers.json", pitcher_doc)
        atomic_write(DATA / "pregame_pitcher_arsenals.json", arsenal_doc)
        atomic_write(DATA / "advance_heatmaps.json", heat_doc, compact=True)
        atomic_write(CHECKPOINT, state)

    jobs = [("hitter", row) for row in hitters] + [("pitcher", row) for row in pitchers]
    jobs.sort(key=lambda item: (item[1]["team"], item[0], item[1]["name"]))
    for index, (kind, player) in enumerate(jobs, 1):
        key = f"{kind}:{player['team']}:{norm(player['name'])}"
        if key in state.get("completed", {}):
            continue
        started = time.monotonic()
        player_key = row_key(player)
        old = (hitter_by if kind == "hitter" else pitcher_by).get(player_key) or {}
        sid = str(old.get("synergy_id") or "")
        if kind == "hitter":
            complete_enough = has_hitter_stats(old) and sid and has_heat(h_heat.get(sid))
        else:
            heat_key = str(old.get("mlb_id") or player.get("mlb_id") or sid)
            flags = arsenal_flags(arsenal_by.get(player_key) or {})
            complete_enough = bool(
                old.get("pitches")
                and sid
                and flags["types"]
                and flags["velocity"]
                and flags["whiff"]
                and has_heat(p_heat.get(heat_key))
            )
        if complete_enough:
            result = {**player, "kind": kind, "status": "preserved", "synergy_id": sid}
            state.setdefault("completed", {})[key] = result
            state.setdefault("results", []).append(result)
            persist()
            print(f"[{index}/{len(jobs)}] {kind} {player['team']} {player['name']} — preserve complete")
            continue
        search_rows: list[dict] = []
        matched = None
        try:
            if not sid:
                sid, matched, search_rows = client.search(player)
            if not sid:
                result = {
                    **player,
                    "kind": kind,
                    "status": "unresolved",
                    "search_candidates": [row.get("name") for row in search_rows],
                }
                print(f"[{index}/{len(jobs)}] {kind} {player['team']} {player['name']} — no Synergy id")
            else:
                raw = client.events(sid=sid, pitcher=kind == "pitcher")
                stats_events, window = select_stats_window(raw)
                if kind == "hitter":
                    if stats_events:
                        hitter_by[player_key] = merge_hitter(old, player, sid, stats_events, window)
                    else:
                        old.update({"name": player["name"], "team": player["team"], "synergy_id": sid})
                        hitter_by[player_key] = old
                    h_heat[sid] = make_heat(player, sid, raw, False) if raw else empty_heat(
                        player["name"], player["team"], sid, player.get("mlb_id"), False
                    )
                else:
                    packed = bas.aggregate_pitcher_count_mixes(stats_events)
                    mix = packed.get(sid) or (next(iter(packed.values())) if packed else None)
                    if mix:
                        mix.update(
                            {
                                "name": player["name"],
                                "team": player["team"],
                                "mlb_id": player.get("mlb_id"),
                                "synergy_id": sid,
                                "window_used": window,
                            }
                        )
                        if old.get("summer_line"):
                            mix["summer_line"] = old["summer_line"]
                        pitcher_by[player_key] = mix
                        arsenal = arsenal_by.get(player_key) or {
                            "name": player["name"],
                            "team": player["team"],
                            "player_id": player["player_id"],
                            "arsenal": [],
                            "view": "hitter",
                        }
                        arsenal_by[player_key] = merge_arsenal(arsenal, mix, stats_events)
                    else:
                        old.update({"name": player["name"], "team": player["team"], "synergy_id": sid})
                        pitcher_by[player_key] = old
                    heat_key = str(player.get("mlb_id") or sid)
                    p_heat[heat_key] = make_heat(player, sid, raw, True) if raw else empty_heat(
                        player["name"], player["team"], sid, player.get("mlb_id"), True
                    )
                result = {
                    **player,
                    "kind": kind,
                    "status": "populated" if stats_events else "empty",
                    "synergy_id": sid,
                    "synergy_name": (matched or {}).get("name"),
                    "raw_events": len(raw),
                    "stats_events": len(stats_events),
                    "window_used": window,
                }
                print(
                    f"[{index}/{len(jobs)}] {kind} {player['team']} {player['name']} "
                    f"— {result['status']} raw={len(raw)} kept={len(stats_events)} {window}"
                )
        except Exception as exc:  # noqa: BLE001
            result = {**player, "kind": kind, "status": "error", "error": repr(exc)}
            print(f"[{index}/{len(jobs)}] {kind} {player['team']} {player['name']} — ERROR {exc!r}")
        state.setdefault("completed", {})[key] = result
        state.setdefault("results", []).append(result)
        persist()
        remaining = MIN_PLAYER_SECONDS - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)

    # Fill missing 2026 bars without deleting already-good lines.
    missing_h = [row for row in hitter_by.values() if row.get("mlb_id") and not row.get("summer_line")]
    if missing_h:
        lines = bas.fetch_2026_summer_lines([row["mlb_id"] for row in missing_h])
        for row in missing_h:
            if int(row["mlb_id"]) in lines:
                row["summer_line"] = lines[int(row["mlb_id"])]
    missing_p = [row for row in pitcher_by.values() if row.get("mlb_id") and not row.get("summer_line")]
    if missing_p:
        lines = bas.fetch_2026_pitcher_summer_lines([row["mlb_id"] for row in missing_p])
        for row in missing_p:
            if int(row["mlb_id"]) in lines:
                row["summer_line"] = lines[int(row["mlb_id"])]
    persist()
    bas.merge_synergy_count_into_arsenals(
        {
            str(row["synergy_id"]): row
            for row in pitcher_by.values()
            if row.get("synergy_id") and row.get("pitches")
        }
    )
    bas.merge_spray_into_pregame(list(hitter_by.values()))

    five = {"yuta nakami", "keishin saito", "haruse narita", "mario zabala", "freddy cabrera"}
    report = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rate_limit": "30 roster players/minute",
        "players": state.get("results") or [],
        "five_additions": [
            row for row in (state.get("results") or []) if norm(row.get("name") or "") in five
        ],
    }
    atomic_write(REPORT, report)
    print(f"DONE checkpoint={CHECKPOINT} report={REPORT}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Keep the newest spray balls that actually have a pitcher hand.

The charts were the newest balls in play. A lot of those pitches have no
pitcherInfo.pitchingSide, so vs RHP and vs LHP drew the same dots. This
walks Synergy newest-first and keeps a ball only when the hand is L or R.
If the latest season has none, it keeps going through the prior year
(Puerto Rico included) until it has 100 or the log runs out.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path

import build_advance_synergy as bas

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
CKPT = RUNS / "spray_pitcher_hand_checkpoint.json"
SPRAY_PATH = DATA / "pregame_spray_charts.json"
HITTERS_PATH = DATA / "advance_opposing_hitters.json"
WANT = bas.SPRAY_MAX_POINTS
SCAN_CAP = 8000


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def load_json(path: Path):
    return json.loads(path.read_text())


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def handed_count(pts: list) -> int:
    return sum(1 for p in pts or [] if p.get("pitcher_hand") in ("L", "R"))


def needs_refill(pts: list) -> bool:
    pts = pts or []
    n = handed_count(pts)
    if n < len(pts):
        return True
    return n < WANT


def is_handed_bip(ev: dict) -> bool:
    pitch = ev.get("pitch") or {}
    contact = ev.get("contact") or {}
    if pitch.get("pitchResult") not in bas.BIP_RESULTS:
        return False
    if contact.get("landingLocationX") is None or contact.get("landingLocationY") is None:
        return False
    return bas._pitcher_throws(ev) in ("L", "R")


def fetch_handed_events(token: str, sid: str) -> tuple[list[dict], str]:
    """Newest balls in play whose pitcher hand is known. Returns (events, token)."""
    kept: list[dict] = []
    skip = 0
    scanned = 0
    while len(kept) < WANT and scanned < SCAN_CAP:
        body = {
            "loggingPhases": [bas.LOGGING_PHASE_PITCH],
            "skip": skip,
            "take": 200,
            "batterId": sid,
        }
        try:
            doc = bas.api_post(bas.EVENTS_FILTER_URL, token, body, timeout_s=90)
        except Exception as exc:  # noqa: BLE001
            if "401" in str(exc):
                token = bas.login_token()
                doc = bas.api_post(bas.EVENTS_FILTER_URL, token, body, timeout_s=90)
            else:
                raise
        rows = doc.get("result") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        years = [bas.event_season_year(ev) for ev in rows]
        for ev in rows:
            if is_handed_bip(ev):
                kept.append(ev)
                if len(kept) >= WANT:
                    break
        scanned += len(rows)
        skip += len(rows)
        max_year = max(years) if years else 0
        print(f"  fetched {len(kept)} handed BIP / {scanned} scanned (page max {max_year})", flush=True)
        # Newest-first. A page entirely before 2024 means last year's Puerto
        # Rico season is already behind us.
        if max_year < 2024:
            break
        total = int(doc.get("totalRecords") or 0)
        if skip >= total or len(rows) < 200:
            break
        time.sleep(0.05)
    return kept[:WANT], token


def points_from_events(events: list[dict], sid: str, team: str) -> list[dict]:
    if not events:
        return []
    raw = bas.aggregate_events(events, team or "")
    h = raw.get(sid)
    if h is None and raw:
        h = next(iter(raw.values()))
    if not h:
        return []
    pts = list((h.get("spray") or {}).get("all") or [])
    return bas.recent_spray_points(pts, WANT)


def splits_from_points(pts: list[dict]) -> list[dict]:
    def pack(name: str, pred) -> dict:
        sub = [p for p in pts if pred(p)]
        return {"split": name, "n": len(sub), "points": sub}

    return [
        pack("all", lambda p: True),
        pack("nobody_on", lambda p: bool(p.get("empty_bases"))),
        pack("risp", lambda p: bool(p.get("risp"))),
        pack("non_risp", lambda p: not p.get("risp")),
        pack("two_strikes", lambda p: bool(p.get("two_strikes"))),
        pack("non_two_strikes", lambda p: not p.get("two_strikes")),
    ]


def targets() -> list[dict]:
    rows = load_json(SPRAY_PATH).get("synergy") or []
    out = []
    for row in rows:
        pts = row.get("points") or []
        if not needs_refill(pts):
            continue
        out.append({
            "synergy_id": str(row.get("synergy_id") or ""),
            "name": row.get("name") or "",
            "team": row.get("team") or "",
            "n_points": len(pts),
            "n_handed": handed_count(pts),
        })
    return [r for r in out if r["synergy_id"]]


def apply_checkpoint() -> None:
    ckpt = load_json(CKPT) if CKPT.is_file() else {"done": {}}
    done = ckpt.get("done") or {}
    ready = {
        sid: info
        for sid, info in done.items()
        if info.get("status") in ("ok", "strip") and (info.get("status") == "strip" or info.get("points"))
    }
    if not ready:
        log("nothing to write")
        return
    spray = load_json(SPRAY_PATH)
    hitters = load_json(HITTERS_PATH)
    written = 0
    for row in spray.get("synergy") or []:
        info = ready.get(str(row.get("synergy_id") or ""))
        if not info:
            continue
        if info.get("status") == "strip":
            pts = [p for p in (row.get("points") or []) if p.get("pitcher_hand") in ("L", "R")]
            pts = bas.recent_spray_points(pts, WANT)
        else:
            pts = info["points"]
        if not pts:
            continue
        row["points"] = pts
        row["splits"] = splits_from_points(pts)
        row["source"] = "Synergy BIP with pitcher hand (newest 100)"
        info = {**info, "points": pts}
        ready[str(row.get("synergy_id") or "")] = info
        written += 1
    by_id = {str(h.get("synergy_id") or ""): h for h in (hitters.get("hitters") or [])}
    for sid, info in ready.items():
        h = by_id.get(sid)
        pts = info.get("points") or []
        if not h or not pts:
            continue
        h["spray_points"] = pts
        h["spray_splits"] = splits_from_points(pts)
    spray["synergy_note"] = (
        "Synergy spray uses landingLocationX/Y in feet (home→CF = +Y, toward RF = +X). "
        f"Each chart uses the most recent {WANT} balls in play that have a pitcher hand "
        "(pitcherInfo.pitchingSide). Newer balls with no hand are skipped, so a season "
        "that omitted hand falls back through the prior year, including Puerto Rico."
    )
    spray["spray_recent_cap"] = WANT
    spray["updated_synergy"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_json(SPRAY_PATH, spray)
    save_json(HITTERS_PATH, hitters)
    log(f"wrote {written} hitters into spray + advance")


def main() -> int:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(key, None)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/Users/colbymorris/apexstats/pitch-tips/.pw-browsers")
    os.environ["NO_PROXY"] = "*"
    rows = targets()
    ckpt = load_json(CKPT) if CKPT.is_file() else {"done": {}}
    done = ckpt.setdefault("done", {})
    queued = [
        r for r in rows
        if (done.get(r["synergy_id"]) or {}).get("status") not in ("ok", "empty", "kept", "strip")
    ]
    log(f"queued={len(queued)} checkpointed={len(rows) - len(queued)} need_refill={len(rows)}")
    token = bas.login_token()
    for i, player in enumerate(queued, 1):
        sid = player["synergy_id"]
        log(f"[{i}/{len(queued)}] {player['team']} {player['name']} saved {player['n_handed']}/{player['n_points']} handed")
        try:
            events, token = fetch_handed_events(token, sid)
            pts = points_from_events(events, sid, player["team"])
        except Exception as exc:  # noqa: BLE001
            log(f"  fail {exc}")
            if "401" in str(exc):
                token = bas.login_token()
            continue
        hands = Counter(p.get("pitcher_hand") for p in pts)
        years = Counter((p.get("game_date_sort") or "")[:4] for p in pts)
        if not pts:
            done[sid] = {"status": "empty", "name": player["name"], "team": player["team"]}
            save_json(CKPT, ckpt)
            log("  no handed balls in play")
            continue
        # Don't shrink a chart that already has more handed balls than this pull.
        if player["n_handed"] > len(pts):
            done[sid] = {
                "status": "kept" if player["n_handed"] == player["n_points"] else "strip",
                "name": player["name"],
                "team": player["team"],
                "n": player["n_handed"],
            }
            save_json(CKPT, ckpt)
            log(f"  kept existing {player['n_handed']} handed (refetch {len(pts)})")
            continue
        done[sid] = {
            "status": "ok",
            "name": player["name"],
            "team": player["team"],
            "n": len(pts),
            "L": hands.get("L", 0),
            "R": hands.get("R", 0),
            "years": dict(years),
            "points": pts,
        }
        save_json(CKPT, ckpt)
        log(f"  kept {len(pts)} L={hands.get('L', 0)} R={hands.get('R', 0)} years={dict(years)}")
    apply_checkpoint()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

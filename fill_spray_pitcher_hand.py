#!/usr/bin/env python3
"""Keep the newest spray balls for each chart, only when pitcher hand is known.

Eight charts: vs RHP and vs LHP, each with 2K, non-2K, RISP, and non-RISP.
Walk Synergy newest-first and keep a ball when it is one of the newest 50
for a chart it matches. A 2K ball with a runner on second lands on both the
2K chart and the RISP chart. Balls with no pitcher hand are skipped. Stop
when every chart has 50, or the log runs out through 2025 Puerto Rico
(a page entirely before 2024).
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
CKPT = RUNS / "spray_per_chart50_checkpoint.json"
SPRAY_PATH = DATA / "pregame_spray_charts.json"
HITTERS_PATH = DATA / "advance_opposing_hitters.json"
PER_CHART = bas.SPRAY_MAX_POINTS
SCAN_CAP = 30000
CHARTS = (
    "R-2k",
    "R-non2k",
    "R-risp",
    "R-nonrisp",
    "L-2k",
    "L-non2k",
    "L-risp",
    "L-nonrisp",
)


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def load_json(path: Path):
    return json.loads(path.read_text())


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def chart_keys_for_event(ev: dict) -> list[str]:
    hand = bas._pitcher_throws(ev)
    if hand not in ("L", "R"):
        return []
    two_k = bas._count_strikes(ev) >= 2
    risp = bool(bas._runners_risp(ev))
    return [
        f"{hand}-{'2k' if two_k else 'non2k'}",
        f"{hand}-{'risp' if risp else 'nonrisp'}",
    ]


def is_handed_bip(ev: dict) -> bool:
    pitch = ev.get("pitch") or {}
    contact = ev.get("contact") or {}
    if pitch.get("pitchResult") not in bas.BIP_RESULTS:
        return False
    if contact.get("landingLocationX") is None or contact.get("landingLocationY") is None:
        return False
    return bas._pitcher_throws(ev) in ("L", "R")


def charts_full(counts: dict) -> bool:
    return all(counts.get(k, 0) >= PER_CHART for k in CHARTS)


def fetch_chart_events(token: str, sid: str) -> tuple[list[dict], dict, str]:
    """Newest BIP that fill each chart up to PER_CHART. Returns (events, counts, token)."""
    kept: list[dict] = []
    counts = {k: 0 for k in CHARTS}
    skip = 0
    scanned = 0
    while not charts_full(counts) and scanned < SCAN_CAP:
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
            if not is_handed_bip(ev):
                continue
            keys = [k for k in chart_keys_for_event(ev) if counts.get(k, 0) < PER_CHART]
            if not keys:
                continue
            for k in keys:
                counts[k] += 1
            kept.append(ev)
            if charts_full(counts):
                break
        scanned += len(rows)
        skip += len(rows)
        max_year = max(years) if years else 0
        short = {k: counts[k] for k in CHARTS if counts[k] < PER_CHART}
        print(
            f"  kept {len(kept)} events / {scanned} scanned (page max {max_year}) short={short or 'none'}",
            flush=True,
        )
        # Newest-first. A page entirely before 2024 means 2025 Puerto Rico is behind us.
        if max_year < 2024:
            break
        total = int(doc.get("totalRecords") or 0)
        if skip >= total or len(rows) < 200:
            break
        time.sleep(0.05)
    return kept, counts, token


def points_from_events(events: list[dict], sid: str, team: str) -> list[dict]:
    if not events:
        return []
    raw = bas.aggregate_events(events, team or "")
    h = raw.get(sid)
    if h is None and raw:
        h = next(iter(raw.values()))
    if not h:
        return []
    pts = []
    for p in (h.get("spray") or {}).get("all") or []:
        if p.get("pitcher_hand") not in ("L", "R"):
            continue
        if p.get("x_ft") is None or p.get("y_ft") is None:
            continue
        pts.append(p)
    return pts


def point_chart_counts(pts: list[dict]) -> dict:
    counts = {k: 0 for k in CHARTS}
    for p in pts:
        hand = p.get("pitcher_hand")
        if hand not in ("L", "R"):
            continue
        keys = [
            f"{hand}-{'2k' if p.get('two_strikes') else 'non2k'}",
            f"{hand}-{'risp' if p.get('risp') else 'nonrisp'}",
        ]
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
    return {k: min(PER_CHART, counts.get(k, 0)) for k in CHARTS}


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
        sid = str(row.get("synergy_id") or "")
        if not sid:
            continue
        out.append({
            "synergy_id": sid,
            "name": row.get("name") or "",
            "team": row.get("team") or "",
        })
    return out


def apply_checkpoint() -> None:
    ckpt = load_json(CKPT) if CKPT.is_file() else {"done": {}}
    done = ckpt.get("done") or {}
    ready = {
        sid: info
        for sid, info in done.items()
        if info.get("status") == "ok" and info.get("points")
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
        pts = info["points"]
        row["points"] = pts
        row["splits"] = splits_from_points(pts)
        row["source"] = f"Synergy BIP with pitcher hand (newest {PER_CHART} per chart)"
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
        f"Each of the eight charts (vs RHP and vs LHP × 2K, non-2K, RISP, non-RISP) "
        f"uses the newest {PER_CHART} balls in play with that pitcher hand and that split. "
        "Balls with no pitcher hand are omitted. A ball can sit on a count chart and a runner chart. "
        "Fewer than 50 means that is the whole sample, through the prior year including Puerto Rico."
    )
    spray["spray_recent_cap"] = PER_CHART
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
    queued = [r for r in rows if (done.get(r["synergy_id"]) or {}).get("status") not in ("ok", "empty")]
    log(f"queued={len(queued)} checkpointed={len(rows) - len(queued)} per_chart={PER_CHART}")
    token = bas.login_token()
    for i, player in enumerate(queued, 1):
        sid = player["synergy_id"]
        log(f"[{i}/{len(queued)}] {player['team']} {player['name']}")
        try:
            events, counts, token = fetch_chart_events(token, sid)
            pts = points_from_events(events, sid, player["team"])
        except Exception as exc:  # noqa: BLE001
            log(f"  fail {exc}")
            if "401" in str(exc):
                token = bas.login_token()
            continue
        chart_n = point_chart_counts(pts)
        hands = Counter(p.get("pitcher_hand") for p in pts)
        years = Counter((p.get("game_date_sort") or "")[:4] for p in pts)
        if not pts:
            done[sid] = {"status": "empty", "name": player["name"], "team": player["team"]}
            save_json(CKPT, ckpt)
            log("  no handed balls in play")
            continue
        done[sid] = {
            "status": "ok",
            "name": player["name"],
            "team": player["team"],
            "n": len(pts),
            "L": hands.get("L", 0),
            "R": hands.get("R", 0),
            "charts": chart_n,
            "event_charts": counts,
            "years": dict(years),
            "points": pts,
        }
        save_json(CKPT, ckpt)
        log(f"  kept {len(pts)} charts={chart_n} years={dict(years)}")
    apply_checkpoint()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

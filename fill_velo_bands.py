"""Attach real velocity percentiles to arsenal pitches.

Middle 50% is the 25th–75th percentile of pitch speed. Top 10% is the 90th
percentile. Speeds come from Synergy pitchSpeedMph. A pitch keeps its average
when fewer than 12 tracked speeds exist.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import build_advance_synergy as bas

ROOT = Path(__file__).resolve().parent
ARSENAL = ROOT / "data" / "pregame_pitcher_arsenals.json"
CHECKPOINT = ROOT / "runs" / "velo_band_checkpoint.json"
MIN_N = 12
MAX_EVENTS = 400


def percentile(values: list[float], pct: float) -> float:
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def bands(speeds: list[float]) -> dict | None:
    clean = [v for v in speeds if 55 <= v <= 105]
    if len(clean) < MIN_N:
        return None
    return {
        "n": len(clean),
        "velo_p25": round(percentile(clean, 25), 1),
        "velo_p75": round(percentile(clean, 75), 1),
        "velo_p90": round(percentile(clean, 90), 1),
    }


FASTBALLS = {"FF", "FA", "SI", "FT", "FC"}


def apply_band(pitch: dict, row: dict) -> None:
    pitch["velo_p25"] = row["velo_p25"]
    pitch["velo_p75"] = row["velo_p75"]
    pitch["velo_p90"] = row["velo_p90"]
    pitch["velo_n"] = row["n"]


def band_for_code(code: str, band_map: dict) -> dict | None:
    if code in band_map:
        return band_map[code]
    for alt in bas._PITCH_TYPE_ALIASES.get(code, ()):
        if alt in band_map:
            return band_map[alt]
    return None


def attach_bands(pitchers: list, done: dict) -> int:
    """Put each pitch's own speed band on that arsenal row.

    A Synergy fastball band with no FF row lands on the one arsenal fastball
    whose average speed is clearly closest, and only when that row has no band.
    """
    attached = 0
    for p in pitchers:
        hit = done.get(str(p.get("synergy_id") or "")) or {}
        band_map = hit.get("bands") or {}
        if not band_map:
            continue
        arsenal = p.get("arsenal") or []
        present = {bas._pitch_code(a.get("type") or a.get("code")) for a in arsenal}
        for pitch in arsenal:
            code = bas._pitch_code(pitch.get("type") or pitch.get("code"))
            row = band_for_code(code, band_map)
            if not row:
                continue
            apply_band(pitch, row)
            attached += 1
        leftover = None
        for code in ("FF", "FA"):
            if code in band_map and code not in present:
                leftover = band_map[code]
                break
        if not leftover:
            continue
        window_lo = float(leftover["velo_p25"]) - 1.0
        window_hi = float(leftover["velo_p75"]) + 1.5
        inside = []
        for pitch in arsenal:
            code = bas._pitch_code(pitch.get("type") or pitch.get("code"))
            if code not in FASTBALLS or pitch.get("velo_p25") is not None:
                continue
            if pitch.get("velo") is None:
                continue
            velo = float(pitch["velo"])
            if window_lo <= velo <= window_hi:
                inside.append(pitch)
        if len(inside) == 1:
            apply_band(inside[0], leftover)
            attached += 1
    return attached


def load_checkpoint() -> dict:
    if CHECKPOINT.is_file():
        return json.loads(CHECKPOINT.read_text())
    return {}


def main() -> None:
    doc = json.loads(ARSENAL.read_text())
    done = load_checkpoint()
    token = Path("/tmp/synergy_token.txt").read_text().strip()
    targets = [p for p in doc["pitchers"] if p.get("synergy_id")]
    print(f"pitchers {len(targets)} cached {len(done)}", flush=True)
    for i, p in enumerate(targets, 1):
        sid = str(p["synergy_id"])
        if sid in done:
            continue
        try:
            events = bas.fetch_events_pages(
                token,
                pitcher_id=sid,
                max_events=MAX_EVENTS,
                take=200,
                pause_s=0.05,
                timeout_s=60,
                max_attempts=2,
            )
        except Exception as exc:
            print(f"  FAIL {p.get('name')} {exc}", flush=True)
            done[sid] = {"error": str(exc)}
            CHECKPOINT.write_text(json.dumps(done))
            continue
        by_code: dict[str, list[float]] = defaultdict(list)
        for ev in events:
            pitch = ev.get("pitch") or {}
            if not isinstance(pitch, dict):
                continue
            speed = pitch.get("pitchSpeedMph")
            if speed is None:
                continue
            try:
                velo = float(speed)
            except (TypeError, ValueError):
                continue
            code = bas._pitch_code(pitch.get("pitchKind") or pitch.get("pitchType"))
            by_code[code].append(velo)
        packed = {}
        for code, speeds in by_code.items():
            row = bands(speeds)
            if row:
                packed[code] = row
        done[sid] = {"name": p.get("name"), "events": len(events), "bands": packed}
        CHECKPOINT.write_text(json.dumps(done))
        label = ", ".join(
            f"{c} {b['velo_p25']:.0f}-{b['velo_p75']:.0f} T{b['velo_p90']:.0f} n={b['n']}"
            for c, b in packed.items()
        )
        print(f"  {i}/{len(targets)} {p.get('name')} events={len(events)} {label}", flush=True)

    attached = attach_bands(doc["pitchers"], done)
    ARSENAL.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"attached {attached}", flush=True)


if __name__ == "__main__":
    main()

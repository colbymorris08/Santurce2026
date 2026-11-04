#!/usr/bin/env python3
"""Backfill incomplete opposing-pitcher chart cells from prior seasons.

Prefer the most recent season that has a real sample (2026 → older).
Sources: Synergy pitchSpeedMph (velo bands), MLB Statcast (bands + spin +
movement), Prospect Savant (movement + spin). Never invents numbers.
Overwrites spin=0 when a tracked sample exists.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import synergy_dns_fix  # noqa: F401
import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import fill_movement_plot_batches as move
import fill_remaining_holes as holes
import fill_velo_bands as velo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
ARSENAL = DATA / "pregame_pitcher_arsenals.json"
ADVANCE = DATA / "advance_opposing_pitchers.json"
REPORT = RUNS / "prior_season_cells_report.json"
BEFORE = RUNS / "prior_season_cells_before.json"
AFTER = RUNS / "prior_season_cells_after.json"
CKPT = RUNS / "prior_season_cells_checkpoint.json"
YEARS = (2026, 2025, 2024, 2023, 2022)
MIN_BAND_N = velo.MIN_N


def dump(path: Path, doc: Any) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def load(name: str):
    return json.loads((DATA / name).read_text())


def norm(name: str) -> str:
    return ligapr.norm_name(name or "")


def synergy_token() -> str:
    path = Path("/tmp/synergy_token.txt")
    if path.is_file() and path.read_text().strip():
        return path.read_text().strip()
    tok = bas.login_token(headed=False)
    path.write_text(tok)
    return tok


def mlb_id(row: dict) -> int | None:
    for key in ("mlb_id", "player_id"):
        try:
            value = int(row.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return None


def has_velo_bands(row: dict | None) -> bool:
    return any(
        a.get("velo_p25") is not None and a.get("velo_p75") is not None
        for a in ((row or {}).get("arsenal") or [])
    )


def missing_spin_pitches(row: dict | None) -> list[str]:
    """Only flag bogus spin=0 (tracked cell written as zero). Null = not tracked."""
    out = []
    for pitch in (row or {}).get("arsenal") or []:
        code = holes.chart_code(pitch)
        if not code:
            continue
        spin = pitch.get("spin")
        if spin in (0, 0.0) or (holes.finite(spin) and float(spin) <= 0):
            out.append(code)
    return out


def null_or_zero_spin_pitches(row: dict | None) -> list[str]:
    """Pitches we will try to fill from prior-season PS/Statcast."""
    out = []
    for pitch in (row or {}).get("arsenal") or []:
        code = holes.chart_code(pitch)
        if not code:
            continue
        spin = pitch.get("spin")
        if spin in (None, "", 0, 0.0) or not holes.finite(spin) or float(spin) <= 0:
            out.append(code)
    return out


def find_arsenal(ars: list[dict], name: str, team: str, mid) -> dict | None:
    for p in ars:
        if p.get("name") == name and p.get("team") == team:
            return p
    if mid and int(mid) > 0:
        mid = int(mid)
        for p in ars:
            if p.get("mlb_id") == mid or p.get("player_id") == mid:
                return p
    for p in ars:
        if p.get("name") == name:
            return p
    return None


def heat_located(heat: dict, row: dict | None) -> int:
    if not row:
        return 0
    pitchers = heat.get("pitchers") or {}
    best = 0
    for key in (
        str(row.get("mlb_id") or ""),
        str(row.get("player_id") or ""),
        str(row.get("synergy_id") or ""),
    ):
        if key and key in pitchers:
            best = max(best, int((pitchers[key] or {}).get("located") or 0))
    want = norm(row.get("name") or "")
    for rec in pitchers.values():
        if norm((rec or {}).get("name") or "") == want:
            best = max(best, int((rec or {}).get("located") or 0))
    return best


def status_for(op: dict, team: str, ars: list[dict], heat: dict) -> dict:
    row = find_arsenal(ars, op["name"], team, op.get("mlb_id"))
    chm = (row or {}).get("count_hand_mix") or {}
    arsenal = (row or {}).get("arsenal") or []
    pitches = int((row or {}).get("pitches") or 0)
    bands = has_velo_bands(row)
    movement = holes.has_movement(row)
    spin0 = missing_spin_pitches(row)
    missing = []
    if not row:
        missing.append("no_arsenal_row")
    if pitches <= 0:
        missing.append("no_pitches")
    if not arsenal:
        missing.append("no_arsenal")
    if not (chm.get("overall") and chm.get("0-0") and chm.get("2_strikes")):
        missing.append("count_hand")
    if pitches > 0 and not chm.get("iz"):
        missing.append("iz")
    if pitches > 0 and not bands:
        missing.append("velo_bands")
    if pitches > 0 and not ((row or {}).get("platoon") or {}).get("vs_RHB") and not (
        (row or {}).get("platoon") or {}
    ).get("vs_LHB"):
        missing.append("platoon")
    hl = heat_located(heat, row)
    if pitches > 0 and hl <= 0:
        missing.append("heat")
    if pitches > 0 and not movement:
        missing.append("movement")
    if pitches > 0 and spin0:
        missing.append("spin")
    if not (row or {}).get("summer_line"):
        missing.append("summer")
    band_preview = {
        a.get("type"): (
            f"{a.get('velo_p25')}-{a.get('velo_p75')}"
            if a.get("velo_p25") is not None
            else None
        )
        for a in arsenal
    }
    return {
        "team": team,
        "name": op["name"],
        "mlb_id": op.get("mlb_id") or mlb_id(row or {}),
        "player_id": (row or {}).get("player_id"),
        "synergy_id": (row or {}).get("synergy_id"),
        "pitches": pitches,
        "heat_located": hl,
        "has_iz": bool(chm.get("iz")),
        "count_hand": bool(chm.get("overall") and chm.get("0-0") and chm.get("2_strikes")),
        "velo_bands": bands,
        "velo_band_preview": band_preview,
        "movement": movement,
        "spin_missing": spin0,
        "summer": bool((row or {}).get("summer_line")),
        "missing": missing,
        "movement_source": (row or {}).get("movement_source")
        or (row or {}).get("ps_source")
        or (row or {}).get("synergy_movement_source"),
        "velo_band_source": (row or {}).get("velo_band_source"),
        "velo_band_season": (row or {}).get("velo_band_season"),
    }


def snapshot() -> list[dict]:
    one = load("pitcher_one_page.json")
    ars = load("pregame_pitcher_arsenals.json")["pitchers"]
    heat = load("advance_heatmaps.json")
    out = []
    for team, pack in (one.get("teams") or {}).items():
        if team == "SAN":
            continue
        for op in pack.get("pitchers") or []:
            out.append(status_for(op, team, ars, heat))
    return out


def percentile(values: list[float], pct: float) -> float:
    return velo.percentile(values, pct)


def bands_from_speeds(speeds: list[float]) -> dict | None:
    return velo.bands(speeds)


def attach_band_map(row: dict, band_map: dict, *, source: str, season) -> int:
    """Apply bands pitch-by-pitch; never overwrite an existing p25/p75."""
    if not band_map:
        return 0
    attached = 0
    arsenal = row.get("arsenal") or []
    present = {bas._pitch_code(a.get("type") or a.get("code")) for a in arsenal}
    for pitch in arsenal:
        if pitch.get("velo_p25") is not None and pitch.get("velo_p75") is not None:
            continue
        code = bas._pitch_code(pitch.get("type") or pitch.get("code"))
        band = velo.band_for_code(code, band_map)
        if not band:
            continue
        velo.apply_band(pitch, band)
        attached += 1
    # Leftover FF band → lone unmatched fastball (same rule as fill_velo_bands)
    leftover = None
    for code in ("FF", "FA"):
        if code in band_map and code not in present:
            leftover = band_map[code]
            break
    if leftover:
        window_lo = float(leftover["velo_p25"]) - 1.0
        window_hi = float(leftover["velo_p75"]) + 1.5
        inside = []
        for pitch in arsenal:
            code = bas._pitch_code(pitch.get("type") or pitch.get("code"))
            if code not in velo.FASTBALLS or pitch.get("velo_p25") is not None:
                continue
            if pitch.get("velo") is None:
                continue
            v = float(pitch["velo"])
            if window_lo <= v <= window_hi:
                inside.append(pitch)
        if len(inside) == 1:
            velo.apply_band(inside[0], leftover)
            attached += 1
    if attached or has_velo_bands(row):
        row["velo_band_source"] = source
        row["velo_band_season"] = season
    return attached


def fill_velo_synergy(row: dict, token: str) -> dict:
    sid = str(row.get("synergy_id") or "")
    if not sid:
        return {"status": "no_sid"}
    if has_velo_bands(row):
        return {"status": "already"}
    try:
        events = bas.fetch_events_pages(
            token,
            pitcher_id=sid,
            max_events=1200,
            take=200,
            pause_s=0.05,
            timeout_s=90,
            max_attempts=2,
            stop_before_year=2022,
            max_scan=6000,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    by_year: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ev in events:
        year = bas.event_season_year(ev) or 0
        if year not in YEARS:
            continue
        pitch = ev.get("pitch") or {}
        if not isinstance(pitch, dict):
            continue
        speed = pitch.get("pitchSpeedMph")
        if speed is None:
            continue
        try:
            value = float(speed)
        except (TypeError, ValueError):
            continue
        code = bas._pitch_code(pitch.get("pitchKind") or pitch.get("pitchType"))
        if code:
            by_year[year][code].append(value)

    # Prefer most recent year that yields any band; then merge older years for
    # pitch types still missing a band.
    combined: dict[str, dict] = {}
    used_years: list[int] = []
    for year in YEARS:
        year_bands = {}
        for code, speeds in by_year.get(year, {}).items():
            band = bands_from_speeds(speeds)
            if band:
                year_bands[code] = band
        if not year_bands:
            continue
        used_years.append(year)
        for code, band in year_bands.items():
            if code not in combined:
                combined[code] = {**band, "season": year}
        if all(
            holes.chart_code(p) in combined
            for p in (row.get("arsenal") or [])
            if holes.chart_code(p)
        ):
            break

    if not combined:
        return {"status": "no_bands", "events": len(events), "years_with_speed": sorted(by_year)}

    band_map = {code: {k: v for k, v in band.items() if k != "season"} for code, band in combined.items()}
    primary_season = used_years[0] if used_years else None
    attach_band_map(
        row,
        band_map,
        source=f"Synergy pitchSpeedMph {primary_season}"
        + (f"+{','.join(str(y) for y in used_years[1:])}" if len(used_years) > 1 else ""),
        season=primary_season,
    )
    label = ", ".join(
        f"{c} {b['velo_p25']:.0f}-{b['velo_p75']:.0f} ({combined[c].get('season')})"
        for c, b in band_map.items()
    )
    return {
        "status": "filled" if has_velo_bands(row) else "partial",
        "events": len(events),
        "bands": label,
        "years": used_years,
    }


def fill_velo_statcast(row: dict) -> dict:
    mid = mlb_id(row)
    if not mid:
        return {"status": "no_mlb_id"}
    if has_velo_bands(row) and not any(
        a.get("velo_p25") is None for a in (row.get("arsenal") or []) if holes.chart_code(a)
    ):
        # Still try to fill missing pitch types
        pass

    try:
        from pybaseball import cache, statcast_pitcher
        import pandas as pd

        cache.enable()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"pybaseball: {exc}"}

    # Recent seasons first; stop once every arsenal pitch type has a band.
    by_year_kind: list[tuple[int, str, dict[str, list[float]]]] = []
    need_codes = {
        holes.chart_code(p)
        for p in (row.get("arsenal") or [])
        if holes.chart_code(p) and p.get("velo_p25") is None
    }
    for kind, ranges in (("regular", holes.REGULAR_RANGES), ("spring", holes.SPRING_RANGES)):
        for year, start, end in ranges:
            y = int(year)
            if y not in (2026, 2025, 2024, 2023):
                continue
            if not need_codes:
                break
            try:
                frame = statcast_pitcher(start, end, mid)
            except Exception as exc:  # noqa: BLE001
                print(f"    statcast {kind} {year} fail: {exc}", flush=True)
                continue
            if frame is None or getattr(frame, "empty", True):
                continue
            if "game_type" in frame.columns:
                want = "R" if kind == "regular" else "S"
                frame = frame[frame["game_type"] == want]
            if frame is None or frame.empty or "pitch_type" not in frame.columns:
                continue
            speeds: dict[str, list[float]] = defaultdict(list)
            for _, rec in frame.iterrows():
                code = str(rec.get("pitch_type") or "").upper()
                code = holes.CODE_ALIAS.get(code, code)
                speed = rec.get("release_speed")
                if not code or speed != speed:
                    continue
                try:
                    speeds[code].append(float(speed))
                except (TypeError, ValueError):
                    continue
            if speeds:
                by_year_kind.append((y, kind, speeds))
                for code, xs in speeds.items():
                    if code in need_codes and bands_from_speeds(xs):
                        need_codes.discard(code)
            time.sleep(0.05)
        if not need_codes:
            break

    # Prefer regular over spring within same year by sorting: year desc, regular first
    by_year_kind.sort(key=lambda t: (-t[0], 0 if t[1] == "regular" else 1))

    combined: dict[str, dict] = {}
    used = []
    for year, kind, speeds in by_year_kind:
        year_bands = {}
        for code, xs in speeds.items():
            band = bands_from_speeds(xs)
            if band:
                year_bands[code] = band
        if not year_bands:
            continue
        used.append(f"{year}{kind[0]}")
        for code, band in year_bands.items():
            # Only fill codes still missing a band on the arsenal row
            already = any(
                holes.chart_code(p) == code and p.get("velo_p25") is not None
                for p in (row.get("arsenal") or [])
            )
            if code in combined or already:
                continue
            combined[code] = {**band, "season": year, "kind": kind}

    if not combined:
        return {"status": "no_bands", "tried": used}

    band_map = {
        code: {k: v for k, v in band.items() if k not in ("season", "kind")}
        for code, band in combined.items()
    }
    primary = next(iter(combined.values()))
    attach_band_map(
        row,
        band_map,
        source=f"MLB Statcast {primary.get('kind')} {primary.get('season')}",
        season=primary.get("season"),
    )
    label = ", ".join(
        f"{c} {b['velo_p25']:.0f}-{b['velo_p75']:.0f} ({combined[c].get('season')}{combined[c].get('kind','')[0]})"
        for c, b in band_map.items()
    )
    return {
        "status": "filled" if has_velo_bands(row) else "partial",
        "bands": label,
        "used": used,
    }


def clear_bogus_zero_spin(row: dict) -> list[str]:
    """When no source has spin, blank spin=0 so the chart does not show a fake zero."""
    cleared = []
    for pitch in row.get("arsenal") or []:
        code = holes.chart_code(pitch)
        spin = pitch.get("spin")
        if code and (spin in (0, 0.0) or (holes.finite(spin) and float(spin) <= 0)):
            pitch["spin"] = None
            pitch["spin_source"] = "unavailable (no tracked sample)"
            cleared.append(code)
    return cleared


def fill_spin_from_sources(row: dict) -> dict:
    """Overwrite spin=0 / missing from Prospect Savant then Statcast."""
    need = set(null_or_zero_spin_pitches(row))
    if not need:
        return {"status": "already"}
    mid = mlb_id(row)
    if not mid:
        cleared = clear_bogus_zero_spin(row)
        return {"status": "no_mlb_id", "need": sorted(need), "cleared_zero": cleared}

    import build_pregame_arsenal as bpa

    filled = []
    ps_hit = False
    # Prospect Savant averages by season (skip ancient 2022 — endpoint 500s)
    for season in (2026, 2025, 2024, 2023):
        try:
            rows = bpa.http_json(f"{bpa.PS_BASE}/stuff/{mid}/{season}", timeout=15)
        except Exception as exc:  # noqa: BLE001
            print(f"    PS spin {season}: {exc}", flush=True)
            continue
        if not isinstance(rows, list) or not rows:
            continue
        ps_hit = True
        by_code = {}
        for rec in rows:
            code = str(rec.get("pitch_type") or "").strip().upper()
            code = holes.CODE_ALIAS.get(code, code)
            spin = rec.get("release_spin_rate")
            if not code or spin in (None, ""):
                continue
            try:
                spin_f = float(spin)
            except (TypeError, ValueError):
                continue
            if spin_f <= 0:
                continue
            by_code[code] = int(round(spin_f))
        if not by_code:
            continue
        for pitch in row.get("arsenal") or []:
            code = holes.chart_code(pitch)
            if code not in need:
                continue
            if code in by_code:
                pitch["spin"] = by_code[code]
                pitch["spin_source"] = f"Prospect Savant {season}"
                filled.append(f"{code}:{by_code[code]}@{season}")
                need.discard(code)
        if not need:
            return {"status": "filled", "filled": filled, "source": "Prospect Savant"}
        time.sleep(0.05)

    # Statcast only when PS had a player page (MLB/MiLB tracked) and zeros remain,
    # or when the hole is an explicit spin=0 on the arsenal.
    force_sc = bool(missing_spin_pitches(row))
    if need and (ps_hit or force_sc):
        try:
            from pybaseball import cache, statcast_pitcher

            cache.enable()
        except Exception as exc:  # noqa: BLE001
            cleared = clear_bogus_zero_spin(row)
            return {
                "status": "partial" if filled else "error",
                "filled": filled,
                "error": str(exc),
                "still": sorted(need),
                "cleared_zero": cleared,
            }

        for kind, ranges in (("regular", holes.REGULAR_RANGES), ("spring", holes.SPRING_RANGES)):
            for year, start, end in ranges:
                if not need:
                    break
                y = int(year)
                if y < 2023:
                    continue
                try:
                    frame = statcast_pitcher(start, end, mid)
                except Exception as exc:  # noqa: BLE001
                    print(f"    SC spin {kind} {year}: {exc}", flush=True)
                    continue
                if frame is None or getattr(frame, "empty", True):
                    continue
                if "game_type" in frame.columns:
                    want = "R" if kind == "regular" else "S"
                    frame = frame[frame["game_type"] == want]
                if frame is None or frame.empty:
                    continue
                spins: dict[str, list[float]] = defaultdict(list)
                for _, rec in frame.iterrows():
                    code = str(rec.get("pitch_type") or "").upper()
                    code = holes.CODE_ALIAS.get(code, code)
                    sr = rec.get("release_spin_rate")
                    if not code or sr != sr:
                        continue
                    try:
                        spins[code].append(float(sr))
                    except (TypeError, ValueError):
                        continue
                for pitch in row.get("arsenal") or []:
                    code = holes.chart_code(pitch)
                    if code not in need:
                        continue
                    xs = spins.get(code) or []
                    if len(xs) < 3:
                        continue
                    avg = int(round(sum(xs) / len(xs)))
                    if avg <= 0:
                        continue
                    pitch["spin"] = avg
                    pitch["spin_source"] = f"MLB Statcast {kind} {year}"
                    filled.append(f"{code}:{avg}@{year}{kind[0]}")
                    need.discard(code)
                time.sleep(0.05)

    cleared = clear_bogus_zero_spin(row)
    if filled and not need:
        return {"status": "filled", "filled": filled}
    if filled:
        return {"status": "partial", "filled": filled, "still": sorted(need), "cleared_zero": cleared}
    return {"status": "unavailable", "still": sorted(need), "cleared_zero": cleared}


def fill_movement(row: dict, player: dict, resolved: dict, token_box: dict) -> dict:
    if holes.has_movement(row):
        # Still allow spin-only merge from PS/Statcast when movement exists
        return {"status": "already", "source": row.get("movement_source") or row.get("ps_source")}
    found = move.lookup_pitcher(player, row, resolved, token_box)
    if not found.get("pitches"):
        return {
            "status": "unavailable",
            "attempts": found.get("attempts") or [],
            "why": move.best_why(found.get("attempts") or []),
        }
    added = move.merge_plot_only(
        row,
        found["pitches"],
        source=found["source"],
        season=found["season"],
        throws=found.get("throws") or row.get("throws") or player.get("throws") or "",
    )
    # Also overwrite spin=0 from the same sample
    by_code = {holes.chart_code(p): p for p in found["pitches"]}
    for pitch in row.get("arsenal") or []:
        code = holes.chart_code(pitch)
        src = by_code.get(code)
        if not src:
            continue
        if (pitch.get("spin") in (None, "", 0, 0.0) or not holes.finite(pitch.get("spin"))) and holes.finite(
            src.get("spin")
        ) and float(src.get("spin") or 0) > 0:
            pitch["spin"] = int(round(float(src["spin"])))
            pitch["spin_source"] = found["source"]
    return {
        "status": "filled" if added else "no_plot",
        "source": found.get("source"),
        "season": found.get("season"),
        "n": len(found.get("pitches") or []),
    }


def fill_summer_prior(row: dict, adv: dict | None) -> dict:
    if (row or {}).get("summer_line"):
        if adv is not None and not adv.get("summer_line"):
            adv["summer_line"] = row["summer_line"]
        return {"status": "already"}
    if adv and adv.get("summer_line"):
        row["summer_line"] = adv["summer_line"]
        return {"status": "copied_from_advance"}
    mid = mlb_id(row) or 0
    if mid <= 0:
        return {"status": "no_mlb_id"}
    # 2026 first
    lines = bas.fetch_2026_pitcher_summer_lines([mid])
    line = lines.get(mid)
    if line:
        row["summer_line"] = line
        if adv is not None:
            adv["summer_line"] = line
        return {"status": "filled_2026", "summary": f"{line.get('team')} {line.get('ip')} IP"}
    # 2025 via prior helper
    import backfill_pitcher_prior_seasons as prior

    prior_lines = prior.summer_2025_lines([mid])
    line = prior_lines.get(mid)
    if line:
        row["summer_line"] = line
        if adv is not None:
            adv["summer_line"] = line
        return {"status": "filled_2025", "summary": f"{line.get('team')} {line.get('ip')} IP"}
    return {"status": "unavailable"}


def prioritize(rows: list[dict]) -> list[dict]:
    nov4 = set()
    path = DATA / "roster_official_2026_11_04_added.json"
    if path.is_file():
        for r in (json.loads(path.read_text()).get("added") or []):
            if str(r.get("position") or "").upper() == "P":
                nov4.add(norm(r.get("name") or ""))
    target_boost = {
        "johnmcmillon",
        "blayneenlow",
        "juanjavierechavarria",
        "tannerburns",
        "aaronmishoulam",
        "ryanmurphy",
        "johnaponte",
    } | nov4

    def key(r: dict):
        n = norm(r["name"])
        return (
            0 if n == "johnmcmillon" else 1,
            0 if n in target_boost else 1,
            0 if "velo_bands" in r["missing"] else 1,
            0 if "spin" in r["missing"] else 1,
            0 if "movement" in r["missing"] else 1,
            r["team"],
            r["name"],
        )

    return sorted(rows, key=key)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", action="append", default=[], help="Only these pitcher names")
    ap.add_argument("--limit", type=int, default=0, help="Max incomplete pitchers to process")
    ap.add_argument("--skip-movement", action="store_true")
    ap.add_argument("--skip-summer", action="store_true")
    ap.add_argument("--skip-velo", action="store_true")
    ap.add_argument("--skip-spin", action="store_true")
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip pitchers whose current missing fields were already attempted in checkpoint",
    )
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    before = snapshot()
    dump(BEFORE, {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "players": before})
    miss_c = Counter()
    for r in before:
        for m in r["missing"]:
            miss_c[m] += 1
    print(f"BEFORE opposing={len(before)} incomplete={sum(1 for r in before if r['missing'])}")
    print(" missing", dict(miss_c))
    mcm = next((r for r in before if r["name"] == "John McMillon"), None)
    print(" McMillon BEFORE:", json.dumps(mcm, indent=2) if mcm else None)

    ckpt = {"pitchers": {}}
    if CKPT.is_file():
        ckpt = json.loads(CKPT.read_text())

    def attempted_fields(rec: dict) -> set[str]:
        out = set()
        if rec.get("velo_synergy") or rec.get("velo_statcast"):
            out.add("velo_bands")
        if rec.get("spin"):
            out.add("spin")
        if rec.get("movement"):
            out.add("movement")
        if rec.get("summer"):
            out.add("summer")
        return out

    want = {norm(n) for n in args.name} if args.name else None
    targets = [r for r in before if r["missing"] and (want is None or norm(r["name"]) in want)]
    targets = prioritize(targets)
    if args.resume:
        kept = []
        skipped = 0
        for r in targets:
            key = f"{r['team']}|{norm(r['name'])}"
            prev = (ckpt.get("pitchers") or {}).get(key) or {}
            done = attempted_fields(prev)
            # Still process if a current hole was never attempted
            remain = [m for m in r["missing"] if m not in done and m not in (
                "no_pitches", "no_arsenal", "no_arsenal_row", "count_hand", "iz", "platoon", "heat"
            )]
            if not remain:
                skipped += 1
                continue
            kept.append(r)
        print(f" resume skip={skipped} remaining={len(kept)}", flush=True)
        targets = kept
    if args.limit:
        targets = targets[: args.limit]

    arsenal_doc = load("pregame_pitcher_arsenals.json")
    ars = arsenal_doc["pitchers"]
    adv_doc = load("advance_opposing_pitchers.json")
    adv_by = {(p.get("team"), norm(p.get("name") or "")): p for p in adv_doc.get("pitchers") or []}
    holes_ckpt = json.loads(holes.CHECKPOINT.read_text()) if holes.CHECKPOINT.is_file() else {}
    resolved = holes_ckpt.get("resolved_ids") or {}
    token_box: dict = {"keys": set()}
    token = None

    report_rows = []
    for i, st in enumerate(targets, 1):
        row = find_arsenal(ars, st["name"], st["team"], st.get("mlb_id"))
        if not row:
            report_rows.append({**st, "result": "no_arsenal_row"})
            print(f"[{i}/{len(targets)}] {st['team']} {st['name']} NO ARSENAL ROW", flush=True)
            continue
        player = {
            "name": st["name"],
            "team": st["team"],
            "id": st.get("mlb_id") or row.get("player_id"),
            "throws": row.get("throws"),
            "position": "P",
        }
        adv = adv_by.get((st["team"], norm(st["name"])))
        key = f"{st['team']}|{norm(st['name'])}"
        rec: dict[str, Any] = {
            **((ckpt.get("pitchers") or {}).get(key) or {}),
            "team": st["team"],
            "name": st["name"],
            "before_missing": list(st["missing"]),
        }
        print(f"[{i}/{len(targets)}] {st['team']} {st['name']} missing={st['missing']}", flush=True)

        if not args.skip_velo and "velo_bands" in st["missing"]:
            if token is None:
                token = synergy_token()
            syn = fill_velo_synergy(row, token)
            rec["velo_synergy"] = syn
            print(f"  velo synergy: {syn}", flush=True)
            if not has_velo_bands(row) or any(
                a.get("velo_p25") is None for a in (row.get("arsenal") or []) if holes.chart_code(a)
            ):
                sc = fill_velo_statcast(row)
                rec["velo_statcast"] = sc
                print(f"  velo statcast: {sc}", flush=True)

        # Spin: only chase explicit spin=0 holes, or fill from PS while we already
        # have an MLB id and are touching the row for another hole (cheap PS only).
        if not args.skip_spin and (
            "spin" in st["missing"]
            or (mlb_id(row) and null_or_zero_spin_pitches(row) and "movement" in st["missing"])
        ):
            spin = fill_spin_from_sources(row)
            rec["spin"] = spin
            print(f"  spin: {spin}", flush=True)

        if not args.skip_movement and "movement" in st["missing"]:
            if token is None and row.get("synergy_id"):
                try:
                    token = synergy_token()
                    token_box["token"] = token
                except Exception as exc:  # noqa: BLE001
                    token_box["error"] = str(exc)
            elif token:
                token_box["token"] = token
            mv = fill_movement(row, player, resolved, token_box)
            rec["movement"] = mv
            print(f"  movement: {mv.get('status')} {mv.get('source') or mv.get('why')}", flush=True)

        if not args.skip_summer and "summer" in st["missing"]:
            sm = fill_summer_prior(row, adv)
            rec["summer"] = sm
            print(f"  summer: {sm}", flush=True)

        # Persist after each pitcher so crashes keep progress
        arsenal_doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        dump(ARSENAL, arsenal_doc)
        dump(ADVANCE, adv_doc)
        ckpt["pitchers"][f"{st['team']}|{norm(st['name'])}"] = rec
        ckpt["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        dump(CKPT, ckpt)
        report_rows.append(rec)

    print("Rebuilding pitcher_one_page.json", flush=True)
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)

    after = snapshot()
    dump(AFTER, {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "players": after})
    miss_after = Counter()
    for r in after:
        for m in r["missing"]:
            miss_after[m] += 1
    mcm_after = next((r for r in after if r["name"] == "John McMillon"), None)
    unfilled = [r for r in after if r["missing"]]
    dump(
        REPORT,
        {
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "hole_counts_before": dict(miss_c),
            "hole_counts_after": dict(miss_after),
            "processed": len(report_rows),
            "mcmillon_before": mcm,
            "mcmillon_after": mcm_after,
            "still_incomplete": [
                {
                    "team": r["team"],
                    "name": r["name"],
                    "missing": r["missing"],
                    "mlb_id": r.get("mlb_id"),
                    "pitches": r.get("pitches"),
                }
                for r in unfilled
            ],
            "rows": report_rows,
        },
    )
    print(f"AFTER incomplete={len(unfilled)} missing={dict(miss_after)}")
    print(" McMillon AFTER:", json.dumps(mcm_after, indent=2) if mcm_after else None)


if __name__ == "__main__":
    main()

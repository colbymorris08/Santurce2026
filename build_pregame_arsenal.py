#!/usr/bin/env python3
"""
Build opposing-pitcher arsenal caches for Pregame:
  Pitch Usage / Platoon Splits / Pitch Tunneling (hitter-perspective plots).

Outputs:
  data/pregame_pitcher_arsenals.json
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
UA = {"User-Agent": "SanturceAnalytics/1.0"}
PS_BASE = "https://oriolebird.pythonanywhere.com"
COUNT_BUCKETS = ("0-0", "0-2", "1-2", "2-2", "3-2")
STATCAST_RANGES = [
    ("2025", "2025-03-20", "2025-10-05"),
    ("2026", "2026-03-20", "2026-07-22"),
]

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl")


def http_json(url: str, timeout: float = 60.0):
    import urllib.request

    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def to_float(v):
    if v is None or v in ("", ".---", "-.--"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pitcher_view_hb_in(pfx_x_ft: float | None) -> float | None:
    """Statcast catcher-oriented pfx_x (ft) → pitcher-view HB inches."""
    if pfx_x_ft is None:
        return None
    return round(-(pfx_x_ft * 12.0), 2)


def hitter_view_hb_in(pfx_x_ft: float | None) -> float | None:
    """Catcher / hitter-facing HB inches (same left-right as looking at the pitcher)."""
    if pfx_x_ft is None:
        return None
    return round(pfx_x_ft * 12.0, 2)


def ivb_in(pfx_z_ft: float | None) -> float | None:
    if pfx_z_ft is None:
        return None
    return round(pfx_z_ft * 12.0, 2)


def fetch_ps_arsenal(pid: int, seasons=(2025, 2026, 2024)) -> tuple[list[dict], int | None, str | None]:
    for season in seasons:
        try:
            rows = http_json(f"{PS_BASE}/stuff/{pid}/{season}")
        except Exception:
            continue
        if not isinstance(rows, list) or not rows:
            continue
        pitches = []
        for r in rows:
            ptype = str(r.get("pitch_type") or "").strip().upper()
            if not ptype:
                continue
            pfx_x = to_float(r.get("pfx_x"))
            pfx_z = to_float(r.get("pfx_z"))
            pitches.append(
                {
                    "type": ptype,
                    "name": r.get("pitch_name") or ptype,
                    "velo": round(to_float(r.get("release_speed")) or 0, 1),
                    "spin": int(round(to_float(r.get("release_spin_rate")) or 0)),
                    "ivb": ivb_in(pfx_z),
                    "hb_pitcher": pitcher_view_hb_in(pfx_x),
                    "hb_hitter": hitter_view_hb_in(pfx_x),
                    "usage": round(to_float(r.get("usage")) or 0, 1),
                    "pitches": int(r.get("pitches") or 0),
                    "ps_stuff": round(to_float(r.get("psStuff")) or 0, 1),
                    "whiff": to_float(r.get("swing_miss_percent")),
                    "xwoba": to_float(r.get("xwoba")),
                }
            )
        pitches.sort(key=lambda p: -(p.get("pitches") or 0))
        total = sum(p["pitches"] for p in pitches)
        return pitches, season, "Prospect Savant"
    return [], None, None


def count_key(balls, strikes) -> str | None:
    try:
        b, s = int(balls), int(strikes)
    except (TypeError, ValueError):
        return None
    key = f"{b}-{s}"
    return key if key in COUNT_BUCKETS else None


def aggregate_statcast(df) -> dict:
    """Usage-by-count + platoon movement averages from a Statcast pitcher frame."""
    if df is None or getattr(df, "empty", True) or "pitch_type" not in df.columns:
        return {}
    work = df[df["pitch_type"].notna()].copy()
    if work.empty:
        return {}

    def pack_group(sub):
        out = []
        total = len(sub)
        for ptype, grp in sub.groupby("pitch_type"):
            ptype = str(ptype).upper()
            n = len(grp)
            pfx_x = to_float(grp["pfx_x"].mean()) if "pfx_x" in grp else None
            pfx_z = to_float(grp["pfx_z"].mean()) if "pfx_z" in grp else None
            velo = to_float(grp["release_speed"].mean()) if "release_speed" in grp else None
            spin = to_float(grp["release_spin_rate"].mean()) if "release_spin_rate" in grp else None
            out.append(
                {
                    "type": ptype,
                    "velo": round(velo, 1) if velo is not None else None,
                    "spin": int(round(spin)) if spin is not None else None,
                    "ivb": ivb_in(pfx_z),
                    "hb_pitcher": pitcher_view_hb_in(pfx_x),
                    "hb_hitter": hitter_view_hb_in(pfx_x),
                    "usage": round(100.0 * n / total, 1) if total else 0,
                    "pitches": n,
                }
            )
        out.sort(key=lambda r: -(r["pitches"] or 0))
        return out

    usage_by_count = {}
    if "balls" in work.columns and "strikes" in work.columns:
        for _, row in work.iterrows():
            pass  # noqa — use vectorized below
        work = work.copy()
        work["_ck"] = [
            count_key(b, s) for b, s in zip(work["balls"], work["strikes"], strict=False)
        ]
        for ck in COUNT_BUCKETS:
            sub = work[work["_ck"] == ck]
            if len(sub) < 8:
                continue
            usage_by_count[ck] = pack_group(sub)

    platoon = {}
    if "stand" in work.columns:
        for hand, label in (("R", "vs_RHB"), ("L", "vs_LHB")):
            sub = work[work["stand"] == hand]
            if len(sub) < 20:
                continue
            platoon[label] = pack_group(sub)

    overall = pack_group(work)
    return {
        "overall": overall,
        "usage_by_count": usage_by_count,
        "platoon": platoon,
        "statcast_pitches": int(len(work)),
    }


def fetch_statcast_arsenal(pid: int) -> dict:
    try:
        from pybaseball import cache, statcast_pitcher

        cache.enable()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"pybaseball unavailable: {exc}"}

    frames = []
    seasons_used = []
    for year, start, end in STATCAST_RANGES:
        try:
            df = statcast_pitcher(start, end, pid)
        except Exception as exc:  # noqa: BLE001
            print("  statcast fail", pid, year, exc)
            continue
        if df is None or getattr(df, "empty", True):
            continue
        frames.append(df)
        seasons_used.append(year)
        time.sleep(0.12)
    if not frames:
        return {}
    import pandas as pd

    df = pd.concat(frames, ignore_index=True)
    packed = aggregate_statcast(df)
    if not packed:
        return {}
    packed["seasons"] = seasons_used
    packed["source"] = "MLB Statcast"
    return packed


def build_row(base: dict, ps_pitches: list, ps_season, ps_source, sc: dict) -> dict:
    pitches = ps_pitches
    if (not pitches) and sc.get("overall"):
        pitches = [
            {
                "type": p["type"],
                "name": p["type"],
                "velo": p.get("velo"),
                "spin": p.get("spin"),
                "ivb": p.get("ivb"),
                "hb_pitcher": p.get("hb_pitcher"),
                "hb_hitter": p.get("hb_hitter"),
                "usage": p.get("usage"),
                "pitches": p.get("pitches"),
                "ps_stuff": None,
                "whiff": None,
                "xwoba": None,
            }
            for p in sc["overall"]
        ]
    return {
        "name": base.get("name"),
        "player_id": base.get("player_id"),
        "team": base.get("team"),
        "throws": base.get("throws"),
        "mlb_debut": base.get("mlb_debut"),
        "level_hint": base.get("level_hint"),
        "stuff_plus": base.get("stuff_plus"),
        "pitches": sum(int(p.get("pitches") or 0) for p in pitches) or base.get("pitches"),
        "arsenal": pitches,
        "usage_by_count": sc.get("usage_by_count") or {},
        "platoon": sc.get("platoon") or {},
        "ps_season": ps_season,
        "ps_source": ps_source,
        "statcast_source": sc.get("source"),
        "statcast_seasons": sc.get("seasons") or [],
        "statcast_pitches": sc.get("statcast_pitches"),
        "view": "hitter",
        "note": (
            "Movement plots use hitter perspective (catcher-facing HB). "
            "Usage-by-count and platoon splits from MLB Statcast when tracked; "
            "overall arsenal from Prospect Savant / Statcast."
        ),
    }


def fetch_throws(ids: list[int]) -> dict[int, str]:
    out = {}
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        try:
            d = http_json(
                "https://statsapi.mlb.com/api/v1/people?personIds="
                + ",".join(map(str, batch))
                + "&hydrate=currentTeam"
            )
            for p in d.get("people") or []:
                out[int(p["id"])] = (p.get("pitchHand") or {}).get("code") or ""
        except Exception as exc:  # noqa: BLE001
            print("people fail", exc)
        time.sleep(0.1)
    return out


def main() -> None:
    stuff_path = OUT / "pregame_opposing_stuff.json"
    out_path = OUT / "pregame_pitcher_arsenals.json"
    base_rows = json.loads(stuff_path.read_text())
    existing = {}
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text())
            for r in prev.get("pitchers") or []:
                existing[int(r["player_id"])] = r
        except Exception:
            existing = {}

    ids = [int(r["player_id"]) for r in base_rows if r.get("player_id")]
    print(f"Opposing pitchers: {len(ids)}")
    throws = fetch_throws(ids)

    pitchers = []
    for i, base in enumerate(base_rows, 1):
        pid = int(base["player_id"])
        base = {**base, "throws": throws.get(pid) or base.get("throws")}
        cached = existing.get(pid)
        # Reuse Statcast if already built; always refresh PS movement cheaply when missing
        need_ps = not (cached and cached.get("arsenal"))
        need_sc = bool(base.get("mlb_debut")) and not (
            cached and (cached.get("usage_by_count") or cached.get("platoon"))
        )

        ps_pitches, ps_season, ps_source = [], None, None
        if need_ps or not cached:
            ps_pitches, ps_season, ps_source = fetch_ps_arsenal(pid)
            time.sleep(0.08)
        else:
            ps_pitches = cached.get("arsenal") or []
            ps_season = cached.get("ps_season")
            ps_source = cached.get("ps_source")

        sc = {}
        if need_sc:
            print(f"  Statcast {i}/{len(base_rows)} {base.get('name')} ({pid})")
            sc = fetch_statcast_arsenal(pid)
        elif cached:
            sc = {
                "usage_by_count": cached.get("usage_by_count") or {},
                "platoon": cached.get("platoon") or {},
                "source": cached.get("statcast_source"),
                "seasons": cached.get("statcast_seasons") or [],
                "statcast_pitches": cached.get("statcast_pitches"),
                "overall": None,
            }

        row = build_row(base, ps_pitches, ps_season, ps_source, sc)
        pitchers.append(row)
        if i % 15 == 0:
            out_path.write_text(
                json.dumps(
                    {
                        "pitchers": pitchers + [
                            existing[p]
                            for p in existing
                            if p not in {int(x["player_id"]) for x in pitchers}
                        ],
                        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "view": "hitter",
                    },
                    indent=2,
                )
            )
            print(f"  checkpoint {i}/{len(base_rows)} arsenal={sum(1 for p in pitchers if p.get('arsenal'))}")

    pitchers.sort(key=lambda r: (r.get("stuff_plus") is not None, r.get("stuff_plus") or 0), reverse=True)
    payload = {
        "pitchers": pitchers,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "view": "hitter",
        "count_buckets": list(COUNT_BUCKETS),
        "note": (
            "KNCT-style arsenal plots from hitter perspective. "
            "HB axis = catcher-facing (looking at pitcher). "
            "Usage-by-count / platoon require Statcast tracking."
        ),
    }
    out_path.write_text(json.dumps(payload, indent=2))
    n_ar = sum(1 for p in pitchers if p.get("arsenal"))
    n_ct = sum(1 for p in pitchers if p.get("usage_by_count"))
    n_pl = sum(1 for p in pitchers if p.get("platoon"))
    print(f"DONE pitchers={len(pitchers)} arsenal={n_ar} usage_counts={n_ct} platoon={n_pl}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build first-pitch swing % (FPS%) and FPS% with RISP from Statcast."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl")

OUT = Path(__file__).resolve().parent / "data"
SWING_DESC = {
    "swinging_strike",
    "swinging_strike_blocked",
    "foul",
    "foul_tip",
    "foul_bunt",
    "missed_bunt",
    "hit_into_play",
    "hit_into_play_score",
    "hit_into_play_no_out",
}
RANGES = {
    2025: ("2025-03-20", "2025-10-05"),
    2026: ("2026-03-20", "2026-07-22"),
}


def collect_ids() -> list[int]:
    ids: set[int] = set()
    for name in (
        "lidom_2025_hitting.json",
        "mlb_2026_hitting.json",
        "milb_2026_hitting.json",
    ):
        rows = json.loads((OUT / name).read_text())
        for r in rows:
            if r.get("playerId"):
                ids.add(int(r["playerId"]))
    return sorted(ids)


def fps_from_df(df) -> dict | None:
    if df is None or df.empty or "pitch_number" not in df.columns:
        return None
    fp = df[df["pitch_number"] == 1]
    if fp.empty:
        return None
    swing = fp["description"].isin(SWING_DESC)
    risp = fp[fp["on_2b"].notna() | fp["on_3b"].notna()]
    risp_swing = risp["description"].isin(SWING_DESC) if len(risp) else None
    return {
        "fps_pct": round(float(swing.mean()), 4),
        "fps_n": int(len(fp)),
        "fps_risp_pct": round(float(risp_swing.mean()), 4) if risp_swing is not None and len(risp) else None,
        "fps_risp_n": int(len(risp)),
    }


def main() -> None:
    from pybaseball import cache, statcast_batter

    cache.enable()
    ids = collect_ids()
    print(f"players to scan: {len(ids)}")
    out: dict[str, dict] = {}
    path = OUT / "fps_cache.json"
    if path.exists():
        out = json.loads(path.read_text())

    for i, pid in enumerate(ids, 1):
        key = str(pid)
        row = out.get(key) or {"player_id": pid, "seasons": {}}
        changed = False
        for year, (start, end) in RANGES.items():
            if str(year) in row.get("seasons", {}) and row["seasons"][str(year)].get("fps_n"):
                continue
            try:
                df = statcast_batter(start, end, pid)
                stats = fps_from_df(df)
            except Exception as exc:  # noqa: BLE001
                print("fail", pid, year, exc)
                stats = None
            if stats:
                row.setdefault("seasons", {})[str(year)] = {**stats, "source": f"Statcast {year}"}
                changed = True
            time.sleep(0.15)
        if changed:
            out[key] = row
        if i % 25 == 0:
            path.write_text(json.dumps(out, indent=2))
            print(f"  {i}/{len(ids)} saved ({len(out)} with any data)")
    path.write_text(json.dumps(out, indent=2))
    print("DONE", len(out))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fill INF/OF positioning codes from spray charts (coach-reviewable defaults).

Maps spray splits → PosApp slots:
  non-2K → OF PRE | 2K → OF 2K | RISP → INF RISP | non-RISP → INF EMPTY
  DP spray also informs RISP + DP rows.

Section rules (v2):
  INF — 16 lateral sections (8 left of 2B, 8 right). Each position owns a
  4-section pocket (3B / SS / 2B / 1B). Shade from pocket BIP only:
    >80% in outer-2 on one side → L-3 / R-3
    ≥80% in extreme outer-1 with ~no balls in home (middle-2) → EL-6 / ER-6
    else N. DP: 3B→H, 1B→BAG.
  OF — 9 lateral angle sections. LF/RF: L / STRAIGHT / G. CF: O / STRAIGHT / P
  (hand-aware). ≥80% of fielder-pocket balls in center+one lateral → that
  shade +10. Home empty AND >90% of all OF spray in the other 6 → +20.
  Else STRAIGHT.

Writes data/positioning_v2.json for PosApp seed load. Does not overwrite
golden Arenado/Arroyo cells when meta.golden_locked is set.
"""
from __future__ import annotations

import json
import math
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPRAY_PATH = ROOT / "data" / "pregame_spray_charts.json"
HITTERS_PATH = ROOT / "data" / "advance_opposing_hitters.json"
OUT_PATH = ROOT / "data" / "positioning_v2.json"

OF_POS = ["LF", "CF", "RF"]
OF_COUNTS = ["PRE", "2K"]
INF_POS = ["1B", "2B", "SS", "3B"]
INF_SITS = ["EMPTY", "RISP", "DP"]

# --- Geometry ---------------------------------------------------------------
# INF: equal-width x_ft bins across the dirt, 8 left of 2B + 8 right.
INF_X_MIN = -96.0
INF_X_MAX = 96.0
INF_N_SEC = 16
INF_POCKET = {
    "3B": (0, 1, 2, 3),
    "SS": (4, 5, 6, 7),
    "2B": (8, 9, 10, 11),
    "1B": (12, 13, 14, 15),
}
# Within a 4-section pocket (local 0..3 left→right): home = middle two.
INF_HOME_LOCAL = (1, 2)
INF_OUTER2_L = (0, 1)
INF_OUTER2_R = (2, 3)
INF_EXTREME_L = 0
INF_EXTREME_R = 3

# OF: equal-width spray angles foul→foul (LF negative, RF positive).
OF_ANG_MIN = -45.0
OF_ANG_MAX = 45.0
OF_N_SEC = 9
# Absolute pocket indices (left→right). Shade labels applied per position.
OF_POCKET_IDX = {
    "LF": (0, 1, 2),  # line / normal / gap
    "CF": (3, 4, 5),  # LF-side / normal / RF-side → O/P by batter hand
    "RF": (6, 7, 8),  # gap / normal / line
}

# --- Tweakable thresholds (coach dials) -------------------------------------
MIN_OF = 5          # min OF BIP (split) before shading off STRAIGHT
MIN_INF = 5         # min INF BIP (split) before preferring that split
MIN_POCKET = 3      # min BIP inside a fielder's pocket to leave N/STRAIGHT
INF_OUTER2_PCT = 0.80   # > this share in outer-2 → L-3 / R-3  (strict >)
INF_EXTREME_PCT = 0.80  # ≥ this share in outer-1 → EL-6 / ER-6
OF_LATERAL_PCT = 0.80   # ≥ this share of pocket in home+one lateral → shade-10
OF_OUTSIDE_PCT = 0.90   # > this share of ALL OF BIP outside pocket (+ empty home) → shade-20
INF_STEPS_MOD = "3"
INF_STEPS_EXT = "6"
OF_STEPS_MOD = "10"
OF_STEPS_EXT = "20"


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFD", str(name or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def angle_deg(p: dict) -> float:
    x = float(p.get("x_ft") or 0.0)
    y = float(p.get("y_ft") or 0.0)
    return math.degrees(math.atan2(x, y))


def filter_pts(
    pts,
    *,
    hand=None,
    two_k=None,
    risp=None,
    y_min=None,
    y_max=None,
    dp_only=False,
):
    out = []
    for p in pts:
        ph = str(p.get("pitcher_hand") or "").upper()[:1]
        if hand and ph != hand:
            continue
        if two_k is not None and bool(p.get("two_strikes")) != bool(two_k):
            continue
        if risp is not None and bool(p.get("risp")) != bool(risp):
            continue
        try:
            y = float(p.get("y_ft"))
        except (TypeError, ValueError):
            continue
        if y_min is not None and y < y_min:
            continue
        if y_max is not None and y > y_max:
            continue
        if dp_only:
            ev = str(p.get("event") or "")
            if "DoublePlay" not in ev and "double play" not in ev.lower():
                continue
        out.append(p)
    return out


def _bin(value: float, lo: float, hi: float, n: int) -> int:
    if hi <= lo or n <= 0:
        return 0
    if value <= lo:
        return 0
    if value >= hi:
        return n - 1
    t = (value - lo) / (hi - lo)
    return min(n - 1, max(0, int(t * n)))


def inf_section(p: dict) -> int | None:
    try:
        x = float(p["x_ft"])
    except (TypeError, ValueError, KeyError):
        return None
    return _bin(x, INF_X_MIN, INF_X_MAX, INF_N_SEC)


def of_section(p: dict) -> int | None:
    try:
        a = angle_deg(p)
    except (TypeError, ValueError):
        return None
    return _bin(a, OF_ANG_MIN, OF_ANG_MAX, OF_N_SEC)


def of_shade_labels(pos: str, bats: str) -> tuple[str, str, str]:
    """Return (left_lateral, home, right_lateral) shade codes for a pocket."""
    b = (bats or "R").upper()
    pull_positive = b.startswith("L")  # LHH pull toward RF (+)
    if pos == "LF":
        return "L", "STRAIGHT", "G"
    if pos == "RF":
        return "G", "STRAIGHT", "L"
    # CF: left section = toward LF (−), right = toward RF (+)
    if pull_positive:
        return "O", "STRAIGHT", "P"  # LHH: oppo LF-side, pull RF-side
    return "P", "STRAIGHT", "O"  # RHH: pull LF-side, oppo RF-side


def of_codes(pts, bats: str) -> dict[str, str]:
    """9-section OF pocket shade. Always returns a code per position."""
    out = {pos: "STRAIGHT" for pos in OF_POS}
    if len(pts) < MIN_OF:
        return out

    sec_counts = Counter()
    for p in pts:
        s = of_section(p)
        if s is not None:
            sec_counts[s] += 1
    total = sum(sec_counts.values())
    if total < MIN_OF:
        return out

    for pos in OF_POS:
        pocket = OF_POCKET_IDX[pos]
        left_lab, home_lab, right_lab = of_shade_labels(pos, bats)
        a, b, c = pocket
        n_a, n_b, n_c = sec_counts[a], sec_counts[b], sec_counts[c]
        in_pocket = n_a + n_b + n_c
        outside = total - in_pocket

        # Extreme: none in home AND >OF_OUTSIDE_PCT of overall spray in other 6.
        if n_b == 0 and total > 0 and (outside / total) > OF_OUTSIDE_PCT:
            # Corners: outside mass is toward CF → G-20.
            # CF: pick O/P by which absolute half holds more outside balls.
            if pos in ("LF", "RF"):
                out[pos] = f"G-{OF_STEPS_EXT}"
            else:
                left_mass = sum(sec_counts[i] for i in range(0, 4))  # LF half + CF-left
                right_mass = sum(sec_counts[i] for i in range(5, 9))
                if left_mass == 0 and right_mass == 0:
                    out[pos] = "STRAIGHT"
                elif left_mass >= right_mass:
                    out[pos] = f"{left_lab}-{OF_STEPS_EXT}"
                else:
                    out[pos] = f"{right_lab}-{OF_STEPS_EXT}"
            continue

        if in_pocket < MIN_POCKET:
            out[pos] = "STRAIGHT"
            continue

        # ≥OF_LATERAL_PCT of pocket balls in {home, one lateral} → that shade +10.
        left_band = (n_a + n_b) / in_pocket
        right_band = (n_c + n_b) / in_pocket
        # Prefer the heavier lateral when both clear the bar (rare; home-heavy).
        if left_band >= OF_LATERAL_PCT and right_band >= OF_LATERAL_PCT:
            if n_a == n_c:
                out[pos] = "STRAIGHT"
            elif n_a > n_c:
                out[pos] = (
                    f"{left_lab}-{OF_STEPS_MOD}" if left_lab != "STRAIGHT" else "STRAIGHT"
                )
            else:
                out[pos] = (
                    f"{right_lab}-{OF_STEPS_MOD}" if right_lab != "STRAIGHT" else "STRAIGHT"
                )
        elif left_band >= OF_LATERAL_PCT and n_a > 0:
            out[pos] = (
                f"{left_lab}-{OF_STEPS_MOD}" if left_lab != "STRAIGHT" else "STRAIGHT"
            )
        elif right_band >= OF_LATERAL_PCT and n_c > 0:
            out[pos] = (
                f"{right_lab}-{OF_STEPS_MOD}" if right_lab != "STRAIGHT" else "STRAIGHT"
            )
        else:
            out[pos] = "STRAIGHT"

    return out


def inf_code_from_pocket(pts, pos: str) -> str:
    """Shade one INF position from BIP inside its 4-section pocket."""
    pocket = INF_POCKET[pos]
    local_counts = [0, 0, 0, 0]
    for p in pts:
        s = inf_section(p)
        if s is None or s not in pocket:
            continue
        local_counts[pocket.index(s)] += 1
    n = sum(local_counts)
    if n < MIN_POCKET:
        return "N"

    home = sum(local_counts[i] for i in INF_HOME_LOCAL)
    outer_l = sum(local_counts[i] for i in INF_OUTER2_L)
    outer_r = sum(local_counts[i] for i in INF_OUTER2_R)
    ext_l = local_counts[INF_EXTREME_L]
    ext_r = local_counts[INF_EXTREME_R]

    # Extreme: ≥INF_EXTREME_PCT in outer-1, OR home empty with mass piled on one edge.
    if ext_l / n >= INF_EXTREME_PCT:
        return f"EL-{INF_STEPS_EXT}"
    if ext_r / n >= INF_EXTREME_PCT:
        return f"ER-{INF_STEPS_EXT}"
    if home == 0:
        if outer_l / n > INF_OUTER2_PCT and outer_l >= outer_r:
            return f"EL-{INF_STEPS_EXT}"
        if outer_r / n > INF_OUTER2_PCT:
            return f"ER-{INF_STEPS_EXT}"

    # Moderate: >INF_OUTER2_PCT of pocket in outer-2 on one side.
    if outer_l / n > INF_OUTER2_PCT and outer_l >= outer_r:
        return f"L-{INF_STEPS_MOD}"
    if outer_r / n > INF_OUTER2_PCT:
        return f"R-{INF_STEPS_MOD}"
    return "N"


def compose_inf(hold: str, steps: str) -> str:
    h = (hold or "").upper() or "N"
    if h in ("BAG", "N", "H"):
        return h
    st = str(steps or "").strip()
    return f"{h}-{st}" if st else h


def inf_code_for(pts, pos: str, sit: str, bats: str, dp_pts) -> str:
    """INF code for a situation. Always returns a non-empty code (N when neutral)."""
    if sit == "DP":
        if pos == "3B":
            return "H"
        if pos == "1B":
            return "BAG"
        use = dp_pts if len(dp_pts) >= MIN_INF else pts
        return inf_code_from_pocket(use, pos) or "N"
    use = pts
    if sit == "RISP" and len(dp_pts) >= MIN_INF:
        use = pts if len(pts) >= MIN_INF else dp_pts
    return inf_code_from_pocket(use, pos) or "N"


def of_key(team, name, pos, count, hand) -> str:
    return f"{team}|{norm_name(name)}|{pos}|{count}|{hand}"


def inf_key(team, name, pos, sit, hand) -> str:
    return f"{team}|{norm_name(name)}|{pos}|{sit}|{hand}"


def load_existing():
    if not OUT_PATH.exists():
        return {"of": {}, "inf": {}, "hands": {}, "lineups": {}, "meta": {}}
    try:
        return json.loads(OUT_PATH.read_text())
    except Exception:
        return {"of": {}, "inf": {}, "hands": {}, "lineups": {}, "meta": {}}


def main():
    spray = json.loads(SPRAY_PATH.read_text())
    synergy = {h["name"]: h for h in spray.get("synergy") or []}
    hitters = json.loads(HITTERS_PATH.read_text())["hitters"]

    existing = load_existing()
    # Only preserve coach-locked golden cells (meta.golden_locked), not prior generator output.
    locked = bool((existing.get("meta") or {}).get("golden_locked"))
    golden = set(existing.get("meta", {}).get("golden_names") or [])
    preserve_of = {}
    preserve_inf = {}
    if locked and golden:
        preserve_of = {
            k: v
            for k, v in (existing.get("of") or {}).items()
            if any(norm_name(g) in k for g in golden)
        }
        preserve_inf = {
            k: v
            for k, v in (existing.get("inf") or {}).items()
            if any(norm_name(g) in k for g in golden)
        }

    of_map: dict[str, str] = {}
    inf_map: dict[str, str] = {}
    hands: dict[str, str] = {}
    per_team = defaultdict(list)
    filled_players = []
    gaps = []

    for h in hitters:
        name = h.get("name") or ""
        team = h.get("team") or ""
        if not name or not team:
            continue
        bats = (h.get("bats") or "").upper()[:1]
        if bats in ("R", "L", "S"):
            hands[f"{team}|{norm_name(name)}"] = bats
        per_team[team].append(name)

        sp = synergy.get(name)
        pts = (sp or {}).get("points") or h.get("spray_points") or []
        if not pts:
            gaps.append({"name": name, "team": team, "reason": "no_spray_points"})
            for hand in ("L", "R"):
                for count in OF_COUNTS:
                    for pos in OF_POS:
                        of_map[of_key(team, name, pos, count, hand)] = "STRAIGHT"
                for pos in INF_POS:
                    for sit in INF_SITS:
                        if sit == "DP" and pos == "3B":
                            code = "H"
                        elif sit == "DP" and pos == "1B":
                            code = "BAG"
                        else:
                            code = "N"
                        inf_map[inf_key(team, name, pos, sit, hand)] = code
            filled_players.append(name)
            continue

        bats_eff = bats if bats in ("R", "L", "S") else "R"

        for hand in ("L", "R"):
            bats_hand = bats_eff
            if bats_eff == "S":
                bats_hand = "L" if hand == "R" else "R"

            for count in OF_COUNTS:
                two_k = count == "2K"
                of_pts = filter_pts(pts, hand=hand, two_k=two_k, y_min=140)
                if len(of_pts) < MIN_OF:
                    of_pts = filter_pts(pts, hand=hand, y_min=140)
                codes = of_codes(of_pts, bats_hand)
                for pos in OF_POS:
                    of_map[of_key(team, name, pos, count, hand)] = codes[pos]

            empty_inf = filter_pts(pts, hand=hand, risp=False, y_min=40, y_max=210)
            risp_inf = filter_pts(pts, hand=hand, risp=True, y_min=40, y_max=210)
            dp_inf = filter_pts(pts, hand=hand, y_min=40, y_max=210, dp_only=True)
            all_inf = filter_pts(pts, hand=hand, y_min=40, y_max=210)
            empty_use = empty_inf if len(empty_inf) >= MIN_INF else all_inf
            risp_use = risp_inf if len(risp_inf) >= MIN_INF else (
                dp_inf if len(dp_inf) >= MIN_INF else all_inf
            )

            for pos in INF_POS:
                for sit in INF_SITS:
                    use = empty_use if sit == "EMPTY" else risp_use
                    code = inf_code_for(use, pos, sit, bats_hand, dp_inf) or "N"
                    inf_map[inf_key(team, name, pos, sit, hand)] = code

        filled_players.append(name)

    of_map.update(preserve_of)
    inf_map.update(preserve_inf)

    golden_names = ["Nolan Arenado", "Chris Arroyo"]
    lineups = {}
    for team, names in per_team.items():
        row = list(dict.fromkeys(names))[:15]
        while len(row) < 15:
            row.append("")
        lineups[team] = row

    ex_meta = existing.get("meta") or {}
    keep_locked = locked or bool(ex_meta.get("golden_locked"))
    out = {
        "of": of_map,
        "inf": inf_map,
        "hands": hands,
        "lineups": lineups,
        "_seed": True,
        "_migratedOfV1": True,
        "meta": {
            "source": "fill_positioning_from_spray.py",
            "rules": "inf16_of9_pocket_v1",
            "golden_names": golden_names,
            "note": (
                "Defaults from spray section pockets for coach review. "
                "Arenado/Arroyo OF/INF cells locked from Safari localStorage when golden_locked=true. "
                "INF: 16 sections / 4-section pockets; >INF_OUTER2_PCT outer-2 → L/R-3; "
                "≥INF_EXTREME_PCT outer-1 (or empty home + outer pile) → EL/ER-6; else N. "
                "OF: 9 sections; ≥OF_LATERAL_PCT home+lateral → shade-10; empty home + >OF_OUTSIDE_PCT outside → shade-20. "
                "Sit map: non-2K→PRE, 2K→2K, RISP→RISP, non-RISP→EMPTY; DP: 3B=H, 1B=BAG. "
                "Tweak thresholds at top of fill_positioning_from_spray.py. "
                "Browser localStorage overrides still win on device."
            ),
            "golden_locked": keep_locked,
            "golden_source": ex_meta.get("golden_source") or (
                "safari_localStorage:santurce_positioning_v2" if keep_locked else None
            ),
            "golden_extracted_at": ex_meta.get("golden_extracted_at"),
            "players_filled": len(set(filled_players)),
            "of_cells": len(of_map),
            "inf_cells": len(inf_map),
            "gaps": gaps,
            "geometry": {
                "inf_x_range": [INF_X_MIN, INF_X_MAX],
                "inf_sections": INF_N_SEC,
                "inf_pockets": {k: list(v) for k, v in INF_POCKET.items()},
                "of_angle_range": [OF_ANG_MIN, OF_ANG_MAX],
                "of_sections": OF_N_SEC,
                "of_pockets": {k: list(v) for k, v in OF_POCKET_IDX.items()},
            },
        },
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Wrote {OUT_PATH.relative_to(ROOT)} · players={out['meta']['players_filled']} "
        f"of={len(of_map)} inf={len(inf_map)} gaps={len(gaps)}"
    )
    # Distribution summary
    of_dist = Counter(of_map.values())
    inf_dist = Counter(inf_map.values())
    print("OF dist:", dict(of_dist.most_common(12)))
    print("INF dist:", dict(inf_dist.most_common(12)))
    for g in golden_names:
        nk = norm_name(g)
        print(f"\n=== {g} (preserved={locked}) ===")
        for k in sorted(x for x in of_map if nk in x):
            print(" OF", k.split("|", 1)[1], "=>", of_map[k])
        for k in sorted(x for x in inf_map if nk in x):
            print(" INF", k.split("|", 1)[1], "=>", inf_map[k])


if __name__ == "__main__":
    main()

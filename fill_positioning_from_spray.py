#!/usr/bin/env python3
"""Fill INF/OF positioning codes from spray charts (coach-reviewable defaults).

Maps spray splits → PosApp slots:
  non-2K → OF PRE | 2K → OF 2K | RISP → INF RISP | non-RISP → INF EMPTY (blank)
  DP spray also informs RISP + DP rows.

Rules (Arenado/Arroyo style):
  - Move only for clear BIP clusters off straight-up (often pull-side).
  - OF steps default 10; 20 only for big spray skew. LF/RF: L|G only; CF: O|P only.
  - INF steps default 3; EL/ER + 6/9 when one side of the infield is empty.
  - DP: 3B often H; 1B often BAG.

Writes data/positioning_v2.json for PosApp seed load. Does not overwrite
golden_manual cells marked in the seed (reserved for coach-entered Arenado/Arroyo).
"""
from __future__ import annotations

import json
import math
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPRAY_PATH = ROOT / "data" / "pregame_spray_charts.json"
HITTERS_PATH = ROOT / "data" / "advance_opposing_hitters.json"
OUT_PATH = ROOT / "data" / "positioning_v2.json"

OF_POS = ["LF", "CF", "RF"]
OF_COUNTS = ["PRE", "2K"]
INF_POS = ["1B", "2B", "SS", "3B"]
INF_SITS = ["EMPTY", "RISP", "DP"]
# Conservative thresholds — only "big clusters" move off STRAIGHT / N.
# Angles: +x/RF is positive, LF negative (home→CF plane).
OF_MOVE_DEG = 8.0
OF_BIG_DEG = 16.0
OF_LINE_DEG = 22.0  # strong pull → L (line) instead of G (gap)
INF_MOVE_X = 18.0
INF_BIG_X = 42.0
MIN_OF = 5
MIN_INF = 5


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


def of_codes(pts, bats: str) -> dict[str, str]:
    """Shade from overall OF spray mean — pull corner / CF / oppo corner.

    Matches Excel-card habits: move for clear pull clusters; oppo corner often
    stays STRAIGHT; prefer 10 steps; 20 only for big skew. LF/RF = L|G; CF = O|P.
    """
    out = {pos: "STRAIGHT" for pos in OF_POS}
    if len(pts) < MIN_OF:
        return out
    mean = sum(angle_deg(p) for p in pts) / len(pts)
    b = (bats or "R").upper()
    pull_positive = b.startswith("L")  # LHH pull toward RF (+)
    pull_amt = mean if pull_positive else -mean  # >0 = toward pull
    pull_corner = "RF" if pull_positive else "LF"
    oppo_corner = "LF" if pull_positive else "RF"

    # CF: P toward pull, O toward oppo
    if abs(mean) >= OF_MOVE_DEG:
        shade = "P" if pull_amt > 0 else "O"
        steps = "20" if abs(mean) >= OF_BIG_DEG else "10"
        out["CF"] = f"{shade}-{steps}"

    # Pull-side corner: G for moderate pull, L for strong line pull
    if pull_amt >= OF_LINE_DEG:
        steps = "20" if pull_amt >= (OF_LINE_DEG + 8) else "10"
        out[pull_corner] = f"L-{steps}"
    elif pull_amt >= OF_MOVE_DEG:
        steps = "20" if pull_amt >= OF_BIG_DEG else "10"
        # Prefer G-10; only G-20 on big moderate-pull band
        out[pull_corner] = f"G-{steps}"

    # Oppo corner: usually STRAIGHT; shade only if spray goes opposite or
    # extreme pull leaves a CF-gap cluster on that side.
    if pull_amt <= -OF_MOVE_DEG:
        # True opposite spray
        if pull_amt <= -OF_LINE_DEG:
            steps = "20" if abs(pull_amt) >= (OF_LINE_DEG + 8) else "10"
            out[oppo_corner] = f"L-{steps}"
        else:
            steps = "20" if abs(pull_amt) >= OF_BIG_DEG else "10"
            out[oppo_corner] = f"G-{steps}"
    elif pull_amt >= (OF_BIG_DEG + 6):
        # Strong pull — light gap shade on oppo (toward CF), never 20
        out[oppo_corner] = "G-10"

    return out


def inf_hold_steps(pts, bats: str) -> tuple[str, str]:
    """Return (hold, steps) for RISP-style shade. Empty strings → no code."""
    if len(pts) < MIN_INF:
        return "N", ""
    xs = []
    for p in pts:
        try:
            xs.append(float(p["x_ft"]))
        except (TypeError, ValueError, KeyError):
            continue
    if not xs:
        return "N", ""
    xs.sort()
    med = xs[len(xs) // 2]
    left = sum(1 for x in xs if x < -20)
    right = sum(1 for x in xs if x > 20)
    # Balanced around middle → neutral
    if abs(med) < INF_MOVE_X:
        if left and right and min(left, right) / max(left, right) > 0.4:
            return "N", ""
        if abs(med) < 10:
            return "N", ""
    extreme = (left <= 1 and right >= 8) or (right <= 1 and left >= 8)
    if med <= -INF_MOVE_X:
        hold = "EL" if extreme or abs(med) >= INF_BIG_X + 10 else "L"
        if extreme or abs(med) >= INF_BIG_X + 15:
            steps = "9"
        elif abs(med) >= INF_BIG_X:
            steps = "6"
        else:
            steps = "3"
        return hold, steps
    if med >= INF_MOVE_X:
        hold = "ER" if extreme or abs(med) >= INF_BIG_X + 10 else "R"
        if extreme or abs(med) >= INF_BIG_X + 15:
            steps = "9"
        elif abs(med) >= INF_BIG_X:
            steps = "6"
        else:
            steps = "3"
        return hold, steps
    return "N", ""


def compose_inf(hold: str, steps: str) -> str:
    h = (hold or "").upper()
    if not h:
        return ""
    if h in ("BAG", "N", "H"):
        return h
    st = str(steps or "").strip()
    return f"{h}-{st}" if st else h


def inf_code_for(pts, pos: str, sit: str, bats: str, dp_pts) -> str:
    if sit == "EMPTY":
        return ""
    if sit == "DP":
        if pos == "3B":
            return "H"
        if pos == "1B":
            return "BAG"
        # Middle infield on DP: use DP spray if enough, else RISP shade
        use = dp_pts if len(dp_pts) >= MIN_INF else pts
        hold, steps = inf_hold_steps(use, bats)
        if hold == "N":
            return "N"
        return compose_inf(hold, steps)
    # RISP — prefer DP spray cluster when it is the clearer signal
    use = pts
    if len(dp_pts) >= MIN_INF:
        # Blend: if DP median agrees with RISP direction, keep RISP; else use DP
        use = pts if len(pts) >= MIN_INF else dp_pts
    hold, steps = inf_hold_steps(use, bats)
    return compose_inf(hold, steps)


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
            # Still write STRAIGHT / blank so the sheet is complete
            for hand in ("L", "R"):
                for count in OF_COUNTS:
                    for pos in OF_POS:
                        of_map[of_key(team, name, pos, count, hand)] = "STRAIGHT"
                for pos in INF_POS:
                    # EMPTY left blank (non-RISP → no entry)
                    inf_map[inf_key(team, name, pos, "RISP", hand)] = "N"
                    if pos == "3B":
                        inf_map[inf_key(team, name, pos, "DP", hand)] = "H"
                    elif pos == "1B":
                        inf_map[inf_key(team, name, pos, "DP", hand)] = "BAG"
                    else:
                        inf_map[inf_key(team, name, pos, "DP", hand)] = "N"
            filled_players.append(name)
            continue

        bats_eff = bats if bats in ("R", "L", "S") else "R"
        # Switch: vs RHP bat as L conceptually for pull? Keep as S→R default for pull side
        # (most PR switch hitters face RHP more; pull vs RHP = LHH). Handled per pitcher hand below.

        for hand in ("L", "R"):
            bats_hand = bats_eff
            if bats_eff == "S":
                # Switch: bat opposite the pitcher
                bats_hand = "L" if hand == "R" else "R"

            for count in OF_COUNTS:
                two_k = count == "2K"
                of_pts = filter_pts(pts, hand=hand, two_k=two_k, y_min=140)
                if len(of_pts) < MIN_OF:
                    # same hand any count
                    of_pts = filter_pts(pts, hand=hand, y_min=140)
                codes = of_codes(of_pts, bats_hand)
                for pos in OF_POS:
                    of_map[of_key(team, name, pos, count, hand)] = codes[pos]

            risp_inf = filter_pts(pts, hand=hand, risp=True, y_min=40, y_max=210)
            dp_inf = filter_pts(pts, hand=hand, y_min=40, y_max=210, dp_only=True)
            # DP spray also templates RISP when RISP is thin
            risp_use = risp_inf if len(risp_inf) >= MIN_INF else (
                dp_inf if len(dp_inf) >= MIN_INF else filter_pts(pts, hand=hand, y_min=40, y_max=210)
            )

            for pos in INF_POS:
                for sit in INF_SITS:
                    code = inf_code_for(risp_use, pos, sit, bats_hand, dp_inf)
                    # Store empty string only for EMPTY sit; skip writing blank EMPTY keys
                    if sit == "EMPTY":
                        continue
                    inf_map[inf_key(team, name, pos, sit, hand)] = code

        filled_players.append(name)

    # Restore golden cells on top
    of_map.update(preserve_of)
    inf_map.update(preserve_inf)

    # Mark Arenado / Arroyo as golden examples (generated, coach may overwrite in browser)
    golden_names = ["Nolan Arenado", "Chris Arroyo"]
    lineups = {}
    for team, names in per_team.items():
        # Prefer order from advance list as-is; pad to 15
        row = list(dict.fromkeys(names))[:15]
        while len(row) < 15:
            row.append("")
        lineups[team] = row

    out = {
        "of": of_map,
        "inf": inf_map,
        "hands": hands,
        "lineups": lineups,
        "_seed": True,
        "_migratedOfV1": True,
        "meta": {
            "source": "fill_positioning_from_spray.py",
            "golden_names": golden_names,
            "note": (
                "Defaults from spray clusters for coach review. "
                "Arenado/Arroyo are golden templates (generator output; browser localStorage overrides win). "
                "Set meta.golden_locked=true to freeze Arenado/Arroyo on regenerate. "
                "INF EMPTY left blank (non-RISP). OF: L/G corners, O/P CF."
            ),
            "golden_locked": False,
            "players_filled": len(set(filled_players)),
            "of_cells": len(of_map),
            "inf_cells": len(inf_map),
            "gaps": gaps,
        },
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Wrote {OUT_PATH.relative_to(ROOT)} · players={out['meta']['players_filled']} "
        f"of={len(of_map)} inf={len(inf_map)} gaps={len(gaps)}"
    )
    # Print Arenado / Arroyo snapshot
    for g in golden_names:
        nk = norm_name(g)
        print(f"\n=== {g} ===")
        for k in sorted(x for x in of_map if nk in x):
            print(" OF", k.split("|", 1)[1], "=>", of_map[k])
        for k in sorted(x for x in inf_map if nk in x):
            print(" INF", k.split("|", 1)[1], "=>", inf_map[k])


if __name__ == "__main__":
    main()

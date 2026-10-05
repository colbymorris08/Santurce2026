#!/usr/bin/env python3
"""Report remaining pitcher usage / join holes after Synergy–PS alias normalize."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP = {"UN", "KN", "EP", "UNKNOWN", "UNK"}

def main():
    data = json.loads((ROOT / "data" / "pregame_pitcher_arsenals.json").read_text())
    holes = []
    long_codes = []
    dups = []
    for p in data.get("pitchers") or []:
        name, pid = p.get("name"), p.get("player_id")
        ars = p.get("arsenal") or []
        cm = p.get("count_mix") or {}
        ubc = p.get("usage_by_count") or {}
        pl = p.get("platoon") or {}
        syn = p.get("synergy_count_pitches") or 0
        has_overall = bool(cm.get("overall") or ubc.get("overall") or ars)
        has_00 = bool(cm.get("0-0") or ubc.get("0-0"))
        has_2k = bool(cm.get("2_strikes") or ubc.get("2_strikes"))
        has_L = any(a.get("usage_l") is not None for a in ars) or bool(pl.get("vs_LHB"))
        has_R = any(a.get("usage_r") is not None for a in ars) or bool(pl.get("vs_RHB"))
        has_whiff = any(a.get("whiff") is not None for a in ars)
        has_move = any(a.get("velo") is not None or a.get("ivb") is not None for a in ars)
        issues = []
        if syn and not has_overall:
            issues.append("missing_usage_despite_synergy")
        if syn and not has_2k:
            issues.append("missing_2k_despite_synergy")
        if syn and not has_00:
            issues.append("missing_00_despite_synergy")
        if pl.get("vs_LHB") and ars and not any(a.get("usage_l") is not None for a in ars):
            issues.append("vsL_not_joined")
        if pl.get("vs_RHB") and ars and not any(a.get("usage_r") is not None for a in ars):
            issues.append("vsR_not_joined")
        if has_move and not has_whiff:
            issues.append("movement_no_whiff")
        for bucket, rows in cm.items():
            types = [str(r.get("type") or "") for r in (rows or [])]
            for t in types:
                if len(t) > 3 or t.upper() in SKIP:
                    long_codes.append((name, bucket, t))
            c = Counter(t.upper() for t in types)
            for t, n in c.items():
                if n > 1:
                    dups.append((name, bucket, t, n))
        if issues:
            holes.append({"name": name, "id": pid, "issues": issues, "syn": syn})
    print(f"pitchers={len(data.get('pitchers') or [])}")
    print(f"holes={len(holes)}")
    for h in holes:
        print(f"  {h['name']} ({h['id']}) syn={h['syn']}: {', '.join(h['issues'])}")
    print(f"long_or_junk_codes={len(long_codes)}")
    for row in long_codes[:20]:
        print("  long", row)
    print(f"duplicate_codes_in_buckets={len(dups)}")
    for row in dups[:20]:
        print("  dup", row)
    out = ROOT / "runs" / "pitch_usage_holes_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"holes": holes, "long_codes": long_codes, "dups": dups}, indent=2) + "\n")
    print(f"wrote {out}")

if __name__ == "__main__":
    main()

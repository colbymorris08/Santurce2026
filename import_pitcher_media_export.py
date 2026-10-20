#!/usr/bin/env python3
"""Import santurce_pitcher_media_export.json (site: Export uploads → JSON).

Each item is named from the pitcher page + slot:
  {lastname}_{firstname}_delivery.jpg|png
  {lastname}_{firstname}_movement.jpg|png

Writes into assets/advance/ and sets:
  arm_slot_image  ← delivery
  arsenal_image   ← movement (also accepts legacy slot "arsenal")
"""
from __future__ import annotations

import base64
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADV = ROOT / "assets" / "advance"
ARS = ROOT / "data" / "pregame_pitcher_arsenals.json"


def alpha(s: str) -> str:
    nk = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^A-Za-z]", "", "".join(c for c in nk if not unicodedata.combining(c))).lower()


def split_name(name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def decode_data_url(data_url: str) -> tuple[bytes, str]:
    header, b64 = data_url.split(",", 1)
    ext = "jpg"
    if "png" in header:
        ext = "png"
    elif "webp" in header:
        ext = "webp"
    return base64.b64decode(re.sub(r"\s+", "", b64)), ext


def normalize_kind(it: dict) -> str:
    kind = str(it.get("kind") or "").lower().strip()
    if kind in ("delivery", "movement"):
        return kind
    slot = str(it.get("slot") or "").lower().strip()
    if slot == "delivery":
        return "delivery"
    if slot in ("arsenal", "movement", "pitch_data"):
        return "movement"
    fn = str(it.get("filename") or "").lower()
    if "_delivery." in fn:
        return "delivery"
    if "_movement." in fn or "_pitch_data." in fn:
        return "movement"
    return ""


def stem_from_name(name: str, player_id: str) -> str:
    first, last = split_name(name)
    if last and first:
        return f"{alpha(last)}_{alpha(first)}"
    if last or first:
        return alpha(last or first)
    return f"id_{player_id}" if player_id else "unknown"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 import_pitcher_media_export.py ~/Downloads/santurce_pitcher_media_export.json")
        return 2
    src = Path(sys.argv[1]).expanduser()
    if not src.is_file():
        print(f"missing file: {src}")
        return 2
    doc = json.loads(src.read_text())
    items = doc.get("items") or []
    ars = json.loads(ARS.read_text())
    by_id = {str(p.get("player_id")): p for p in ars["pitchers"]}
    by_mlb = {
        str(p.get("mlb_id")): p
        for p in ars["pitchers"]
        if p.get("mlb_id") is not None
    }
    by_name = {(p.get("team"), p.get("name")): p for p in ars["pitchers"]}
    ADV.mkdir(parents=True, exist_ok=True)
    written = []
    skipped = []
    for it in items:
        pid = str(it.get("player_id") or "")
        name = it.get("name") or ""
        team = it.get("team") or ""
        row = by_id.get(pid) or by_mlb.get(pid) or by_name.get((team, name))
        if row:
            name = row.get("name") or name
            team = row.get("team") or team
        kind = normalize_kind(it)
        data_url = it.get("data_url") or ""
        if not data_url.startswith("data:image"):
            skipped.append({"player_id": pid, "reason": "no_data_url"})
            continue
        if kind not in ("delivery", "movement"):
            skipped.append({"player_id": pid, "name": name, "reason": "unknown_kind", "slot": it.get("slot")})
            continue
        raw, ext = decode_data_url(data_url)
        # Export uses *_movement; repo/site arsenal_image path keeps *_pitch_data.
        stem = stem_from_name(name, pid)
        export_fn = str(it.get("filename") or "").strip()
        m = re.match(r"^([A-Za-z0-9_.-]+?)_(delivery|movement|pitch_data)\.[A-Za-z0-9]+$", export_fn, re.I)
        if m:
            stem = alpha(m.group(1).replace("-", "_")) or stem
        suffix = "delivery" if kind == "delivery" else "pitch_data"
        fname = f"{stem}_{suffix}.{ext}"

        field = "arm_slot_image" if kind == "delivery" else "arsenal_image"
        dest = ADV / fname
        dest.write_bytes(raw)
        rel = f"assets/advance/{fname}"
        if row is not None:
            row[field] = rel
        written.append(
            {
                "player_id": pid,
                "name": name,
                "team": team,
                "kind": kind,
                "path": rel,
                "bytes": len(raw),
                "wired": row is not None,
            }
        )
        print(f"OK {team} {name} {kind} → {rel} ({len(raw)} bytes)")
    ARS.write_text(json.dumps(ars, ensure_ascii=False, indent=2) + "\n")
    report = {"written": written, "skipped": skipped, "source": str(src)}
    (ROOT / "runs" / "pitcher_media_import_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"imported {len(written)} files; skipped {len(skipped)}; arsenal JSON updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

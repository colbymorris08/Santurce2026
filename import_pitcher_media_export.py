#!/usr/bin/env python3
"""Import santurce_pitcher_media_export.json (from site Export uploads → JSON).

Writes:
  assets/advance/<lastname>_<firstname>_delivery.jpg
  assets/advance/<lastname>_<firstname>_pitch_data.jpg  (arsenal slot)
Updates pregame_pitcher_arsenals.json arm_slot_image / arsenal_image paths.
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


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 import_pitcher_media_export.py ~/Downloads/santurce_pitcher_media_export.json")
        return 2
    src = Path(sys.argv[1]).expanduser()
    doc = json.loads(src.read_text())
    items = doc.get("items") or []
    ars = json.loads(ARS.read_text())
    by_id = {str(p.get("player_id")): p for p in ars["pitchers"]}
    by_name = {(p.get("team"), p.get("name")): p for p in ars["pitchers"]}
    ADV.mkdir(parents=True, exist_ok=True)
    written = []
    for it in items:
        pid = str(it.get("player_id") or "")
        slot = str(it.get("slot") or "")
        name = it.get("name") or ""
        team = it.get("team") or ""
        row = by_id.get(pid) or by_name.get((team, name))
        if row:
            name = row.get("name") or name
            team = row.get("team") or team
        first, last = split_name(name)
        stem = f"{alpha(last)}_{alpha(first)}" if last else alpha(name) or pid
        data_url = it.get("data_url") or ""
        if not data_url.startswith("data:image"):
            continue
        raw, ext = decode_data_url(data_url)
        if slot == "delivery":
            fname = f"{stem}_delivery.{ext}"
            field = "arm_slot_image"
        elif slot == "arsenal":
            fname = f"{stem}_pitch_data.{ext}"
            field = "arsenal_image"
        else:
            fname = f"{stem}_{slot}.{ext}"
            field = None
        dest = ADV / fname
        dest.write_bytes(raw)
        rel = f"assets/advance/{fname}"
        if row is not None and field:
            row[field] = rel
        written.append({"player_id": pid, "name": name, "team": team, "slot": slot, "path": rel, "bytes": len(raw)})
        print(f"OK {team} {name} {slot} → {rel} ({len(raw)} bytes)")
    ARS.write_text(json.dumps(ars, ensure_ascii=False, indent=2) + "\n")
    (ROOT / "runs" / "pitcher_media_import_report.json").write_text(json.dumps({"written": written}, indent=2) + "\n")
    print(f"imported {len(written)} files; arsenal JSON updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

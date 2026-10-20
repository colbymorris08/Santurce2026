#!/usr/bin/env python3
"""Download LowHome / HighHome / normal (behind-pitcher) clips into Downloads/final additions.

One folder per pitcher:
  ~/Downloads/final additions/<TEAM>_<LAST><FIRST>/
    LowHome.mp4   — hitter-view low home (preferred)
    HighHome.mp4  — hitter-view high home
    normal.mp4    — CF / pitcher-facing / AsLogged fallback

Targets: card-roster pitchers with no repo delivery snapshot (arm_slot_image),
except Dereck Rodriguez who already has assets/advance/rodriguez_arm_slot.png.
Prefers LBPRC games when available.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PITCH_TIPS = Path("/Users/colbymorris/apexstats/pitch-tips")
OUT = Path.home() / "Downloads" / "final additions"
CKPT = ROOT / "runs" / "final_additions_video_checkpoint.json"
LOG = ROOT / "runs" / "final_additions_video_run.log"
REPORT = ROOT / "runs" / "final_additions_video_report.json"

TEAM_FOLDER = {
    "CAR": "CAROLINA",
    "SAN": "SANTURCE",
    "MAY": "MAYAGUEZ",
    "PON": "PONCE",
    "CAG": "CAGUAS",
    "SJU": "SANJUAN",
}
LEAGUE_LBPRC = "5dd2cbcc4b8b50a3e8e46172"
PR_MAX_SCAN = int(os.environ.get("FINAL_ADD_PR_SCAN", "4000"))
PR_MAX_KEEP = int(os.environ.get("FINAL_ADD_PR_KEEP", "600"))
FALLBACK_MAX = int(os.environ.get("FINAL_ADD_FALLBACK_MAX", "900"))

ANGLE_FILES = {
    "lowhome": "LowHome.mp4",
    "highhome": "HighHome.mp4",
    "normal": "normal.mp4",
}


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def alpha_upper(s: str) -> str:
    return re.sub(r"[^A-Za-z]", "", strip_accents(s or "")).upper()


def folder_name(team: str, first: str, last: str) -> str:
    return f"{TEAM_FOLDER.get(team, team)}_{alpha_upper(last)}{alpha_upper(first)}"


def split_name(name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def track_angle(track: dict) -> str:
    return str(track.get("videoCameraAngle") or track.get("cameraAngle") or "").strip()


def track_playable(track: dict) -> tuple[str | None, str]:
    for seg in track.get("segments") or []:
        video = seg.get("video") or {}
        asset_id = str(video.get("id") or "")
        for var in video.get("videoVariants") or []:
            path = var.get("endpointRelativePath") or var.get("url")
            if path:
                return str(path).replace("http://", "https://"), asset_id
        if (video.get("videoComponents") or []) and asset_id:
            return f"https://asset.synergysportstech.com/{asset_id}/hls/master.m3u8", asset_id
    return None, ""


def classify_angle(angle: str) -> str | None:
    a = (angle or "").strip().lower()
    if a.startswith("lowhome"):
        return "lowhome"
    if a.startswith("highhome"):
        return "highhome"
    # Behind-pitcher / face-on to pitcher from CF / open / logged
    if a.startswith("cf") or a in {"aslogged", "broadcast"} or "open" in a:
        return "normal"
    return None


def is_pr(ev: dict, bas=None) -> bool:
    game = ev.get("game") or {}
    league = game.get("league") or {}
    lid = ""
    if bas is not None:
        try:
            lid = str(bas.event_league_id(ev) or "")
        except Exception:
            lid = ""
    if not lid:
        lid = str(league.get("id") or "")
    ab = str(league.get("nameAbbrev") or game.get("leagueName") or "").upper()
    return lid == LEAGUE_LBPRC or ab in {"LBPRC", "LBPR", "PR"} or "PUERTO" in ab


def download_url(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 10_000:
        return
    tmp = dest.with_suffix(".part.mp4")
    tmp.unlink(missing_ok=True)
    if str(url).endswith(".m3u8") or "/hls/" in str(url):
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", url, "-t", "12", "-c", "copy", "-movflags", "+faststart", str(tmp),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 10_000:
            raise RuntimeError(f"HLS failed: {(r.stderr or '')[-200:]}")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "SanturceFinalAdd/1.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            tmp.write_bytes(resp.read())
        if not tmp.is_file() or tmp.stat().st_size < 10_000:
            raise RuntimeError("download too small")
    tmp.replace(dest)


def collect_best(events: list[dict]) -> dict[str, dict]:
    """Best candidate per angle class (prefer PR, then newer date)."""
    best: dict[str, dict] = {}
    for ev in events:
        game = ev.get("game") or {}
        league = ((game.get("league") or {}).get("nameAbbrev") or game.get("leagueName") or "")
        pr = 0 if is_pr(ev) else 1
        date = str(game.get("gameDate") or "")
        for track in ((ev.get("videoGroup") or {}).get("videoTracks")) or []:
            angle = track_angle(track)
            kind = classify_angle(angle)
            if not kind:
                continue
            url, asset_id = track_playable(track)
            if not url:
                continue
            cand = {
                "url": url,
                "asset_id": asset_id,
                "angle": angle,
                "league": league,
                "game_date": date,
                "pr": pr,
                "event": ev,
            }
            prev = best.get(kind)
            # Prefer PR (pr=0), then newer game date.
            if prev is None or pr < prev["pr"] or (
                pr == prev["pr"] and date > (prev.get("game_date") or "")
            ):
                best[kind] = cand
    return best


def has_release_picture(team: str, name: str, player_id, ars_row: dict, rod: dict) -> bool:
    """True if repo already has a delivery / release still for this pitcher.

    Do NOT match bare <lastname>_arm_slot.* — that false-positives every Rodriguez
    against Dereck's shared rodriguez_arm_slot.png. Prefer arsenal arm_slot_image
    path, then first_last / last_first delivery stems, then player_id stems.
    """
    if ars_row.get("arm_slot_image"):
        path = ROOT / str(ars_row["arm_slot_image"])
        if path.is_file() and path.stat().st_size > 1000:
            return True
    if name in ("Dereck Rodriguez", "Dereck Rodríguez") and rod.get("arm_slot_image"):
        path = ROOT / str(rod["arm_slot_image"])
        if path.is_file() and path.stat().st_size > 1000:
            return True
    first = alpha_upper(split_name(name)[0])
    last = alpha_upper(split_name(name)[1] or name)
    fl = f"{first.lower()}_{last.lower()}"
    lf = f"{last.lower()}_{first.lower()}"
    adv = ROOT / "assets" / "advance"
    candidates = [
        adv / f"{lf}_delivery.jpg",
        adv / f"{lf}_delivery.png",
        adv / f"{fl}_delivery.jpg",
        adv / f"{fl}_delivery.png",
        adv / f"{lf}_arm_slot.jpg",
        adv / f"{lf}_arm_slot.png",
        adv / f"{fl}_arm_slot.jpg",
        adv / f"{fl}_arm_slot.png",
        # Bare lastname delivery only (not arm_slot) — still ambiguous for common names
        # but useful when uniquely named (e.g. zabala_delivery.jpg).
        adv / f"{last.lower()}_delivery.jpg",
        adv / f"{last.lower()}_delivery.png",
    ]
    if player_id is not None:
        candidates += [
            adv / f"{player_id}_delivery.jpg",
            adv / f"{player_id}_delivery.png",
            adv / f"{player_id}_arm_slot.jpg",
            adv / f"{player_id}_arm_slot.png",
        ]
    return any(p.is_file() and p.stat().st_size > 1000 for p in candidates)


def load_targets() -> list[dict]:
    cards = json.loads((ROOT / "data" / "pregame_card_stats.json").read_text())
    ars = { (p.get("team"), p.get("name")): p for p in json.loads((ROOT / "data" / "pregame_pitcher_arsenals.json").read_text())["pitchers"] }
    audit = { p["name"]: p for p in json.loads((ROOT / "data" / "roster_2026_27_synergy_audit.json").read_text())["players"] }
    opp = { p["name"]: p for p in json.loads((ROOT / "data" / "advance_opposing_pitchers.json").read_text()).get("pitchers") or [] }
    rod = json.loads((ROOT / "data" / "advance_rodriguez.json").read_text())

    out = []
    skipped_have_pic = 0
    for p in cards["players"]:
        if p.get("role") != "P":
            continue
        key = (p.get("team"), p.get("name"))
        row = ars.get(key) or {}
        if has_release_picture(p.get("team") or "", p["name"], p.get("id"), row, rod):
            skipped_have_pic += 1
            continue
        first, last = split_name(p["name"])
        sid = row.get("synergy_id") or (audit.get(p["name"]) or {}).get("synergy_id") or (opp.get(p["name"]) or {}).get("synergy_id")
        out.append({
            "name": p["name"],
            "team": p["team"],
            "first": first,
            "last": last,
            "folder": folder_name(p["team"], first, last),
            "synergy_id": str(sid) if sid else None,
            "player_id": p.get("id"),
        })
    log(f"skip_have_release_pic={skipped_have_pic}")
    return sorted(out, key=lambda r: (r["team"], r["last"], r["first"]))


def save_ckpt(ckpt: dict) -> None:
    CKPT.parent.mkdir(parents=True, exist_ok=True)
    CKPT.write_text(json.dumps(ckpt, indent=2) + "\n")


def resolve_synergy_id(bas, token: str, player: dict) -> str | None:
    if player.get("synergy_id"):
        return str(player["synergy_id"])
    first, last = player.get("first") or "", (player.get("last") or "").split()[-1:]
    last = last[0] if last else ""
    if not last:
        return None
    try:
        doc = bas.api_get(
            "/api/players/search",
            token,
            params={"firstName": first or player["name"].split()[0], "lastName": last, "sportId": bas.SPORT_ID_BASEBALL},
        )
    except Exception:
        return None
    rows = (doc or {}).get("result") or []
    mlb = player.get("player_id")
    for row in rows:
        mid = row.get("mlbProfileId") or row.get("ids")
        try:
            if mlb and int(mid or 0) == int(mlb):
                return str(row["id"])
        except (TypeError, ValueError):
            pass
    want = strip_accents(player["name"]).lower()
    exact = [r for r in rows if strip_accents(str(r.get("name") or "")).lower() == want]
    if len(exact) == 1:
        return str(exact[0]["id"])
    if len(rows) == 1:
        return str(rows[0]["id"])
    return None


def process(bas, token: str, player: dict, ckpt: dict) -> str:
    folder = OUT / player["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    have = {k: (folder / fn).is_file() and (folder / fn).stat().st_size > 10_000 for k, fn in ANGLE_FILES.items()}
    if all(have.values()):
        ckpt.setdefault("done", {})[player["folder"]] = {"status": "skip_complete", **{k: have[k] for k in ANGLE_FILES}}
        save_ckpt(ckpt)
        return "skip"

    sid = resolve_synergy_id(bas, token, player)
    player["synergy_id"] = sid
    if not sid:
        ckpt.setdefault("failed", {})[player["folder"]] = {"error": "no_synergy_id", "name": player["name"]}
        save_ckpt(ckpt)
        return "fail"

    log(f"  scanning LBPRC…")
    # Keep enough PR pitches to find home angles, but don't walk 4k when we already have them.
    events = bas.fetch_events_pages(
        token=token,
        pitcher_id=sid,
        max_events=min(PR_MAX_KEEP, 250),
        take=200,
        pause_s=0.04,
        timeout_s=120,
        max_attempts=2,
        max_scan=min(PR_MAX_SCAN, 1800),
        retain=lambda ev: is_pr(ev, bas),
    )
    best = collect_best(events)
    if "lowhome" not in best or "highhome" not in best or "normal" not in best:
        # One more PR page window if something is still missing
        if len(events) >= 200 and not all(k in best for k in ("lowhome", "highhome", "normal")):
            more = bas.fetch_events_pages(
                token=token,
                pitcher_id=sid,
                max_events=400,
                take=200,
                pause_s=0.04,
                timeout_s=120,
                max_attempts=2,
                max_scan=3200,
                retain=lambda ev: is_pr(ev, bas),
            )
            for k, v in collect_best(more).items():
                if k not in best:
                    best[k] = v
    if not any(k in best for k in ("lowhome", "highhome")):
        log("  no PR home — any league")
        events2 = bas.fetch_events_pages(
            token=token,
            pitcher_id=sid,
            max_events=min(FALLBACK_MAX, 400),
            take=200,
            pause_s=0.04,
            timeout_s=120,
            max_attempts=2,
            max_scan=1200,
        )
        best2 = collect_best(events2)
        for k, v in best2.items():
            if k not in best:
                best[k] = v
    log(f"  angles found: {sorted(best.keys())}")

    got = {}
    for kind, fname in ANGLE_FILES.items():
        dest = folder / fname
        if dest.is_file() and dest.stat().st_size > 10_000:
            got[kind] = {"path": str(dest), "kept": True}
            continue
        cand = best.get(kind)
        if not cand:
            continue
        try:
            download_url(cand["url"], dest)
            got[kind] = {
                "path": str(dest),
                "angle": cand["angle"],
                "league": cand["league"],
                "game_date": cand["game_date"],
                "bytes": dest.stat().st_size,
            }
            log(f"  OK {kind} {cand['angle']} {cand['league']} {cand['game_date']} → {fname}")
        except Exception as exc:  # noqa: BLE001
            log(f"  FAIL {kind}: {exc}")

    # If no LowHome but HighHome exists, that's still hitter view — leave LowHome empty.
    # If no normal but we have something else, don't invent.

    record = {
        "name": player["name"],
        "team": player["team"],
        "synergy_id": sid,
        "clips": got,
        "missing": [k for k in ANGLE_FILES if k not in got],
    }
    if got:
        ckpt.setdefault("done", {})[player["folder"]] = record
        ckpt.get("failed", {}).pop(player["folder"], None)
        save_ckpt(ckpt)
        return "ok" if not record["missing"] else "partial"
    ckpt.setdefault("failed", {})[player["folder"]] = {"error": "no_clips", "name": player["name"]}
    save_ckpt(ckpt)
    return "fail"


def main() -> int:
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PITCH_TIPS / ".pw-browsers"))

    sys.path[:0] = [str(ROOT), str(PITCH_TIPS / "cv")]
    import build_advance_synergy as bas

    OUT.mkdir(parents=True, exist_ok=True)
    targets = load_targets()
    only = os.environ.get("FINAL_ADD_TEAMS", "").upper().split(",")
    only = [t.strip() for t in only if t.strip()]
    if only:
        targets = [t for t in targets if t["team"] in only]
    ckpt = json.loads(CKPT.read_text()) if CKPT.is_file() else {"done": {}, "failed": {}}
    log(f"targets={len(targets)} out={OUT}")

    token = bas.login_token()
    counts = defaultdict(int)
    t0 = time.time()
    for i, player in enumerate(targets, 1):
        done = (ckpt.get("done") or {}).get(player["folder"]) or {}
        clips = done.get("clips") or {}
        if done.get("status") == "skip_complete" or (
            isinstance(clips, dict)
            and all((OUT / player["folder"] / ANGLE_FILES[k]).is_file() for k in ANGLE_FILES)
        ):
            counts["skip"] += 1
            continue
        log(f"[{i}/{len(targets)}] {player['team']} {player['name']} → {player['folder']}")
        try:
            status = process(bas, token, player, ckpt)
            counts[status] += 1
        except Exception as exc:  # noqa: BLE001
            counts["fail"] += 1
            log(f"  FAIL {type(exc).__name__}: {exc}")
            ckpt.setdefault("failed", {})[player["folder"]] = {"error": str(exc), "name": player["name"]}
            save_ckpt(ckpt)

    summary = {
        "out": str(OUT),
        "targets": len(targets),
        **dict(counts),
        "done": len(ckpt.get("done") or {}),
        "failed": len(ckpt.get("failed") or {}),
        "elapsed_min": round((time.time() - t0) / 60, 2),
    }
    REPORT.write_text(json.dumps(summary, indent=2) + "\n")
    log(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

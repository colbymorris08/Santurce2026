#!/usr/bin/env python3
"""Download Synergy clips for PR roster pitchers (hitter perspective when possible).

Rules:
- Prefer Puerto Rico (LBPRC) games first — many affiliates' newest clips are MiLB AsLogged
  with no home angle; guys who've pitched in LBPRC usually have LowHome/HighHome there.
- Within a league: LowHome > HighHome > any other playable angle.
- One clip per pitcher: STEM.mp4 under ~/Downloads/PR video/<TEAM>/
- Pitchers only. Resumes; upgrades non-PR / AsLogged clips when PR video exists.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PITCH_TIPS = Path("/Users/colbymorris/apexstats/pitch-tips")
OUT_DIR = Path.home() / "Downloads" / "PR video"
CKPT = ROOT / "runs" / "pr_hitter_view_video_checkpoint.json"
REPORT = ROOT / "runs" / "pr_hitter_view_video_report.json"
LOG = ROOT / "runs" / "pr_hitter_view_video_run.log"

TEAM_FOLDER = {
    "CAR": "CAROLINA",
    "SAN": "SANTURCE",
    "MAY": "MAYAGUEZ",
    "PON": "PONCE",
    "CAG": "CAGUAS",
    "SJU": "SANJUAN",
}

# Synergy LBPRC league id (Puerto Rico winter league).
LEAGUE_LBPRC = "5dd2cbcc4b8b50a3e8e46172"
PR_LEAGUE_ABBREVS = {"LBPRC", "LBPR", "PR"}

MAX_EVENTS = int(os.environ.get("PR_VIDEO_MAX_EVENTS", "900"))
# Walk past recent MiLB seasons to reach older LBPRC LowHome.
PR_MAX_SCAN = int(os.environ.get("PR_VIDEO_PR_MAX_SCAN", "5000"))
PR_MAX_KEEP = int(os.environ.get("PR_VIDEO_PR_MAX_KEEP", "800"))
PAGE_TAKE = 200
CLIPS_PER_PLAYER = 1  # one game is enough


def log(msg: str) -> None:
    # stdout only — callers redirect to LOG via nohup/tee (avoid double lines).
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def alpha_upper(s: str) -> str:
    return re.sub(r"[^A-Za-z]", "", strip_accents(s or "")).upper()


def is_lowhome_angle(angle: str) -> bool:
    return (angle or "").strip().lower().startswith("lowhome")


def is_highhome_angle(angle: str) -> bool:
    return (angle or "").strip().lower().startswith("highhome")


def is_pr_league(league: str | None, league_id: str | None = None) -> bool:
    if league_id and str(league_id) == LEAGUE_LBPRC:
        return True
    ab = (league or "").strip().upper()
    if ab in PR_LEAGUE_ABBREVS:
        return True
    return "PUERTO" in ab or "LBPRC" in ab


def event_is_pr(ev: dict[str, Any], bas=None) -> bool:
    game = ev.get("game") or {}
    league = game.get("league") or {}
    lid = ""
    if bas is not None:
        try:
            lid = str(bas.event_league_id(ev) or "")
        except Exception:
            lid = ""
    if not lid:
        lid = str(league.get("id") or game.get("leagueId") or "")
    abbrev = str(league.get("nameAbbrev") or game.get("leagueName") or "")
    return is_pr_league(abbrev, lid)


def angle_rank(angle: str) -> int:
    """Lower is better. LowHome > HighHome > everything else with video."""
    a = (angle or "").strip().lower()
    if a.startswith("lowhome"):
        return 0
    if a.startswith("highhome"):
        return 1
    if a:
        return 2
    return 9


def league_rank(league: str | None) -> int:
    """Lower is better. LBPRC first, then MLB (front views), then everything else."""
    if is_pr_league(league):
        return 0
    ab = (league or "").strip().upper()
    if ab in {"MLB", "MLBS"}:
        return 1
    return 2


def clip_sort_key(cand: dict[str, Any]) -> tuple:
    return (
        int(cand.get("league_rank") if cand.get("league_rank") is not None else league_rank(cand.get("league"))),
        int(cand.get("rank") if cand.get("rank") is not None else angle_rank(str(cand.get("angle") or ""))),
        # newer dates first within tier (invert via reverse sort later — use negative-ish)
        str(cand.get("game_date") or ""),
    )


def split_name(name: str, last_hint: str | None = None) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    if last_hint:
        lh = strip_accents(str(last_hint)).lower()
        joined = strip_accents(" ".join(parts[1:])).lower()
        if lh and (joined == lh or joined.endswith(lh) or lh in joined):
            return parts[0], " ".join(parts[1:])
    return parts[0], " ".join(parts[1:])


def team_dir_name(team_abbr: str) -> str:
    return TEAM_FOLDER.get(str(team_abbr).upper(), alpha_upper(str(team_abbr)))


def file_stem(team_abbr: str, first: str, last: str) -> str:
    team = team_dir_name(team_abbr)
    return f"{team}_{alpha_upper(last)}{alpha_upper(first)}"


def team_out_dir(team_abbr: str) -> Path:
    d = OUT_DIR / team_dir_name(team_abbr)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_players() -> list[dict[str, Any]]:
    base = ROOT / "data"
    cards = json.loads((base / "pregame_card_stats.json").read_text())
    ars = {p["name"]: p for p in json.loads((base / "pregame_pitcher_arsenals.json").read_text())["pitchers"]}
    opp_p = {p["name"]: p for p in json.loads((base / "advance_opposing_pitchers.json").read_text())["pitchers"]}
    opp_h = {h["name"]: h for h in json.loads((base / "advance_opposing_hitters.json").read_text())["hitters"]}
    audit = {p["name"]: p for p in json.loads((base / "roster_2026_27_synergy_audit.json").read_text())["players"]}
    by_sid: dict[str, dict[str, Any]] = {}

    def add(name: str, team: str, kind: str, sid: str | None, last_hint: str | None = None) -> None:
        if not sid or not name or not team:
            return
        key = str(sid)
        if key in by_sid:
            return
        first, last = split_name(name, last_hint)
        by_sid[key] = {
            "name": name,
            "team": str(team).upper(),
            "team_folder": team_dir_name(team),
            "kind": kind,
            "synergy_id": key,
            "first": first,
            "last": last,
            "stem": file_stem(team, first, last),
        }

    for p in cards["players"]:
        name = p["name"]
        role = p.get("role") or p.get("roster_pos") or ""
        kind = "pitcher" if role == "P" else "hitter"
        sid = None
        for src in (ars.get(name), opp_p.get(name), opp_h.get(name), audit.get(name)):
            if src and src.get("synergy_id"):
                sid = str(src["synergy_id"])
                break
        add(name, p.get("team"), kind, sid, p.get("last"))
    for name, p in audit.items():
        kind = "pitcher" if (p.get("kind") or "") == "pitcher" else "hitter"
        add(name, p.get("team"), kind, p.get("synergy_id"))
    rows = sorted(by_sid.values(), key=lambda r: (r["team"], r["last"], r["first"], r["name"]))
    # Pitcher clips only (LowHome hitter-perspective of the pitcher).
    return [r for r in rows if r.get("kind") == "pitcher"]


def load_ckpt() -> dict[str, Any]:
    if CKPT.is_file():
        return json.loads(CKPT.read_text())
    return {"done": {}, "failed": {}, "partial": {}}


def save_ckpt(ckpt: dict[str, Any]) -> None:
    CKPT.parent.mkdir(parents=True, exist_ok=True)
    CKPT.write_text(json.dumps(ckpt, indent=2) + "\n")


def track_angle(track: dict[str, Any]) -> str:
    return str(track.get("videoCameraAngle") or track.get("cameraAngle") or "").strip()


def track_playable(track: dict[str, Any]) -> tuple[str | None, str]:
    """Return (url, asset_id) for the first playable segment on a track."""
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


def best_url_from_event(ev: dict[str, Any]) -> tuple[str | None, str, str]:
    """Best playable track on an event: LowHome > HighHome > other."""
    best: tuple[int, str | None, str, str] | None = None  # rank, url, asset, angle
    for track in ((ev.get("videoGroup") or {}).get("videoTracks")) or []:
        angle = track_angle(track)
        url, asset_id = track_playable(track)
        if not url:
            continue
        rank = angle_rank(angle)
        if best is None or rank < best[0]:
            best = (rank, url, asset_id, angle)
            if rank == 0:
                break
    if not best:
        return None, "", ""
    return best[1], best[2], best[3]


def collect_clips_by_game(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        url, asset_id, angle = best_url_from_event(ev)
        if not url:
            continue
        game = ev.get("game") or {}
        gid = str(game.get("id") or "")
        if not gid:
            continue
        league = ((game.get("league") or {}).get("nameAbbrev") or game.get("leagueName"))
        by[gid].append(
            {
                "event": ev,
                "url": url,
                "asset_id": asset_id,
                "angle": angle,
                "game_id": gid,
                "game_date": game.get("gameDate"),
                "event_id": str(ev.get("id") or ""),
                "league": league,
                "rank": angle_rank(angle),
                "league_rank": league_rank(str(league) if league else None),
                "is_pr": event_is_pr(ev),
            }
        )
    for gid, rows in by.items():
        # Best angle within game; PR preference applied when ordering games.
        rows.sort(key=lambda r: (int(r.get("rank") or 9), str(r.get("game_date") or "")))
    return by


def download_url(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 10_000:
        return
    if str(url).endswith(".m3u8") or "/hls/" in str(url):
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", url, "-t", "10", "-c", "copy", "-movflags", "+faststart", str(dest),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not dest.is_file() or dest.stat().st_size < 10_000:
            raise RuntimeError(f"HLS failed: {(r.stderr or '')[-300:]}")
        return
    req = urllib.request.Request(url, headers={"User-Agent": "SanturcePRVideo/2.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        dest.write_bytes(resp.read())
    if not dest.is_file() or dest.stat().st_size < 10_000:
        raise RuntimeError("download too small")


def dest_paths(stem: str, team_abbr: str | None = None) -> list[Path]:
    """Write under PR video/<TEAM>/STEM.mp4 (optional legacy _2 kept if present)."""
    if team_abbr:
        base = team_out_dir(team_abbr)
    else:
        prefix = stem.split("_", 1)[0] if "_" in stem else ""
        base = (OUT_DIR / prefix) if prefix else OUT_DIR
        base.mkdir(parents=True, exist_ok=True)
    return [base / f"{stem}.mp4", base / f"{stem}_2.mp4"]


def good_clip(c: dict[str, Any]) -> bool:
    path = Path(str(c.get("path") or ""))
    return path.is_file() and path.stat().st_size > 10_000 and bool(c.get("game_id"))


def clip_is_pr_home(c: dict[str, Any]) -> bool:
    """PR game with LowHome or HighHome — what we want for league veterans."""
    if not good_clip(c):
        return False
    if not is_pr_league(str(c.get("league") or "")):
        return False
    ang = str(c.get("angle") or "")
    return is_lowhome_angle(ang) or is_highhome_angle(ang)


def player_complete(record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    clips = [c for c in (record.get("clips") or []) if good_clip(c)]
    if not clips:
        return False
    # Non-PR / AsLogged counts as incomplete so we can upgrade to LBPRC LowHome.
    if record.get("needs_pr_upgrade"):
        return False
    if any(clip_is_pr_home(c) for c in clips):
        return True
    # Accept any PR clip, or a confirmed LowHome from elsewhere (e.g. MLB).
    c0 = clips[0]
    if is_pr_league(str(c0.get("league") or "")):
        return True
    if is_lowhome_angle(str(c0.get("angle") or "")):
        return True
    # Affiliate AsLogged without PR attempt → incomplete
    if record.get("pr_scanned"):
        return True  # already hunted PR; keep whatever we have
    return False


def purge_mislabeled(ckpt: dict[str, Any]) -> int:
    """Drop checkpoint rows whose files are missing/corrupt. Keep any playable angle."""
    removed = 0
    done = ckpt.get("done") or {}
    for sid, row in list(done.items()):
        clips = row.get("clips") or []
        keep = [c for c in clips if good_clip(c)]
        if keep:
            row["clips"] = keep[:CLIPS_PER_PLAYER] if CLIPS_PER_PLAYER == 1 else keep
            row["angle"] = keep[0].get("angle") or row.get("angle")
            row["confirmed_lowhome"] = is_lowhome_angle(str(row["angle"] or ""))
            done[sid] = row
        else:
            path = Path(str(row.get("path") or ""))
            if path.is_file() and path.stat().st_size > 10_000:
                # legacy single-path row
                continue
            done.pop(sid, None)
            removed += 1
    ckpt["done"] = done
    save_ckpt(ckpt)
    return removed


def fetch_events(bas, token: str, player: dict[str, Any], *, pr_only: bool = False) -> list[dict]:
    """Fetch pitcher/batter events. pr_only walks deeper and keeps LBPRC rows only."""
    id_kw = (
        {"pitcher_id": player["synergy_id"]}
        if player["kind"] == "pitcher"
        else {"batter_id": player["synergy_id"]}
    )
    if pr_only:
        return bas.fetch_events_pages(
            token=token,
            **id_kw,
            max_events=PR_MAX_KEEP,
            take=PAGE_TAKE,
            pause_s=0.05,
            timeout_s=120,
            max_attempts=2,
            max_scan=PR_MAX_SCAN,
            retain=lambda ev: event_is_pr(ev, bas),
        )
    return bas.fetch_events_pages(
        token=token,
        **id_kw,
        max_events=MAX_EVENTS,
        take=PAGE_TAKE,
        pause_s=0.06,
        timeout_s=120,
        max_attempts=2,
        max_scan=MAX_EVENTS * 2,
    )


def download_clip(cand: dict[str, Any], dest: Path) -> dict[str, Any]:
    url, asset_id, angle = best_url_from_event(cand["event"])
    if not url:
        # fall back to pre-resolved fields from collect
        url, asset_id, angle = cand.get("url"), cand.get("asset_id") or "", cand.get("angle") or ""
    if not url:
        raise RuntimeError("no playable url on event")
    tmp = dest.with_suffix(".part.mp4")
    tmp.unlink(missing_ok=True)
    try:
        download_url(str(url), tmp)
    except Exception:
        if not asset_id:
            raise
        download_url(f"https://asset.synergysportstech.com/{asset_id}/hls/master.m3u8", tmp)
    tmp.replace(dest)
    return {
        "path": str(dest),
        "angle": angle,
        "game_id": cand["game_id"],
        "game_date": cand.get("game_date"),
        "event_id": cand.get("event_id"),
        "league": cand.get("league"),
        "bytes": dest.stat().st_size,
        "confirmed_lowhome": is_lowhome_angle(str(angle or "")),
    }


def _ordered_game_ids(by_game: dict[str, list[dict[str, Any]]]) -> list[str]:
    """LBPRC first, then MLB, then other; within that LowHome > HighHome > other; newest date."""

    def key(gid: str) -> tuple:
        row = by_game[gid][0]
        # Invert date so newer sorts lower (better) within the same ranks.
        date = str(row.get("game_date") or "")
        return (
            int(row.get("league_rank") if row.get("league_rank") is not None else 9),
            int(row.get("rank") if row.get("rank") is not None else 9),
            # MM/DD/YYYY → comparable; missing dates last
            tuple(-int(x) for x in date.split("/")) if date.count("/") == 2 else (1,),
        )

    return sorted(by_game.keys(), key=key)


def _try_download_from_games(
    by_game: dict[str, list[dict[str, Any]]],
    paths: list[Path],
) -> list[dict[str, Any]]:
    clips: list[dict[str, Any]] = []
    for gid in _ordered_game_ids(by_game):
        if len(clips) >= CLIPS_PER_PLAYER:
            break
        dest = paths[len(clips)]
        # Replace prior affiliate file in place.
        dest.unlink(missing_ok=True)
        try:
            meta = download_clip(by_game[gid][0], dest)
        except Exception as exc:  # noqa: BLE001
            log(f"  retry next game after {type(exc).__name__}: {exc}")
            continue
        clips.append(meta)
        log(
            f"  OK {meta.get('angle')} game={meta.get('game_date')} league={meta.get('league')} "
            f"→ {dest.name} ({meta['bytes']} bytes)"
        )
    return clips


def process_player(bas, token: str, player: dict[str, Any], ckpt: dict[str, Any]) -> str:
    sid = player["synergy_id"]
    stem = player["stem"]
    prev = (ckpt.get("done") or {}).get(sid) or {}
    existing = [c for c in (prev.get("clips") or []) if good_clip(c)]
    paths = dest_paths(stem, player.get("team"))

    if existing and any(clip_is_pr_home(c) for c in existing):
        record = {
            "name": player["name"],
            "stem": stem,
            "team": player["team"],
            "kind": player["kind"],
            "clips": [c for c in existing if clip_is_pr_home(c)][:CLIPS_PER_PLAYER],
            "angle": existing[0].get("angle"),
            "confirmed_lowhome": is_lowhome_angle(str(existing[0].get("angle") or "")),
            "pr_scanned": True,
            "events_scanned": int(prev.get("events_scanned") or 0),
        }
        ckpt.setdefault("done", {})[sid] = record
        ckpt.get("failed", {}).pop(sid, None)
        save_ckpt(ckpt)
        return "ok"

    # 1) Hunt LBPRC first (deep scan past recent MiLB AsLogged).
    log("  scanning LBPRC…")
    pr_events = fetch_events(bas, token, player, pr_only=True)
    pr_by_game = collect_clips_by_game(pr_events)
    # Prefer home angles inside PR; only fall back to PR AsLogged if no home track exists.
    home_pr = {
        gid: rows
        for gid, rows in pr_by_game.items()
        if any(
            is_lowhome_angle(str(r.get("angle") or "")) or is_highhome_angle(str(r.get("angle") or ""))
            for r in rows
        )
    }
    # Within home games, put the home-angle row first (not AsLogged).
    for rows in home_pr.values():
        rows.sort(
            key=lambda r: (
                0
                if is_lowhome_angle(str(r.get("angle") or ""))
                else 1
                if is_highhome_angle(str(r.get("angle") or ""))
                else 2,
                str(r.get("game_date") or ""),
            )
        )
    use_pr = home_pr if home_pr else pr_by_game
    if home_pr:
        log(f"  LBPRC home games={len(home_pr)} (of {len(pr_by_game)} PR games)")
    elif pr_by_game:
        log(f"  LBPRC games={len(pr_by_game)} but no LowHome/HighHome — using best PR angle")
    if use_pr:
        clips = _try_download_from_games(use_pr, paths)
        if clips:
            record = {
                "name": player["name"],
                "stem": stem,
                "team": player["team"],
                "kind": player["kind"],
                "clips": clips,
                "games_with_video": len(use_pr),
                "angle": clips[0].get("angle"),
                "confirmed_lowhome": is_lowhome_angle(str(clips[0].get("angle") or "")),
                "pr_scanned": True,
                "events_scanned": len(pr_events),
            }
            ckpt.setdefault("done", {})[sid] = record
            ckpt.get("failed", {}).pop(sid, None)
            save_ckpt(ckpt)
            return "ok"

    # 2) No PR video — keep a prior file if present, else any-league fallback.
    if existing or (paths[0].is_file() and paths[0].stat().st_size > 10_000):
        if not existing and paths[0].is_file():
            existing = [{
                "path": str(paths[0]),
                "angle": prev.get("angle") or "unknown",
                "game_id": "disk",
                "league": prev.get("clips", [{}])[0].get("league") if prev.get("clips") else None,
                "bytes": paths[0].stat().st_size,
            }]
        log("  no LBPRC home clip — keeping existing non-PR file")
        record = {
            "name": player["name"],
            "stem": stem,
            "team": player["team"],
            "kind": player["kind"],
            "clips": existing[:CLIPS_PER_PLAYER],
            "angle": existing[0].get("angle"),
            "confirmed_lowhome": is_lowhome_angle(str(existing[0].get("angle") or "")),
            "pr_scanned": True,
            "events_scanned": len(pr_events),
        }
        ckpt.setdefault("done", {})[sid] = record
        ckpt.get("failed", {}).pop(sid, None)
        save_ckpt(ckpt)
        return "ok"

    log("  no LBPRC — falling back to any league")
    events = fetch_events(bas, token, player, pr_only=False)
    by_game = collect_clips_by_game(events)
    clips = _try_download_from_games(by_game, paths)
    if not clips:
        ckpt.setdefault("failed", {})[sid] = {
            "name": player["name"],
            "stem": stem,
            "error": "no_playable_video",
            "events_scanned": len(pr_events) + len(events),
        }
        save_ckpt(ckpt)
        return "fail"

    record = {
        "name": player["name"],
        "stem": stem,
        "team": player["team"],
        "kind": player["kind"],
        "clips": clips,
        "games_with_video": len(by_game),
        "angle": clips[0].get("angle"),
        "confirmed_lowhome": is_lowhome_angle(str(clips[0].get("angle") or "")),
        "pr_scanned": True,
        "events_scanned": len(pr_events) + len(events),
    }
    ckpt.setdefault("done", {})[sid] = record
    ckpt.get("failed", {}).pop(sid, None)
    save_ckpt(ckpt)
    return "ok"


def main() -> int:
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PITCH_TIPS / ".pw-browsers"))

    # single-instance lock
    lock = ROOT / "runs" / "pr_hitter_view_video.lock"
    if lock.is_file():
        try:
            old_pid = int(lock.read_text().strip() or "0")
            os.kill(old_pid, 0)
            log(f"another instance running pid={old_pid}; exit")
            return 2
        except (OSError, ValueError):
            pass
    lock.write_text(str(os.getpid()))

    try:
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(PITCH_TIPS / "cv"))
        import build_advance_synergy as bas

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        players = load_players()
        ckpt = load_ckpt()
        purged = purge_mislabeled(ckpt)
        log(f"players={len(players)} out={OUT_DIR} purged={purged} max_events={MAX_EVENTS}")

        token = bas.login_token()
        Path("/tmp/synergy_token.txt").write_text(token)

        # Retry failures; mark non-PR AsLogged done-rows for LBPRC upgrade.
        ckpt["failed"] = {}
        ckpt["partial"] = {}
        for sid, row in list((ckpt.get("done") or {}).items()):
            clips = row.get("clips") or []
            if clips and not any(clip_is_pr_home(c) for c in clips) and not row.get("pr_scanned"):
                row["needs_pr_upgrade"] = True
                ckpt["done"][sid] = row
        save_ckpt(ckpt)

        counts = {"ok": 0, "fail": 0, "skip": 0}
        t0 = time.time()
        for i, player in enumerate(players, 1):
            sid = player["synergy_id"]
            prev = (ckpt.get("done") or {}).get(sid)
            if player_complete(prev):
                counts["skip"] += 1
                continue
            log(f"[{i}/{len(players)}] {player['team']} {player['name']} ({player['kind']}) → {player['stem']}")
            try:
                status = process_player(bas, token, player, ckpt)
                counts[status] = counts.get(status, 0) + 1
                if status == "fail":
                    log("  FAIL no playable video / download")
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    Path("/tmp/synergy_token.txt").unlink(missing_ok=True)
                    token = bas.login_token()
                    Path("/tmp/synergy_token.txt").write_text(token)
                ckpt.setdefault("failed", {})[sid] = {
                    "name": player["name"],
                    "stem": player["stem"],
                    "error": f"HTTP {exc.code}",
                }
                save_ckpt(ckpt)
                counts["fail"] += 1
                log(f"  FAIL HTTP {exc.code}")
            except Exception as exc:  # noqa: BLE001
                ckpt.setdefault("failed", {})[sid] = {
                    "name": player["name"],
                    "stem": player["stem"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
                save_ckpt(ckpt)
                counts["fail"] += 1
                log(f"  FAIL {type(exc).__name__}: {exc}")

        done = ckpt.get("done") or {}
        lowhome_n = sum(1 for r in done.values() if r.get("confirmed_lowhome"))
        summary = {
            "out_dir": str(OUT_DIR),
            "players": len(players),
            "rule": "1 clip/pitcher; LowHome > HighHome > any playable",
            **counts,
            "done_total": len(done),
            "lowhome_total": lowhome_n,
            "fallback_angle_total": len(done) - lowhome_n,
            "failed_total": len(ckpt.get("failed") or {}),
            "elapsed_min": round((time.time() - t0) / 60, 2),
            "naming": "TEAMNAME_LASTFIRST.mp4",
            "example": "CAROLINA_REYNOSOEDDY.mp4",
        }
        REPORT.write_text(json.dumps(summary, indent=2) + "\n")
        log(json.dumps(summary))
        return 0
    finally:
        try:
            if lock.is_file() and lock.read_text().strip() == str(os.getpid()):
                lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

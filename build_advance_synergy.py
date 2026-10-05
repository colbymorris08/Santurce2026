#!/usr/bin/env python3
"""
Build Santurce Advance tab caches from Synergy (+ seed Rodriguez packet).

Outputs (under data/):
  advance_rodriguez.json          — Dereck Rodríguez packet (PDF sections, live JSON)
  advance_opposing_hitters.json   — Select-all + per-hitter pitch-type / RISP / bunts+SB
  pregame_spray_charts.json       — enriched with Synergy spray (keeps MLB Statcast when present)

Roster gate: only players on Puerto Rico (LBPRC) opposing rosters (CAG/CAR/MAY/PON/SJU).
Grouping: PR team from local LBPRC roster (not summer club).
Sample expansion: for those players only, attach winter LBPRC + summer MLB/MiLB/LMB
events for target years (default 2025–2026). Summer-only players are excluded.

Auth: pitch-tips/.env.synergy via Playwright OIDC (Chrome channel).
Never writes secrets into site JSON.

Refresh:
  cd /Users/colbymorris/Santurce2026
  python3 build_advance_synergy.py --years 2025 2026 --max-events-per-team 4000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data"
PITCH_TIPS = Path.home() / "apexstats" / "pitch-tips"
if not PITCH_TIPS.is_dir():
    PITCH_TIPS = Path("/Users/colbymorris/apexstats/pitch-tips")

SPORT_API = "https://sport.synergysportstech.com"
EVENTS_FILTER_URL = "https://baseball.synergysportstech.com/external/api/events/filter"
SPORT_ID_BASEBALL = "570aaedc46c5d11de0f8c0bd"
LEAGUE_LBPRC = "5dd2cbcc4b8b50a3e8e46172"
LEAGUE_LMB = "616a0762c122029009e74144"
LEAGUE_MLB = "573d688d080cc2cbe8aecba2"
LEAGUE_MILB = "5834a73335be47a927637a89"
LOGGING_PHASE_PITCH = 16

# Summer + winter leagues used to expand samples for PR-rostered players only.
SAMPLE_LEAGUES = {
    "LBPRC": LEAGUE_LBPRC,  # winter
    "LMB": LEAGUE_LMB,      # Mexico summer
    "MLB": LEAGUE_MLB,
    "MILB": LEAGUE_MILB,
}
SUMMER_LEAGUE_IDS = {LEAGUE_LMB, LEAGUE_MLB, LEAGUE_MILB}
WINTER_LEAGUE_IDS = {LEAGUE_LBPRC}

# PR opposing rosters (Advance tabs). SJU = Synergy SSJ (Senadores).
OPP_TEAMS = {
    "CAG": "5dd2ce614b8b50a3e8e461a2",
    "CAR": "5dd2ce884b8b50a3e8e461a8",
    "MAY": "5dd2ceb04b8b50a3e8e461ae",
    "PON": "6274570a77783bd4debe46be",
    "SJU": "67080ee4004b3d1070e35804",
}
SAN_TEAM = "5dd2cf384b8b50a3e8e461bc"
CHI_TEAM = "65fc6ecbe369791707cf9760"
HITTER_POS = {
    "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "OF", "DH", "UT", "INF", "PH", "PR",
}

SWING_RESULTS = {
    "StrikeSwinging",
    "Foul",
    "Bip",
    "InPlay",
    "FoulTip",
    "FoulBunt",
    "MissedBunt",
}
WHIFF_RESULTS = {"StrikeSwinging", "FoulTip", "MissedBunt"}
BIP_RESULTS = {"Bip", "InPlay"}

PA_TB = {
    "Single": 1,
    "Double": 2,
    "Triple": 3,
    "HomeRun": 4,
    "Homerun": 4,
}
PA_HIT = {"Single", "Double", "Triple", "HomeRun", "Homerun"}
PA_AB_OUT = {
    "Strikeout",
    "Groundout",
    "Flyout",
    "Lineout",
    "Popout",
    "Forceout",
    "FieldersChoice",
    "DoublePlay",
    "TriplePlay",
    "BuntGroundout",
    "BuntPopout",
    "SacrificeBunt",  # typically not AB — handled separately
}
PA_WALK = {"Walk", "IntentionalWalk", "HitByPitch"}
PA_SAC_BUNT = {"SacrificeBunt", "BuntGroundout", "BuntPopout"}

# Seeded from pitch-tips/scripts/build_rodriguez_advance.py (Synergy/Trackman sample used for PDF)
RODRIGUEZ_SEED = {
    "name": "Dereck Rodríguez",
    "mlb_id": 605446,
    "throws": "R",
    "bats": "R",
    "bio": "6′0″ · 208 lbs · R/R · Age 34 · MLB ID 605446",
    "subtitle": "RHP · Dorados de Chihuahua (LMB) · Advance Report",
    "source": "Synergy / Trackman sample (automated from advance packet builders)",
    "builder": "pitch-tips/scripts/build_rodriguez_advance.py",
    "seasons": {
        "lmb_2026": {
            "label": "2026 Chihuahua (LMB)",
            "w": 4,
            "l": 2,
            "era": "4.12",
            "g": 19,
            "gs": 5,
            "ip": "39.1",
            "so": 32,
            "bb": 9,
            "whip": "1.53",
            "h": 51,
            "hr": 4,
        },
        "lbprc_winter": {
            "label": "2025-2026 Winter Caguas (LBPRC)",
            "w": 2,
            "l": 3,
            "era": "2.84",
            "g": 9,
            "gs": 9,
            "ip": "44.1",
            "so": 28,
            "bb": 8,
            "whip": "1.11",
            "h": 41,
            "hr": 2,
        },
    },
    "overview": [
        "4S / SL foundation; CU is the primary third pitch.",
        "CH is LHH-leaning; FC bridges FF→SL movement.",
        "High ¾ slot. 2S omitted (<2% usage).",
        "Source: Synergy/Trackman · 629 pitches · 19 G / 5 GS.",
    ],
    "approach": [
        "vs LHH: more 4S + CH; CU posts elite K% (54.5%) in-sample — hunt chase below zone after FF elevate.",
        "vs RHH: SL jumps to ~33% usage; keep FC/SL sequence glove-side, CU as putaway.",
        "In-zone overall ~51%; Strike% 67%. Force chase on SL/CU after early FF strikes.",
        "Ahead → SL/CU; Behind → 4S/FC in zone.",
    ],
    "delivery_note": "Visual arm-slot reference (high three-quarters). Trackman RelX/RelZ ~−15″ / 74″.",
    "headshot_url": "https://img.mlbstatic.com/mlb-photos/image/upload/w_213,q_auto:best/v1/people/605446/headshot/67/current",
    "headshot_local": "assets/advance/rodriguez_headshot.png",
    "arm_slot_image": "assets/advance/rodriguez_arm_slot.png",
    "delivery": {
        "label": "High three-quarters",
        "rel_x_in": -15.0,
        "rel_z_in": 74.0,
        "arm_angle_approx_deg": 11.5,
        "note": "Visual arm-slot reference (high three-quarters). Trackman RelX/RelZ ~−15″ / 74″.",
    },
    "totals": {
        "pitches": 629,
        "strike_pct": 67.2,
        "iz_pct": 50.9,
        "k_pct": 18.1,
        "bb_pct": 5.1,
        "miss_pct": 22.6,
        "gb_pct": 44.0,
        "ev": 83.0,
    },
    "arsenal": [
        {
            "code": "4S",
            "name": "4-Seam",
            "usage": 35.8,
            "velo": 89.4,
            "ivb": 13.75,
            "hb": -4.6,
            "spin": 2256,
            "miss_l": 27.9,
            "miss_r": 26.7,
            "k_l": 21.7,
            "k_r": 20.0,
            "strike": 69.0,
            "iz": 52.0,
            "gb": 46.0,
            "usage_l": 39.2,
            "usage_r": 33.4,
            "desc": "Below-avg ride, some cut. T92 in 2026, avg ~89. Works up; damage middle/up. Low whiff on FB.",
        },
        {
            "code": "SL",
            "name": "Slider",
            "usage": 27.8,
            "velo": 85.5,
            "ivb": 5.5,
            "hb": 4.5,
            "spin": 2317,
            "miss_l": 14.3,
            "miss_r": 22.2,
            "k_l": 18.8,
            "k_r": 8.3,
            "strike": 74.0,
            "iz": 52.0,
            "gb": 48.0,
            "usage_l": 20.8,
            "usage_r": 32.9,
            "desc": "Cutter-slider profile; ~5″ gloveside. Works middle/down. Damage in zone.",
        },
        {
            "code": "CU",
            "name": "Curveball",
            "usage": 17.8,
            "velo": 75.6,
            "ivb": -11.0,
            "hb": 9.1,
            "spin": 2487,
            "miss_l": 34.5,
            "miss_r": 34.1,
            "k_l": 54.5,
            "k_r": 20.0,
            "strike": 60.0,
            "iz": 43.0,
            "gb": 42.0,
            "usage_l": 16.0,
            "usage_r": 19.0,
            "desc": "Slurve shape, get-me-over. ~13 mph off FB. Spit if it starts bottom half.",
        },
        {
            "code": "FC",
            "name": "Cutter",
            "usage": 8.4,
            "velo": 87.7,
            "ivb": 11.3,
            "hb": 0.8,
            "spin": 2282,
            "miss_l": 15.4,
            "miss_r": 17.6,
            "k_l": 0.0,
            "k_r": 0.0,
            "strike": 66.0,
            "iz": 50.0,
            "gb": 36.0,
            "usage_l": 8.8,
            "usage_r": 8.1,
            "desc": "FF→SL bridge; more common first pitch than putaway. GB / in on hands.",
        },
        {
            "code": "CH",
            "name": "Changeup",
            "usage": 8.5,
            "velo": 82.4,
            "ivb": 8.7,
            "hb": -6.9,
            "spin": 1620,
            "miss_l": 27.8,
            "miss_r": 18.2,
            "k_l": 16.7,
            "k_r": 16.7,
            "strike": 61.0,
            "iz": 43.0,
            "gb": 47.0,
            "usage_l": 14.0,
            "usage_r": 4.6,
            "desc": "Not a runner CH; limited arm-side. Works away; LHH-leaning.",
        },
    ],
    "usage_vs_hand": {
        "vs_LHH": {"n": 247},
        "vs_RHH": {"n": 340},
    },
    "short_vs_rhh": {
        "overall": [("4S", 33), ("SL", 33), ("CU", 19), ("FC", 8), ("CH", 5)],
        "0-0": [("SL", 37), ("4S", 28), ("CU", 16), ("FC", 15)],
        "2_strikes": [("4S", 41), ("CU", 26), ("SL", 17), ("CH", 10), ("FC", 5)],
    },
}


def _load_env() -> None:
    sys.path.insert(0, str(PITCH_TIPS / "cv"))
    from preflight.synergy_env import load_synergy_env  # noqa: WPS433

    load_synergy_env()


async def _login_chrome(headed: bool = False) -> str:
    _load_env()
    email = (os.environ.get("SYNERGY_EMAIL") or "").strip()
    password = (os.environ.get("SYNERGY_PASSWORD") or "").strip()
    if not email or not password:
        raise RuntimeError("SYNERGY_EMAIL / SYNERGY_PASSWORD missing in pitch-tips/.env.synergy")

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=not headed)
        page = await browser.new_page()
        await page.goto("https://baseball-web.synergysports.com/", wait_until="domcontentloaded", timeout=60000)
        await page.locator('input[type="password"]').first.wait_for(timeout=25000)
        await page.locator('input[type="email"], input[name="Username"], input#Username').first.fill(email)
        await page.locator('input[type="password"]').first.fill(password)
        await page.get_by_role("button", name=re.compile("login", re.I)).click()
        user = None
        for _ in range(50):
            await page.wait_for_timeout(600)
            user = await page.evaluate(
                """() => {
                  const k = Object.keys(localStorage).find(x => x.startsWith('oidc.user:'));
                  if (!k) return null;
                  return JSON.parse(localStorage.getItem(k));
                }"""
            )
            if user and user.get("access_token"):
                break
        await browser.close()
    if not user or not user.get("access_token"):
        raise RuntimeError("Synergy OIDC login failed")
    return str(user["access_token"])


def login_token(headed: bool = False, cached: Path | None = None) -> str:
    cached = cached or Path("/tmp/synergy_token.txt")
    if cached.is_file() and cached.stat().st_size > 100:
        age = time.time() - cached.stat().st_mtime
        if age < 3000:
            return cached.read_text().strip()
    tok = asyncio.run(_login_chrome(headed=headed))
    cached.write_text(tok)
    return tok


def api_get(path: str, token: str, params: dict | None = None) -> Any:
    url = path if path.startswith("http") else f"{SPORT_API}{path}"
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "SanturceAdvance/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode())


def api_post(url: str, token: str, body: dict) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SanturceAdvance/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode())


def list_league_seasons(league_id: str, token: str) -> list[dict]:
    """Return Synergy seasons for a league, newest-first.

    Season calendar year is taken from the leading YYYY in ``name``
    (e.g. "2025-2026 Winter" → 2025, "2026 Regular" → 2026).
    """
    doc = api_get(f"/api/leagues/{league_id}/seasons", token)
    rows = doc.get("result") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or not rows:
        return []

    def key(r: dict) -> tuple:
        try:
            year = int(str(r.get("name") or "0")[:4])
        except ValueError:
            year = 0
        return (year, int(r.get("iid") or 0))

    return sorted(rows, key=key, reverse=True)


def season_year(row: dict) -> int:
    try:
        return int(str(row.get("name") or "0")[:4])
    except ValueError:
        return 0


def seasons_for_years(league_id: str, token: str, years: tuple[int, ...] = (2025, 2026)) -> list[dict]:
    """Pick all league seasons whose name starts with any of ``years``."""
    want = set(int(y) for y in years)
    rows = [r for r in list_league_seasons(league_id, token) if season_year(r) in want]
    # Fallback: if nothing matched (naming quirks), keep the latest season only.
    if not rows:
        all_rows = list_league_seasons(league_id, token)
        return all_rows[:1] if all_rows else []
    return rows


def latest_season_id(league_id: str, token: str) -> str | None:
    rows = list_league_seasons(league_id, token)
    if not rows:
        return None
    return str(rows[0].get("id") or "") or None


def fetch_events_pages(
    token: str,
    *,
    team_id: str | None = None,
    batter_id: str | None = None,
    pitcher_id: str | None = None,
    season_ids: list[str] | str | None = None,
    max_events: int,
    take: int = 200,
) -> list[dict]:
    """Paginate Synergy events/filter by teamIds, batterId, and/or pitcherId.

    Note: with batterId/pitcherId, Synergy often ignores seasonIds — callers should
    filter client-side via ``filter_events_by_leagues_years``.
    Prefer pitcherId (Montgomery-style) for pitcher count/hand usage — teamIds
    alone often omit ``pitcher.id`` (use defense.lineup.pitcher instead).
    """
    if isinstance(season_ids, str):
        season_ids = [season_ids]
    season_ids = [s for s in (season_ids or []) if s]
    out: list[dict] = []
    skip = 0
    while len(out) < max_events:
        body: dict[str, Any] = {
            "loggingPhases": [LOGGING_PHASE_PITCH],
            "skip": skip,
            "take": min(take, max_events - len(out)),
        }
        if team_id:
            body["teamIds"] = [team_id]
        if batter_id:
            body["batterId"] = batter_id
        if pitcher_id:
            body["pitcherId"] = pitcher_id
        if season_ids:
            body["seasonIds"] = season_ids
        if not team_id and not batter_id and not pitcher_id:
            break
        try:
            doc = api_post(EVENTS_FILTER_URL, token, body)
        except urllib.error.HTTPError as exc:
            label = pitcher_id or batter_id or team_id
            print(f"  events HTTP {exc.code} id={label} skip={skip}")
            break
        rows = doc.get("result") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        total = int(doc.get("totalRecords") or 0)
        skip += len(rows)
        print(f"  fetched {len(out)}/{min(max_events, total or max_events)} (page)")
        if skip >= total or len(rows) < take:
            break
        time.sleep(0.12)
    return out


def _norm_person(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def event_league_id(ev: dict) -> str:
    g = ev.get("game") or {}
    lg = g.get("league") if isinstance(g, dict) else None
    if isinstance(lg, dict):
        return str(lg.get("id") or "")
    return ""


def event_season_year(ev: dict) -> int:
    g = ev.get("game") or {}
    try:
        return int(g.get("season") or 0)
    except (TypeError, ValueError):
        return 0


def filter_events_by_leagues_years(
    events: list[dict],
    *,
    league_ids: set[str],
    years: set[int],
) -> list[dict]:
    out = []
    for ev in events:
        lid = event_league_id(ev)
        yr = event_season_year(ev)
        if lid in league_ids and yr in years:
            out.append(ev)
    return out


def load_pr_roster_gate() -> dict[str, Any]:
    """Load opposing PR (LBPRC) rosters used as the exclusive player gate.

    Returns dict with:
      by_mlb: {mlbam: {team, name, position, mlb_id}}
      by_name: {norm_name: same}
      counts: {team_abbr: {all, hitters}}
    """
    rost = OUT / "lbprc_2025_rosters.json"
    by_mlb: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    counts: dict[str, dict[str, int]] = {}
    if not rost.is_file():
        return {"by_mlb": by_mlb, "by_name": by_name, "counts": counts}
    for t in json.loads(rost.read_text()).values():
        abbr = str(t.get("teamAbbrev") or "").upper()
        if abbr not in OPP_TEAMS:
            continue
        players = t.get("players") or []
        n_hit = 0
        for p in players:
            pos = str(p.get("position") or "").upper()
            is_hitter = pos != "P"  # include blank / UT / TWP as potential hitters
            if is_hitter:
                n_hit += 1
            try:
                mid = int(p.get("id") or 0)
            except (TypeError, ValueError):
                mid = 0
            name = str(p.get("name") or "").strip()
            rec = {"team": abbr, "name": name, "position": pos, "mlb_id": mid or None, "is_hitter": is_hitter}
            if mid:
                by_mlb[mid] = rec
            if name:
                by_name[_norm_person(name)] = rec
        counts[abbr] = {"all": len(players), "hitters": n_hit}
    return {"by_mlb": by_mlb, "by_name": by_name, "counts": counts}


def resolve_player_mlb_id(token: str, synergy_id: str, cache: dict[str, int | None]) -> int | None:
    if synergy_id in cache:
        return cache[synergy_id]
    mid: int | None = None
    try:
        doc = api_get(f"/api/players/{synergy_id}", token)
        res = doc.get("result") if isinstance(doc, dict) else None
        if isinstance(res, dict):
            raw = res.get("mlbProfileId") or res.get("ids")
            if isinstance(raw, list) and raw:
                raw = raw[0]
            try:
                mid = int(raw) if raw not in (None, "", 0, "0") else None
            except (TypeError, ValueError):
                mid = None
    except Exception:  # noqa: BLE001
        mid = None
    cache[synergy_id] = mid
    return mid


def match_pr_roster(
    *,
    name: str,
    mlb_id: int | None,
    gate: dict[str, Any],
) -> dict | None:
    """Return PR roster record if this person is on an opposing PR roster."""
    by_mlb: dict = gate["by_mlb"]
    by_name: dict = gate["by_name"]
    if mlb_id and mlb_id in by_mlb:
        return by_mlb[mlb_id]
    key = _norm_person(name)
    if key and key in by_name:
        return by_name[key]
    return None


def merge_hitter_raw(dest: dict[str, dict], raw: dict[str, dict]) -> None:
    for bid, h in raw.items():
        if bid not in dest:
            dest[bid] = h
            continue
        d = dest[bid]
        if h.get("name") and not d.get("name"):
            d["name"] = h["name"]
        for ptype, bucket in h["by_pitch"].items():
            db = d["by_pitch"][ptype]
            for k, v in bucket.items():
                if k == "games":
                    db["games"] |= set(v or [])
                elif isinstance(v, (int, float)):
                    db[k] = int(db.get(k) or 0) + int(v)
        for ptype, bucket in h["risp_by_pitch"].items():
            db = d["risp_by_pitch"][ptype]
            for k, v in bucket.items():
                if k == "games":
                    db["games"] |= set(v or [])
                elif isinstance(v, (int, float)):
                    db[k] = int(db.get(k) or 0) + int(v)
        for split, pts in h["spray"].items():
            d["spray"].setdefault(split, []).extend(pts)


def sample_stats(hitters: list[dict], events: list[dict] | None = None) -> dict[str, Any]:
    tot_p = sum(int((h.get("overall") or {}).get("pitches") or 0) for h in hitters)
    tot_pa = sum(int((h.get("overall") or {}).get("pa") or 0) for h in hitters)
    spray = sum(len(h.get("spray_points") or []) for h in hitters)
    out = {
        "hitters": len(hitters),
        "pitches": tot_p,
        "pa": tot_pa,
        "spray_bip": spray,
        "with_mlb_id": sum(1 for h in hitters if h.get("mlb_id")),
    }
    if events is not None:
        out["events_total"] = len(events)
    return out


def discover_pr_roster_players(
    token: str,
    *,
    lbprc_season_ids: list[str],
    max_events_per_team: int,
    gate: dict[str, Any],
) -> dict[str, dict]:
    """Map Synergy batter id → PR roster person using winter LBPRC team pulls.

    Only players matching the local opposing PR roster gate are returned.
    Team label comes from the PR roster file (not the Synergy game club).
    """
    mlb_cache: dict[str, int | None] = {}
    found: dict[str, dict] = {}
    for abbr, tid in OPP_TEAMS.items():
        print(f"=== Discover PR roster {abbr} (winter LBPRC) ===")
        events = fetch_events_pages(
            token,
            team_id=tid,
            season_ids=lbprc_season_ids,
            max_events=max_events_per_team,
        )
        # Keep winter LBPRC only (team filter can occasionally leak)
        events = filter_events_by_leagues_years(
            events,
            league_ids=WINTER_LEAGUE_IDS,
            years=set(range(2000, 2100)),  # accept any year tagged LBPRC in pull
        )
        seen_batters: dict[str, str] = {}
        for ev in events:
            bid, name = _batter(ev)
            if bid and bid not in seen_batters:
                seen_batters[bid] = name
        print(f"  unique batters in team pull={len(seen_batters)} events={len(events)}")
        matched = 0
        for bid, name in seen_batters.items():
            mid = resolve_player_mlb_id(token, bid, mlb_cache)
            rec = match_pr_roster(name=name, mlb_id=mid, gate=gate)
            if not rec:
                continue
            if not rec.get("is_hitter"):
                continue
            # Prefer first team assignment from roster file
            if bid in found:
                continue
            found[bid] = {
                "synergy_id": bid,
                "name": rec.get("name") or name,
                "team": rec["team"],
                "mlb_id": rec.get("mlb_id") or mid,
                "position": rec.get("position"),
            }
            matched += 1
        print(f"  roster-gated hitters matched={matched} (running total={len(found)})")
        time.sleep(0.1)
    return found


def _batter(ev: dict) -> tuple[str, str]:
    b = ev.get("batter") or {}
    if not isinstance(b, dict):
        return "", ""
    bid = str(b.get("id") or "")
    name = f"{b.get('nameFirst') or ''} {b.get('nameLast') or ''}".strip()
    return bid, name


def _runners_risp(ev: dict) -> bool:
    start = ((ev.get("runners") or {}).get("runnerConfigurationStart")) or {}
    if not isinstance(start, dict):
        return False
    on2 = bool(start.get("runnerOn2b") or start.get("runnerOnSecond") or start.get("2b"))
    on3 = bool(start.get("runnerOn3b") or start.get("runnerOnThird") or start.get("3b"))
    # also occupied bases list forms
    for k, v in start.items():
        lk = str(k).lower()
        if v and ("2b" in lk or "second" in lk or "3b" in lk or "third" in lk):
            return True
    return on2 or on3


def _first_pitch(ev: dict) -> bool:
    c = ev.get("count") or {}
    try:
        return int(c.get("balls") or 0) == 0 and int(c.get("strikes") or 0) == 0
    except (TypeError, ValueError):
        return False


def _norm_pitch(kind: str | None) -> str:
    k = (kind or "UNK").strip()
    aliases = {
        "FourSeamFastball": "Fastball",
        "Four-Seam": "Fastball",
        "4-Seam": "Fastball",
        "Fastball": "Fastball",
        "Sinker": "Sinker",
        "TwoSeamFastball": "Sinker",
        "Cutter": "Cutter",
        "Slider": "Slider",
        "Curveball": "Curveball",
        "Changeup": "Changeup",
        "Splitter": "Splitter",
        "Sweeper": "Sweeper",
        "Knuckleball": "Knuckleball",
    }
    return aliases.get(k, k)


def _hit_label(pa: str | None, contact: dict) -> str:
    pa = pa or ""
    if pa in ("HomeRun", "Homerun"):
        return "HR"
    if pa == "Triple":
        return "3B"
    if pa == "Double":
        return "2B"
    if pa == "Single":
        return "1B"
    if "Sacrifice" in pa or "Bunt" in pa:
        return "SAC"
    if pa in ("Error",) or "Error" in pa:
        return "E"
    ct = str((contact or {}).get("contactType") or "")
    if pa and pa not in ("", "None"):
        return "OUT"
    if ct:
        return "BIP"
    return "BIP"


def _is_swing(result: str | None) -> bool:
    return (result or "") in SWING_RESULTS


def _is_whiff(result: str | None) -> bool:
    return (result or "") in WHIFF_RESULTS


def _pa_bucket() -> dict[str, Any]:
    return {
        "pa": 0,
        "ab": 0,
        "h": 0,
        "tb": 0,
        "bb": 0,
        "hbp": 0,
        "sf": 0,
        "first_pitches": 0,
        "first_pitch_swings": 0,
        "swings": 0,
        "whiffs": 0,
        "pitches": 0,
        "bunts": 0,
        "sb_1b": 0,
        "sb_2b": 0,
        "sb_3b": 0,
        "games": set(),
    }


def _rate(n: int, d: int) -> float | None:
    if d <= 0:
        return None
    return round(100.0 * n / d, 1)


def _ops(bucket: dict) -> float | None:
    ab = int(bucket.get("ab") or 0)
    h = int(bucket.get("h") or 0)
    tb = int(bucket.get("tb") or 0)
    bb = int(bucket.get("bb") or 0)
    hbp = int(bucket.get("hbp") or 0)
    sf = int(bucket.get("sf") or 0)
    pa_ob = ab + bb + hbp + sf
    if ab <= 0 or pa_ob <= 0:
        return None
    avg = h / ab
    obp = (h + bb + hbp) / pa_ob
    slg = tb / ab
    return round(obp + slg, 3)


def aggregate_events(events: list[dict], team_abbr: str) -> dict[str, Any]:
    """Aggregate pitch events into per-hitter + select-all tables."""
    hitters: dict[str, dict[str, Any]] = {}

    def ensure(bid: str, name: str) -> dict:
        if bid not in hitters:
            hitters[bid] = {
                "synergy_id": bid,
                "name": name,
                "team": team_abbr,
                "overall": defaultdict(_pa_bucket),
                "by_pitch": defaultdict(_pa_bucket),
                "risp_by_pitch": defaultdict(_pa_bucket),
                "spray": {"all": [], "nobody_on": [], "risp": [], "non_risp": [], "two_strikes": [], "non_two_strikes": []},
                "seen_pa": set(),
            }
        elif name and not hitters[bid]["name"]:
            hitters[bid]["name"] = name
        return hitters[bid]

    for ev in events:
        if str(ev.get("eventType") or "") not in ("Pitch", "Pickoff", ""):
            # still inspect pickoffs lightly
            pass
        bid, name = _batter(ev)
        if not bid:
            continue
        h = ensure(bid, name)
        pitch = ev.get("pitch") or {}
        result = pitch.get("pitchResult")
        kind = _norm_pitch(pitch.get("pitchKind"))
        contact = ev.get("contact") or {}
        pa = ev.get("plateAppearanceResult")
        risp = _runners_risp(ev)
        fp = _first_pitch(ev)
        game = ev.get("game") or {}
        gid = str(game.get("id") or game.get("iid") or "")

        # Steal inference from runner start/end occupancy shifts on non-BIP events
        if str(ev.get("eventType") or "") == "Pickoff":
            continue

        buckets = [h["overall"][kind], h["by_pitch"][kind]]
        if risp:
            buckets.append(h["risp_by_pitch"][kind])
            buckets.append(h["overall"]["__RISP__"])

        for b in buckets:
            b["pitches"] += 1
            if gid:
                b["games"].add(gid)
            if fp:
                b["first_pitches"] += 1
                if _is_swing(result):
                    b["first_pitch_swings"] += 1
            if _is_swing(result):
                b["swings"] += 1
            if _is_whiff(result):
                b["whiffs"] += 1
            intent = str(contact.get("contactIntent") or "")
            if intent.lower().startswith("bunt") or (pa or "") in PA_SAC_BUNT or "Bunt" in str(pa or ""):
                b["bunts"] += 1

        # PA outcome once per PA id
        pa_key = f"{gid}:{ev.get('inning')}:{ev.get('inningTop')}:{ev.get('inningPlateAppearanceNumber')}"
        if pa and pa_key not in h["seen_pa"]:
            h["seen_pa"].add(pa_key)
            targets = [h["overall"][kind], h["by_pitch"][kind]]
            if risp:
                targets.append(h["risp_by_pitch"][kind])
            for b in targets:
                b["pa"] += 1
                if pa in PA_HIT:
                    b["h"] += 1
                    b["tb"] += PA_TB.get(pa, 1)
                    b["ab"] += 1
                elif pa in PA_WALK:
                    if pa == "HitByPitch":
                        b["hbp"] += 1
                    else:
                        b["bb"] += 1
                elif pa in ("SacrificeFly", "SacFly"):
                    b["sf"] += 1
                elif pa in PA_SAC_BUNT or "Bunt" in pa:
                    b["bunts"] += 1
                elif pa in PA_AB_OUT or pa.endswith("out") or pa.endswith("Out"):
                    b["ab"] += 1

        # Spray points from landing location — store EVERY BIP with risp/2K flags
        # so UI can multi-select RISP∪non-RISP and 2K∪non-2K independently.
        lx = contact.get("landingLocationX")
        ly = contact.get("landingLocationY")
        if lx is not None and ly is not None and result in BIP_RESULTS:
            c = ev.get("count") or {}
            strikes = int(c.get("strikes") or 0)
            start = ((ev.get("runners") or {}).get("runnerConfigurationStart")) or {}
            empty = not any(bool(v) for v in start.values()) if isinstance(start, dict) else True
            two_k = strikes >= 2
            pt = {
                "x_ft": round(float(lx), 1),
                "y_ft": round(float(ly), 1),
                "hit": _hit_label(pa, contact),
                "event": pa or contact.get("contactType") or "BIP",
                "coord": "synergy_feet",
                "field_zone": contact.get("fieldZone"),
                "risp": bool(risp),
                "two_strikes": bool(two_k),
                "empty_bases": bool(empty),
                "strikes": strikes,
            }
            h["spray"].setdefault("all", []).append(pt)
            if empty:
                h["spray"]["nobody_on"].append(pt)
            if risp:
                h["spray"]["risp"].append(pt)
            else:
                h["spray"].setdefault("non_risp", []).append(pt)
            if two_k:
                h["spray"]["two_strikes"].append(pt)
            else:
                h["spray"].setdefault("non_two_strikes", []).append(pt)

        # SB per base: occupancy drops with advance flags when present
        start = ((ev.get("runners") or {}).get("runnerConfigurationStart")) or {}
        end = ((ev.get("runners") or {}).get("runnerConfigurationEnd")) or {}
        if isinstance(start, dict) and isinstance(end, dict):
            # Heuristic: runner on 1B at start, not on 1B at end, on 2B at end, and pitch not BIP/walk → steal of 2B
            def on(cfg: dict, *keys: str) -> bool:
                for k, v in cfg.items():
                    lk = str(k).lower()
                    if v and any(x in lk for x in keys):
                        return True
                return False

            if result in (None, "Ball", "StrikeTaken", "StrikeSwinging", "Foul", "FoulTip"):
                if on(start, "1b", "first") and on(end, "2b", "second") and not on(end, "1b", "first"):
                    h["overall"][kind]["sb_1b"] += 1  # steal of 2nd (from 1B)
                    h["by_pitch"]["__SB__"]["sb_1b"] += 1
                if on(start, "2b", "second") and on(end, "3b", "third") and not on(end, "2b", "second"):
                    h["overall"][kind]["sb_2b"] += 1
                    h["by_pitch"]["__SB__"]["sb_2b"] += 1
                if on(start, "3b", "third") and not on(end, "3b", "third") and (pa in ("StolenBaseHome",) or False):
                    h["overall"][kind]["sb_3b"] += 1
                    h["by_pitch"]["__SB__"]["sb_3b"] += 1

    return hitters


def _freeze_bucket(b: dict) -> dict:
    games = b.get("games") or set()
    g = len(games) if isinstance(games, set) else int(games or 0)
    out = {
        "pitches": int(b.get("pitches") or 0),
        "pa": int(b.get("pa") or 0),
        "ab": int(b.get("ab") or 0),
        "h": int(b.get("h") or 0),
        "ops": _ops(b),
        "first_pitch_swing_pct": _rate(int(b.get("first_pitch_swings") or 0), int(b.get("first_pitches") or 0)),
        "whiff_pct": _rate(int(b.get("whiffs") or 0), int(b.get("swings") or 0)),
        "swings": int(b.get("swings") or 0),
        "whiffs": int(b.get("whiffs") or 0),
        "first_pitches": int(b.get("first_pitches") or 0),
        "first_pitch_swings": int(b.get("first_pitch_swings") or 0),
        "bunts": int(b.get("bunts") or 0),
        "sb_from_1b": int(b.get("sb_1b") or 0),
        "sb_from_2b": int(b.get("sb_2b") or 0),
        "sb_from_3b": int(b.get("sb_3b") or 0),
        "games": g,
    }
    # per 162
    if g > 0:
        scale = 162.0 / g
        out["bunts_per_162"] = round(out["bunts"] * scale, 1)
        out["sb_1b_per_162"] = round(out["sb_from_1b"] * scale, 1)
        out["sb_2b_per_162"] = round(out["sb_from_2b"] * scale, 1)
        out["sb_3b_per_162"] = round(out["sb_from_3b"] * scale, 1)
    else:
        out["bunts_per_162"] = None
        out["sb_1b_per_162"] = None
        out["sb_2b_per_162"] = None
        out["sb_3b_per_162"] = None
    return out



def load_mlb_id_map() -> dict[str, int]:
    """Map normalized player name → MLBAM id from LBPRC rosters (+ MLB hitting fallback)."""
    import re
    import unicodedata

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", s or "")
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"[^a-z ]", "", s.lower()).strip()

    by: dict[str, int] = {}
    rost = OUT / "lbprc_2025_rosters.json"
    if rost.exists():
        for t in json.loads(rost.read_text()).values():
            for p in t.get("players") or []:
                if p.get("id") and p.get("name"):
                    by[norm(p["name"])] = int(p["id"])
    mlb = OUT / "mlb_2026_hitting.json"
    if mlb.exists():
        for row in json.loads(mlb.read_text()):
            nm = row.get("playerFullName") or row.get("playerName")
            if nm and row.get("playerId") and norm(nm) not in by:
                by[norm(nm)] = int(row["playerId"])
    return by


def serialize_hitters(raw: dict[str, dict], season_games: dict[str, int], mlb_ids: dict[str, int] | None = None) -> list[dict]:
    rows = []
    for bid, h in raw.items():
        by_pitch = []
        for ptype, bucket in h["by_pitch"].items():
            if ptype.startswith("__"):
                continue
            fr = _freeze_bucket(bucket)
            if fr["pitches"] < 5:
                continue
            by_pitch.append({"pitch_type": ptype, **fr})
        by_pitch.sort(key=lambda r: -r["pitches"])

        risp = []
        for ptype, bucket in h["risp_by_pitch"].items():
            fr = _freeze_bucket(bucket)
            if fr["pitches"] < 3:
                continue
            risp.append({"pitch_type": ptype, **fr})
        risp.sort(key=lambda r: -r["pitches"])

        # overall across pitches
        overall_acc = _pa_bucket()
        for ptype, bucket in h["by_pitch"].items():
            if ptype.startswith("__"):
                continue
            for k in ("pitches", "pa", "ab", "h", "tb", "bb", "hbp", "sf", "first_pitches", "first_pitch_swings", "swings", "whiffs", "bunts", "sb_1b", "sb_2b", "sb_3b"):
                overall_acc[k] = int(overall_acc.get(k) or 0) + int(bucket.get(k) or 0)
            overall_acc["games"] |= set(bucket.get("games") or [])

        # Prefer LBPRC season G for per-162 when available (name match)
        g_season = season_games.get(h["name"].lower())
        fr_all = _freeze_bucket(overall_acc)
        if g_season and g_season > 0:
            scale = 162.0 / g_season
            fr_all["games_season"] = g_season
            fr_all["bunts_per_162"] = round(fr_all["bunts"] * scale, 1)
            fr_all["sb_1b_per_162"] = round(fr_all["sb_from_1b"] * scale, 1)
            fr_all["sb_2b_per_162"] = round(fr_all["sb_from_2b"] * scale, 1)
            fr_all["sb_3b_per_162"] = round(fr_all["sb_from_3b"] * scale, 1)

        spray_splits = []
        for split, pts in h["spray"].items():
            spray_splits.append({"split": split, "n": len(pts), "points": pts[:800]})
        # Deduped BIP list for multi-select filters (prefer explicit "all")
        all_pts = list(h["spray"].get("all") or [])
        if not all_pts:
            seen = set()
            for split, pts in h["spray"].items():
                for pt in pts:
                    key = (pt.get("x_ft"), pt.get("y_ft"), pt.get("hit"), pt.get("event"), pt.get("risp"), pt.get("two_strikes"))
                    if key in seen:
                        continue
                    seen.add(key)
                    all_pts.append(pt)

        import re as _re
        import unicodedata as _ud
        def _norm(s: str) -> str:
            s = _ud.normalize("NFKD", s or "")
            s = "".join(c for c in s if not _ud.combining(c))
            return _re.sub(r"[^a-z ]", "", s.lower()).strip()
        mid = (mlb_ids or {}).get(_norm(h["name"]))
        row = {
            "name": h["name"],
            "synergy_id": bid,
            "team": h["team"],
            "overall": fr_all,
            "by_pitch_type": by_pitch,
            "risp_by_pitch_type": risp,
            "spray_splits": spray_splits,
            "spray_points": all_pts[:800],
        }
        if mid:
            row["mlb_id"] = mid
            row["headshot_url"] = (
                f"https://img.mlbstatic.com/mlb-photos/image/upload/w_213,q_auto:best/v1/people/{mid}/headshot/67/current"
            )
        rows.append(row)
    rows.sort(key=lambda r: -(r["overall"].get("pitches") or 0))
    return rows


def select_all_aggregate(hitters: list[dict]) -> dict:
    """Roster Select-All rollup from frozen per-hitter pitch rows."""
    by_pitch: dict[str, dict] = defaultdict(_pa_bucket)
    risp: dict[str, dict] = defaultdict(_pa_bucket)
    overall = _pa_bucket()

    def add_row(dest: dict, row: dict) -> None:
        dest["pitches"] += int(row.get("pitches") or 0)
        dest["pa"] += int(row.get("pa") or 0)
        dest["ab"] += int(row.get("ab") or 0)
        dest["h"] += int(row.get("h") or 0)
        dest["swings"] += int(row.get("swings") or 0)
        dest["whiffs"] += int(row.get("whiffs") or 0)
        dest["first_pitches"] += int(row.get("first_pitches") or 0)
        dest["first_pitch_swings"] += int(row.get("first_pitch_swings") or 0)
        dest["bunts"] += int(row.get("bunts") or 0)
        dest["sb_1b"] += int(row.get("sb_from_1b") or 0)
        dest["sb_2b"] += int(row.get("sb_from_2b") or 0)
        dest["sb_3b"] += int(row.get("sb_from_3b") or 0)
        # TB unknown in frozen rows — use H as lower-bound for OPS approx later
        dest["tb"] += int(row.get("h") or 0)

    for h in hitters:
        for row in h.get("by_pitch_type") or []:
            add_row(by_pitch[row["pitch_type"]], row)
            add_row(overall, row)
        for row in h.get("risp_by_pitch_type") or []:
            add_row(risp[row["pitch_type"]], row)
        o = h.get("overall") or {}
        g = int(o.get("games_season") or o.get("games") or 0)
        if g:
            overall["games"].add(f"{h.get('synergy_id')}:{g}")

    def pack(src: dict[str, dict]) -> list[dict]:
        out = []
        for ptype, b in src.items():
            fr = _freeze_bucket(b)
            if fr["ops"] is None and fr["ab"] > 0:
                fr["ops"] = round((fr["h"] / fr["ab"]) * 2.0, 3)
                fr["ops_note"] = "approx from H/AB when TB incomplete in rollup"
            out.append({"pitch_type": ptype, **fr})
        out.sort(key=lambda r: -r["pitches"])
        return out

    fr_all = _freeze_bucket(overall)
    # Prefer sum of season G across hitters for per-162 when available
    g_sum = sum(int((h.get("overall") or {}).get("games_season") or 0) for h in hitters)
    if g_sum > 0:
        scale = 162.0 / g_sum
        fr_all["games_season"] = g_sum
        fr_all["bunts_per_162"] = round(fr_all["bunts"] * scale, 1)
        fr_all["sb_1b_per_162"] = round(fr_all["sb_from_1b"] * scale, 1)
        fr_all["sb_2b_per_162"] = round(fr_all["sb_from_2b"] * scale, 1)
        fr_all["sb_3b_per_162"] = round(fr_all["sb_from_3b"] * scale, 1)

    return {
        "name": "Select All (opposing)",
        "team": "ALL",
        "overall": fr_all,
        "by_pitch_type": pack(by_pitch),
        "risp_by_pitch_type": pack(risp),
    }


def load_season_games() -> dict[str, int]:
    path = OUT / "lbprc_2025_hitting.json"
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text())
    out = {}
    for r in rows:
        if r.get("teamAbbrev") == "SAN":
            continue
        name = (r.get("playerFullName") or r.get("playerName") or "").strip().lower()
        g = int(r.get("gamesPlayed") or 0)
        if name and g:
            out[name] = max(out.get(name, 0), g)
    return out


def merge_spray_into_pregame(hitters: list[dict]) -> None:
    path = OUT / "pregame_spray_charts.json"
    existing = json.loads(path.read_text()) if path.is_file() else {}
    synergy_players = []
    for h in hitters:
        splits = h.get("spray_splits") or []
        n = sum(s.get("n") or 0 for s in splits)
        if n <= 0:
            continue
        synergy_players.append(
            {
                "name": h["name"],
                "synergy_id": h["synergy_id"],
                "team": h["team"],
                "mlb_id": h.get("mlb_id"),
                "source": "Synergy LBPRC events (landingLocationX/Y)",
                "splits": splits,
                "points": h.get("spray_points") or next(
                    (s.get("points") for s in splits if s.get("split") == "all"),
                    [],
                ),
            }
        )
    synergy_players.sort(key=lambda r: -sum(s.get("n") or 0 for s in r["splits"]))
    existing["synergy"] = synergy_players
    existing["synergy_note"] = (
        "Synergy spray uses landingLocationX/Y in feet (home→CF = +Y, toward RF = +X). "
        "Multi-select filters: RISP/non-RISP and 2K/non-2K are independent toggles "
        "(check both in a dimension = all). Points carry risp + two_strikes flags. "
        "MLB Statcast sprays retained under .mlb when available."
    )
    existing["updated_synergy"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(existing, indent=2))
    print(f"spray synergy players={len(synergy_players)} → {path}")


def enrich_rodriguez_from_synergy(token: str, season_lmb: str | list[str] | None) -> dict:
    """Attach live Synergy pitch-kind mix for Chihuahua sample when available."""
    packet = json.loads(json.dumps(RODRIGUEZ_SEED))  # deep copy
    season_ids = season_lmb if isinstance(season_lmb, list) else ([season_lmb] if season_lmb else [])
    season_ids = [s for s in season_ids if s]
    if not season_ids:
        packet["synergy_live"] = {"ok": False, "note": "No LMB season id"}
        return packet
    # Pull a capped CHI sample and summarize pitchKind frequency (pitcher field often empty on team filter)
    try:
        rows = fetch_events_pages(token, team_id=CHI_TEAM, season_ids=season_ids, max_events=1600, take=200)
    except Exception as exc:  # noqa: BLE001
        packet["synergy_live"] = {"ok": False, "note": str(exc)}
        return packet
    kinds: dict[str, int] = defaultdict(int)
    for ev in rows:
        if str(ev.get("eventType") or "") != "Pitch":
            continue
        # Prefer RHP three-quarters matches Rodriguez profile when pitcher missing
        pi = ev.get("pitcherInfo") or {}
        if pi.get("pitchingSide") and str(pi.get("pitchingSide")).lower().startswith("l"):
            continue
        kind = _norm_pitch((ev.get("pitch") or {}).get("pitchKind"))
        kinds[kind] += 1
    total = sum(kinds.values()) or 1
    packet["synergy_live"] = {
        "ok": True,
        "sample_pitches": sum(kinds.values()),
        "team": "Dorados de Chihuahua",
        "season_id": season_ids[0] if len(season_ids) == 1 else None,
        "season_ids": season_ids,
        "pitch_kind_usage": [
            {"pitch_type": k, "n": n, "usage": round(100.0 * n / total, 1)}
            for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])
        ],
        "note": (
            "Team-filter sample (pitcher attribution often blank on Synergy team query). "
            "Primary arsenal/usage vs hand remain from the curated Synergy/Trackman advance packet."
        ),
    }
    return packet


def _pitcher(ev: dict) -> tuple[str, str]:
    """Return (synergy_id, name). Team filters often blank pitcher — fall back to defense lineup."""
    p = ev.get("pitcher") or {}
    if not isinstance(p, dict) or not p.get("id"):
        p = (((ev.get("defense") or {}).get("lineup") or {}).get("pitcher")) or {}
    if not isinstance(p, dict):
        return "", ""
    pid = str(p.get("id") or "")
    name = f"{p.get('nameFirst') or p.get('firstName') or ''} {p.get('nameLast') or p.get('lastName') or ''}".strip()
    if not name:
        name = str(p.get("name") or "").strip()
    return pid, name


def _pitch_code(kind: str | None) -> str:
    """Map Synergy pitchKind → Statcast-style codes used by advance UI."""
    k = (kind or "").strip()
    aliases = {
        "Fastball": "FF",
        "FourSeamFastball": "FF",
        "Four-Seam": "FF",
        "4-Seam": "FF",
        "Four Seam Fastball": "FF",
        "Sinker": "SI",
        "TwoSeamFastball": "SI",
        "Two-Seam": "SI",
        "Cutter": "FC",
        "Slider": "SL",
        "Curveball": "CU",
        "Changeup": "CH",
        "Splitter": "FS",
        "Sweeper": "ST",
        "Knuckleball": "KN",
        "KnuckleCurve": "KC",
        "Slurve": "SV",
        "Eephus": "EP",
        "IntentionalBall": "IB",
        "PitchOut": "PO",
    }
    if k in aliases:
        return aliases[k]
    up = k.upper()
    if len(up) <= 3 and up.isalpha():
        return up
    return aliases.get(_norm_pitch(k), up or "UN")


def _bat_side(ev: dict) -> str | None:
    """Synergy Left/Right hitter filter → L / R (mirrors UI LHH / RHH)."""
    bi = ev.get("batterInfo") or {}
    side = bi.get("battingSide") or bi.get("batSide") or bi.get("side")
    if not side:
        batter = ev.get("batter") or {}
        side = batter.get("battingSide") or batter.get("batSide")
    if not side:
        return None
    s = str(side).strip().lower()
    if s.startswith("l"):
        return "L"
    if s.startswith("r"):
        return "R"
    return None


def discover_pr_roster_pitchers(
    token: str,
    *,
    lbprc_season_ids: list[str],
    max_events_per_team: int,
    gate: dict[str, Any],
) -> dict[str, dict]:
    """Map Synergy pitcher id → PR roster pitcher via winter team pulls.

    Uses defense.lineup.pitcher (team filter events usually lack pitcher.id).
    Stops paging early once unique pitcher discovery plateaus (much faster than
    pulling the full team event corpus).
    """
    mlb_cache: dict[str, int | None] = {}
    found: dict[str, dict] = {}
    # Discovery only needs IDs — 1200 events/team is plenty; caller may pass less.
    disc_cap = min(int(max_events_per_team or 1200), 1200)
    for abbr, tid in OPP_TEAMS.items():
        print(f"=== Discover PR pitchers {abbr} (winter LBPRC) ===")
        # Manual paginate with early stop on unique-pitcher plateau
        seen: dict[str, str] = {}
        events: list[dict] = []
        skip = 0
        take = 200
        stagnant_pages = 0
        prev_n = 0
        while len(events) < disc_cap:
            body = {
                "loggingPhases": [LOGGING_PHASE_PITCH],
                "skip": skip,
                "take": min(take, disc_cap - len(events)),
                "teamIds": [tid],
                "seasonIds": lbprc_season_ids,
            }
            try:
                doc = api_post(EVENTS_FILTER_URL, token, body)
            except urllib.error.HTTPError as exc:
                print(f"  events HTTP {exc.code} id={tid} skip={skip}")
                break
            rows = doc.get("result") if isinstance(doc, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            events.extend(rows)
            for ev in rows:
                pid, name = _pitcher(ev)
                if pid and pid not in seen:
                    seen[pid] = name
            print(f"  fetched {len(events)}/{disc_cap} unique_pitchers={len(seen)}")
            if len(seen) == prev_n:
                stagnant_pages += 1
            else:
                stagnant_pages = 0
            prev_n = len(seen)
            total = int(doc.get("totalRecords") or 0)
            skip += len(rows)
            if stagnant_pages >= 2 and len(seen) >= 3:
                print(f"  early-stop discovery (plateau) unique={len(seen)}")
                break
            if skip >= total or len(rows) < take:
                break
            time.sleep(0.08)
        events = filter_events_by_leagues_years(
            events,
            league_ids=WINTER_LEAGUE_IDS,
            years=set(range(2000, 2100)),
        )
        # Re-scan after filter
        seen = {}
        for ev in events:
            pid, name = _pitcher(ev)
            if pid and pid not in seen:
                seen[pid] = name
        print(f"  unique defense pitchers in team pull={len(seen)} events={len(events)}")
        matched = 0
        for pid, name in seen.items():
            mid = resolve_player_mlb_id(token, pid, mlb_cache)
            rec = match_pr_roster(name=name, mlb_id=mid, gate=gate)
            if not rec:
                continue
            pos = str(rec.get("position") or "").upper()
            if rec.get("is_hitter") and pos not in ("P", "TWP", ""):
                continue
            if pid in found:
                continue
            found[pid] = {
                "synergy_id": pid,
                "name": rec.get("name") or name,
                "team": rec["team"],
                "mlb_id": rec.get("mlb_id") or mid,
                "position": rec.get("position") or "P",
            }
            matched += 1
        print(f"  roster-gated pitchers matched={matched} (running total={len(found)})")
        time.sleep(0.1)
    return found


def aggregate_pitcher_count_mixes(events: list[dict]) -> dict[str, dict]:
    """Build Synergy overall / 0-0 / 2-strike + vs LHH/RHH pitch-type usage by pitcher.

    Mirrors Synergy UI Count + Left/Right (hitter) filters using:
      count.balls / count.strikes, batterInfo.battingSide, pitch.pitchKind
    """
    by: dict[str, dict] = {}
    for ev in events:
        pid, name = _pitcher(ev)
        if not pid:
            continue
        pitch = ev.get("pitch") or {}
        code = _pitch_code(pitch.get("pitchKind"))
        if not code or code == "UN":
            continue
        c = ev.get("count") or {}
        try:
            balls, strikes = int(c.get("balls") or 0), int(c.get("strikes") or 0)
        except (TypeError, ValueError):
            balls, strikes = 0, 0
        side = _bat_side(ev)
        rec = by.setdefault(
            pid,
            {
                "synergy_id": pid,
                "name": name,
                "pitches": 0,
                "zero_zero": defaultdict(int),
                "two_strikes": defaultdict(int),
                "overall": defaultdict(int),
                "vs_L": defaultdict(int),
                "vs_R": defaultdict(int),
                "leagues": defaultdict(int),
                "years": set(),
            },
        )
        if name and not rec.get("name"):
            rec["name"] = name
        rec["pitches"] += 1
        rec["overall"][code] += 1
        if balls == 0 and strikes == 0:
            rec["zero_zero"][code] += 1
        if strikes >= 2:
            rec["two_strikes"][code] += 1
        if side == "L":
            rec["vs_L"][code] += 1
        elif side == "R":
            rec["vs_R"][code] += 1
        lid = event_league_id(ev)
        for abbr, lid0 in SAMPLE_LEAGUES.items():
            if lid == lid0:
                rec["leagues"][abbr] += 1
                break
        yr = event_season_year(ev)
        if yr:
            rec["years"].add(yr)

    def pack(counter: dict, total: int) -> list[dict]:
        rows = [
            {"type": k, "pitches": n, "usage": round(100.0 * n / total, 1) if total else 0}
            for k, n in counter.items()
        ]
        rows.sort(key=lambda r: -(r["pitches"] or 0))
        return rows

    out = {}
    for pid, rec in by.items():
        zz_n = sum(rec["zero_zero"].values())
        tw_n = sum(rec["two_strikes"].values())
        ov_n = sum(rec["overall"].values())
        l_n = sum(rec["vs_L"].values())
        r_n = sum(rec["vs_R"].values())
        platoon = {}
        if l_n >= 8:
            platoon["vs_LHB"] = pack(rec["vs_L"], l_n)
        if r_n >= 8:
            platoon["vs_RHB"] = pack(rec["vs_R"], r_n)
        out[pid] = {
            "synergy_id": pid,
            "name": rec["name"],
            "pitches": rec["pitches"],
            "count_mix": {
                "overall": pack(rec["overall"], ov_n),
                "0-0": pack(rec["zero_zero"], zz_n),
                "2_strikes": pack(rec["two_strikes"], tw_n),
            },
            "platoon": platoon,
            "hand_n": {"vs_LHH": l_n, "vs_RHH": r_n},
            "leagues": dict(rec["leagues"]),
            "years": sorted(rec["years"]),
            "source": "Synergy events/filter pitcherId · Count + Left/Right (hitter)",
        }
    return out


# Synergy vs Prospect Savant / Statcast often disagree on breaking-ball labels
# (e.g. Sweeper ST vs Curve CU). Try aliases when joining hand usage onto PS rows.
_PITCH_TYPE_ALIASES = {
    "ST": ("CU", "SL", "SV"),
    "CU": ("ST", "KC", "SV"),
    "KC": ("CU", "ST"),
    "SV": ("SL", "ST", "CU"),
    "FF": ("FA", "4S"),
    "FA": ("FF", "4S"),
    "4S": ("FF", "FA"),
    "SI": ("FT", "2S"),
    "FT": ("SI", "2S"),
}


def _lookup_pitch_row(by_code: dict, code: str):
    """Exact type match, then one-hop alias if unique among available Synergy types."""
    if not code:
        return None
    hit = by_code.get(code)
    if hit:
        return hit
    for alt in _PITCH_TYPE_ALIASES.get(code, ()):
        if alt in by_code:
            return by_code[alt]
    return None


def _align_synergy_type_to_arsenal(syn_type: str, arsenal_types: set[str]) -> str:
    """Remap Synergy type onto a PS arsenal code when labels diverge (ST→CU)."""
    code = str(syn_type or "").upper()
    if not code or code in arsenal_types or not arsenal_types:
        return code
    for alt in _PITCH_TYPE_ALIASES.get(code, ()):
        if alt in arsenal_types and code not in arsenal_types:
            return alt
    return code


def _remap_mix_types(rows: list | None, arsenal_types: set[str]) -> list:
    out = []
    for r in rows or []:
        row = dict(r)
        row["type"] = _align_synergy_type_to_arsenal(row.get("type"), arsenal_types)
        out.append(row)
    return out


def _enrich_arsenal_hand(pitches: list, platoon: dict) -> list:
    """Attach Synergy vs-LHH/RHH usage only — never touch PS movement fields."""
    if not pitches or not platoon:
        return pitches
    by_l = {str(p.get("type") or "").upper(): p for p in (platoon.get("vs_LHB") or [])}
    by_r = {str(p.get("type") or "").upper(): p for p in (platoon.get("vs_RHB") or [])}
    out = []
    for p in pitches:
        row = dict(p)
        code = str(row.get("type") or "").upper()
        left = _lookup_pitch_row(by_l, code)
        right = _lookup_pitch_row(by_r, code)
        if left:
            row["usage_l"] = left.get("usage")
            row["pitches_l"] = left.get("pitches")
        if right:
            row["usage_r"] = right.get("usage")
            row["pitches_r"] = right.get("pitches")
        out.append(row)
    return out


_PS_MOVE_KEYS = ("velo", "spin", "ivb", "hb", "hb_pitcher", "hb_hitter", "ps_stuff", "whiff", "xwoba")


def _has_ps_movement(pitches: list | None) -> bool:
    for p in pitches or []:
        if any(p.get(k) is not None for k in _PS_MOVE_KEYS):
            return True
    return False


def merge_synergy_count_into_arsenals(pitcher_mixes: dict[str, dict]) -> dict[str, int]:
    """Attach Synergy count + hand usage onto arsenals without overwriting PS movement.

    Prospect Savant owns velo / IVB / HB / spin / plot rows.
    Synergy owns Overall / 0–0 / 2K count mixes and vs LHH / vs RHH usage.
    """
    path = OUT / "pregame_pitcher_arsenals.json"
    stats = {"count": 0, "hand": 0, "matched": 0, "arsenal_rows": 0}
    if not path.is_file() or not pitcher_mixes:
        return stats
    data = json.loads(path.read_text())
    by_mlb: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    for rec in pitcher_mixes.values():
        mid = rec.get("mlb_id")
        if mid:
            try:
                by_mlb[int(mid)] = rec
            except (TypeError, ValueError):
                pass
        key = _norm_person(rec.get("name") or "")
        if key:
            by_name[key] = rec
    for p in data.get("pitchers") or []:
        syn = None
        try:
            mid = int(p.get("player_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid and mid in by_mlb:
            syn = by_mlb[mid]
        else:
            syn = by_name.get(_norm_person(p.get("name") or ""))
        if not syn:
            continue
        stats["matched"] += 1
        cm = syn.get("count_mix") or {}
        if cm.get("0-0") or cm.get("2_strikes") or cm.get("overall"):
            if p.get("count_mix") and not p.get("statcast_count_mix") and p.get("statcast_source"):
                p["statcast_count_mix"] = p.get("count_mix")
            arsenal_types = {
                str(a.get("type") or "").upper()
                for a in (p.get("arsenal") or [])
                if a.get("type")
            }
            cm = {
                "overall": _remap_mix_types(cm.get("overall"), arsenal_types),
                "0-0": _remap_mix_types(cm.get("0-0"), arsenal_types),
                "2_strikes": _remap_mix_types(cm.get("2_strikes"), arsenal_types),
            }
            cm = {k: v for k, v in cm.items() if v}
            p["count_mix"] = cm
            ubc = dict(p.get("usage_by_count") or {})
            # Wire Synergy 0-0 / 2K into Usage tab (Statcast fine buckets rare)
            if cm.get("0-0"):
                ubc["0-0"] = cm["0-0"]
            if cm.get("2_strikes"):
                ubc["2_strikes"] = cm["2_strikes"]
            if cm.get("overall") and "overall" not in ubc:
                ubc["overall"] = cm["overall"]
            p["usage_by_count"] = ubc
            p["synergy_count_source"] = syn.get("source")
            p["synergy_count_pitches"] = syn.get("pitches")
            p["synergy_id"] = syn.get("synergy_id")
            p["synergy_leagues"] = syn.get("leagues")
            p["synergy_years"] = syn.get("years")
            stats["count"] += 1
        platoon = syn.get("platoon") or {}
        overall = (cm.get("overall") or []) if cm else []
        has_ps = _has_ps_movement(p.get("arsenal")) or (
            str(p.get("ps_source") or "").startswith("Prospect Savant")
        )
        # Synergy-only fill when no PS arsenal exists — never replace PS movement rows.
        if overall and not p.get("arsenal") and not has_ps:
            p["arsenal"] = [
                {
                    "type": r.get("type"),
                    "name": r.get("type"),
                    "usage": r.get("usage"),
                    "pitches": r.get("pitches"),
                    "velo": None,
                    "spin": None,
                    "ivb": None,
                    "hb_pitcher": None,
                    "hb_hitter": None,
                    "ps_stuff": None,
                    "whiff": None,
                    "xwoba": None,
                }
                for r in overall
            ]
            p["pitches"] = p.get("pitches") or syn.get("pitches")
            # Do not label Synergy as Prospect Savant.
            if not p.get("ps_source") or str(p.get("ps_source")).startswith("Synergy"):
                p["ps_source"] = None
            p["synergy_usage_source"] = syn.get("source") or "Synergy pitchKind usage"
            stats["arsenal_rows"] += 1
        elif has_ps and str(p.get("ps_source") or "").startswith("Synergy"):
            # Repair mislabel from earlier merges; keep real PS movement.
            p["ps_source"] = "Prospect Savant"
        if platoon.get("vs_LHB") or platoon.get("vs_RHB"):
            if p.get("platoon") and not p.get("statcast_platoon") and p.get("statcast_source"):
                p["statcast_platoon"] = p.get("platoon")
            arsenal_types = {
                str(a.get("type") or "").upper()
                for a in (p.get("arsenal") or [])
                if a.get("type")
            }
            platoon = {
                k: _remap_mix_types(v, arsenal_types)
                for k, v in platoon.items()
                if isinstance(v, list)
            }
            p["platoon"] = platoon
            if p.get("arsenal"):
                p["arsenal"] = _enrich_arsenal_hand(p["arsenal"], platoon)
                stats["arsenal_rows"] += 1
            p["synergy_hand_n"] = syn.get("hand_n")
            p["synergy_hand_source"] = syn.get("source")
            stats["hand"] += 1
        note = p.get("note") or ""
        syn_note = (
            "Count (overall/0-0/2K) + vs LHH/RHH from Synergy pitcherId events "
            "(Count + Left/Right filters). Movement stays Prospect Savant when tracked."
        )
        if "Synergy pitcherId" not in note:
            p["note"] = (note + " " + syn_note).strip() if note else syn_note
    data["synergy_count_merged"] = stats["count"]
    data["synergy_hand_merged"] = stats["hand"]
    data["updated_synergy_counts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data["note"] = (
        "KNCT-style arsenal plots from hitter perspective. "
        "Movement (velo/IVB/HB/spin/plot) from Prospect Savant when tracked. "
        "Count usage (overall / 0-0 / 2 strikes) and hand splits (vs LHH / vs RHH) "
        "from Synergy pitcherId events when available; Statcast fallback for MLB-debut arms. "
        "Synergy never overwrites Prospect Savant movement fields."
    )
    path.write_text(json.dumps(data, indent=2))
    print(
        f"merged Synergy → arsenals count={stats['count']} hand={stats['hand']} "
        f"matched={stats['matched']}"
    )
    return stats



def pull_pr_pitcher_usage(
    token: str,
    *,
    lbprc_ids: list[str],
    season_meta: dict,
    year_set: set[int],
    max_events_per_team: int,
    max_events_per_pitcher: int,
    gate: dict[str, Any] | None = None,
) -> dict[str, dict]:
    """Discover PR roster pitchers and pull count + hand usage via pitcherId."""
    gate = gate or load_pr_roster_gate()
    target_league_ids = set(SAMPLE_LEAGUES.values())
    roster_pitchers = discover_pr_roster_pitchers(
        token,
        lbprc_season_ids=lbprc_ids,
        max_events_per_team=max_events_per_team,
        gate=gate,
    )
    print(f"PR roster-gated Synergy pitchers={len(roster_pitchers)}")
    pitcher_mixes: dict[str, dict] = {}
    events_by_league: dict[str, int] = defaultdict(int)
    empty = []
    ck_path = OUT / "advance_opposing_pitchers.json"
    if ck_path.is_file():
        try:
            prev = json.loads(ck_path.read_text())
            for row in prev.get("pitchers") or []:
                sid = str(row.get("synergy_id") or "")
                if sid and row.get("pitches"):
                    pitcher_mixes[sid] = row
            print(f"resumed {len(pitcher_mixes)} pitchers from checkpoint")
        except Exception as exc:  # noqa: BLE001
            print("checkpoint load failed", exc)
    for i, (pid, rec) in enumerate(
        sorted(roster_pitchers.items(), key=lambda kv: (kv[1].get("team") or "", kv[1].get("name") or "")),
        1,
    ):
        team = str(rec.get("team") or "?")
        name = rec.get("name") or pid
        if pid in pitcher_mixes and (pitcher_mixes[pid].get("pitches") or 0) >= 20:
            print(f"=== pitcher [{i}/{len(roster_pitchers)}] {team} {name} · skip (checkpoint)")
            continue
        print(f"=== pitcher [{i}/{len(roster_pitchers)}] {team} {name} · pitcherId ===")
        raw_events = fetch_events_pages(
            token,
            pitcher_id=pid,
            max_events=max_events_per_pitcher,
        )
        kept = filter_events_by_leagues_years(
            raw_events,
            league_ids=target_league_ids,
            years=year_set,
        )
        for ev in kept:
            lid = event_league_id(ev)
            for abbr, lid0 in SAMPLE_LEAGUES.items():
                if lid == lid0:
                    events_by_league[abbr] += 1
                    break
        packed = aggregate_pitcher_count_mixes(kept)
        # Force attribution to this pitcherId (pitcherId query should already be scoped)
        mix = packed.get(pid)
        if not mix and packed:
            # Rare: defense id differs; take sole key
            mix = next(iter(packed.values()))
        if not mix or not mix.get("pitches"):
            empty.append({"synergy_id": pid, "name": name, "team": team, "raw": len(raw_events), "kept": len(kept)})
            print(f"  empty kept={len(kept)} raw={len(raw_events)}")
            continue
        mix = dict(mix)
        mix["synergy_id"] = pid
        mix["name"] = name
        mix["team"] = team
        if rec.get("mlb_id"):
            mix["mlb_id"] = rec["mlb_id"]
        pitcher_mixes[pid] = mix
        cm = mix.get("count_mix") or {}
        hn = mix.get("hand_n") or {}
        print(
            f"  pitches={mix.get('pitches')} 0-0={sum(p.get('pitches') or 0 for p in (cm.get('0-0') or []))} "
            f"2K={sum(p.get('pitches') or 0 for p in (cm.get('2_strikes') or []))} "
            f"LHH={hn.get('vs_LHH')} RHH={hn.get('vs_RHH')} leagues={mix.get('leagues')}"
        )
        # Checkpoint after each pitcher so token expiry does not lose progress
        ck_path.write_text(
            json.dumps(
                {
                    "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source": "Synergy pitcherId checkpoint (in progress)",
                    "season_meta": season_meta,
                    "pitchers": sorted(pitcher_mixes.values(), key=lambda r: -(r.get("pitches") or 0)),
                    "empty": empty,
                    "sample": {"pitchers": len(pitcher_mixes), "discovered": len(roster_pitchers)},
                },
                indent=2,
            )
        )
        time.sleep(0.08)

    (OUT / "advance_opposing_pitchers.json").write_text(
        json.dumps(
            {
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": (
                    "Synergy events/filter pitcherId · PR roster gate · "
                    "LBPRC + MLB/MiLB/LMB 2025–2026 · Count + Left/Right (hitter)"
                ),
                "season_meta": season_meta,
                "pitchers": sorted(pitcher_mixes.values(), key=lambda r: -(r.get("pitches") or 0)),
                "empty": empty,
                "note": (
                    "Pitcher-centric pulls (not batter/team-only). "
                    "overall / 0-0 / 2_strikes from count.balls/strikes; "
                    "vs_LHB / vs_RHB from batterInfo.battingSide."
                ),
                "sample": {
                    "pitchers": len(pitcher_mixes),
                    "discovered": len(roster_pitchers),
                    "empty": len(empty),
                    "events_by_league": dict(events_by_league),
                },
            },
            indent=2,
        )
    )
    print(f"wrote advance_opposing_pitchers.json pitchers={len(pitcher_mixes)} empty={len(empty)}")
    merge_synergy_count_into_arsenals(pitcher_mixes)
    return pitcher_mixes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-events-per-team", type=int, default=4000,
                    help="Cap for winter LBPRC team pulls (hitters). Pitcher discovery auto-caps at 1200 + early-stop.")
    ap.add_argument("--max-events-per-player", type=int, default=2500,
                    help="Cap for per-batter Synergy pulls (winter+summer expansion)")
    ap.add_argument("--years", nargs="+", type=int, default=[2025, 2026],
                    help="Calendar years for sample expansion (game.season)")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--skip-fetch", action="store_true", help="Rebuild Rodriguez only")
    ap.add_argument("--pitchers-only", action="store_true",
                    help="Skip hitter expansion; discover+pull PR pitchers by pitcherId")
    ap.add_argument("--skip-pitchers", action="store_true",
                    help="Skip pitcherId count/hand pull")
    ap.add_argument("--max-events-per-pitcher", type=int, default=1000,
                    help="Cap for per-pitcher Synergy pitcherId pulls")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    token = login_token(headed=args.headed)
    print("Synergy login ok")

    target_years = tuple(args.years)
    year_set = set(int(y) for y in target_years)
    lbprc_seasons = seasons_for_years(LEAGUE_LBPRC, token, target_years)
    lmb_seasons = seasons_for_years(LEAGUE_LMB, token, target_years)
    mlb_seasons = seasons_for_years(LEAGUE_MLB, token, target_years)
    milb_seasons = seasons_for_years(LEAGUE_MILB, token, target_years)
    lbprc_ids = [str(s.get("id")) for s in lbprc_seasons if s.get("id")]
    lmb_ids = [str(s.get("id")) for s in lmb_seasons if s.get("id")]

    def _season_rows(rows: list[dict]) -> list[dict]:
        return [
            {"id": s.get("id"), "name": s.get("name"), "year": season_year(s)}
            for s in rows
        ]

    season_meta = {
        "target_years": list(target_years),
        "roster_source": "LBPRC opposing PR rosters (CAG/CAR/MAY/PON/SJU) from lbprc_2025_rosters.json",
        "sample_leagues": {
            "winter": ["LBPRC"],
            "summer": ["MLB", "MILB", "LMB"],
        },
        "lbprc": _season_rows(lbprc_seasons),
        "lmb": _season_rows(lmb_seasons),
        "mlb": _season_rows(mlb_seasons),
        "milb": _season_rows(milb_seasons),
    }
    print("seasons LBPRC", [(s.get("name"), s.get("id")) for s in lbprc_seasons])
    print("seasons LMB", [(s.get("name"), s.get("id")) for s in lmb_seasons])
    print("seasons MLB", [(s.get("name"), s.get("id")) for s in mlb_seasons])
    print("seasons MILB", [(s.get("name"), s.get("id")) for s in milb_seasons])

    # Rodriguez advance packet — pull across LMB 2025+2026 when available
    packet = enrich_rodriguez_from_synergy(token, lmb_ids or None)
    packet["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    packet["season_meta"] = {
        "target_years": list(target_years),
        "lmb": season_meta["lmb"],
        "mlb": season_meta["mlb"],
        "milb": season_meta["milb"],
    }
    (OUT / "advance_rodriguez.json").write_text(json.dumps(packet, indent=2))
    print("wrote advance_rodriguez.json")

    if args.skip_fetch:
        return

    if not lbprc_ids:
        raise SystemExit("No LBPRC seasons for target years")

    gate = load_pr_roster_gate()
    print("PR roster gate counts", gate.get("counts"))

    if args.pitchers_only:
        pull_pr_pitcher_usage(
            token,
            lbprc_ids=lbprc_ids,
            season_meta=season_meta,
            year_set=year_set,
            max_events_per_team=args.max_events_per_team,
            max_events_per_pitcher=args.max_events_per_pitcher,
            gate=gate,
        )
        print("DONE pitchers-only")
        return

    roster_players = discover_pr_roster_players(
        token,
        lbprc_season_ids=lbprc_ids,
        max_events_per_team=args.max_events_per_team,
        gate=gate,
    )
    print(f"PR roster-gated Synergy hitters={len(roster_players)}")
    by_team_counts: dict[str, int] = defaultdict(int)
    for rec in roster_players.values():
        by_team_counts[str(rec.get("team") or "?")] += 1
    print("matched by PR team", dict(sorted(by_team_counts.items())))

    season_games = load_season_games()
    all_events: list[dict] = []
    winter_events: list[dict] = []
    events_by_league: dict[str, int] = defaultdict(int)
    all_raw: dict[str, dict] = {}
    winter_raw: dict[str, dict] = {}
    target_league_ids = set(SAMPLE_LEAGUES.values())

    for i, (bid, rec) in enumerate(sorted(roster_players.items(), key=lambda kv: (kv[1].get("team") or "", kv[1].get("name") or "")), 1):
        team = str(rec.get("team") or "?")
        name = rec.get("name") or bid
        print(f"=== [{i}/{len(roster_players)}] {team} {name} · expand winter+summer ===")
        # batterId pulls ignore seasonIds reliably — filter client-side
        raw_events = fetch_events_pages(
            token,
            batter_id=bid,
            max_events=args.max_events_per_player,
        )
        kept = filter_events_by_leagues_years(
            raw_events,
            league_ids=target_league_ids,
            years=year_set,
        )
        winter = filter_events_by_leagues_years(
            kept,
            league_ids=WINTER_LEAGUE_IDS,
            years=year_set,
        )
        # Force PR team label on aggregates
        raw = aggregate_events(kept, team)
        wraw = aggregate_events(winter, team)
        # Ensure MLBAM / name from roster gate
        if bid in raw:
            raw[bid]["name"] = name
            raw[bid]["team"] = team
        if bid in wraw:
            wraw[bid]["name"] = name
            wraw[bid]["team"] = team
        merge_hitter_raw(all_raw, raw)
        merge_hitter_raw(winter_raw, wraw)
        all_events.extend(kept)
        winter_events.extend(winter)
        for ev in kept:
            lid = event_league_id(ev)
            for abbr, lid0 in SAMPLE_LEAGUES.items():
                if lid == lid0:
                    events_by_league[abbr] += 1
                    break
        print(
            f"  kept={len(kept)} winter={len(winter)} "
            f"raw_pull={len(raw_events)} leagues={dict(events_by_league)}"
        )

    mlb_ids = load_mlb_id_map()
    # Prefer roster-gate MLBAM ids
    for bid, rec in roster_players.items():
        if rec.get("mlb_id") and rec.get("name"):
            mlb_ids[_norm_person(rec["name"])] = int(rec["mlb_id"])

    hitters = serialize_hitters(all_raw, season_games, mlb_ids)
    winter_hitters = serialize_hitters(winter_raw, season_games, mlb_ids)
    # Attach PR team from roster map (serialize already has team from aggregate)
    for h in hitters:
        sid = str(h.get("synergy_id") or "")
        if sid in roster_players:
            h["team"] = roster_players[sid]["team"]
            if roster_players[sid].get("mlb_id"):
                h["mlb_id"] = roster_players[sid]["mlb_id"]
                h["headshot_url"] = (
                    f"https://img.mlbstatic.com/mlb-photos/image/upload/"
                    f"c_fill,g_auto/w_180/v1/people/{h['mlb_id']}/headshot/67/current"
                )

    select_all = select_all_aggregate(hitters)
    sample_full = sample_stats(hitters, all_events)
    sample_winter = sample_stats(winter_hitters, winter_events)
    sample_full.update(
        {
            "max_events_per_team": args.max_events_per_team,
            "max_events_per_player": args.max_events_per_player,
            "season_years": list(target_years),
            "events_by_league": dict(events_by_league),
            "pr_roster_matched": len(roster_players),
            "pr_roster_counts": gate.get("counts"),
            "pr_matched_by_team": dict(sorted(by_team_counts.items())),
        }
    )

    payload = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": (
            "Synergy events/filter · PR roster gate (CAG/CAR/MAY/PON/SJU) · "
            "samples from LBPRC winter + MLB/MiLB/LMB summer"
        ),
        "season_id": lbprc_ids[0] if len(lbprc_ids) == 1 else None,
        "season_ids": lbprc_ids,
        "season_meta": season_meta,
        "max_events_per_team": args.max_events_per_team,
        "max_events_per_player": args.max_events_per_player,
        "events_by_league": dict(events_by_league),
        "sample": sample_full,
        "sample_winter_only": sample_winter,
        "sample_comparison": {
            "winter_only": sample_winter,
            "winter_plus_summer": {
                "hitters": sample_full["hitters"],
                "pitches": sample_full["pitches"],
                "pa": sample_full["pa"],
                "spray_bip": sample_full["spray_bip"],
            },
            "prior_live_file_approx": {
                "hitters": 63,
                "pitches": 1411,
                "pa": 360,
                "spray_bip": 178,
                "note": "Previous Pages file (single LBPRC season, max_events/team=600)",
            },
        },
        "select_all": select_all,
        "hitters": hitters,
        "notes": [
            (
                "Roster gate: only LBPRC opposing PR roster players "
                f"(CAG/CAR/MAY/PON/SJU). Matched {len(roster_players)} Synergy hitters."
            ),
            (
                "Sample leagues: winter LBPRC + summer MLB / MiLB / LMB for "
                f"game.season in {list(target_years)}. Summer-only (non-PR) players excluded."
            ),
            f"Events by league: {dict(events_by_league)}.",
            (
                f"Sample winter-only → {sample_winter.get('pitches')} pitches / "
                f"{sample_winter.get('pa')} PA / {sample_winter.get('hitters')} hitters; "
                f"winter+summer → {sample_full.get('pitches')} pitches / "
                f"{sample_full.get('pa')} PA / {sample_full.get('hitters')} hitters."
            ),
            "UI grouping uses PR roster team abbreviations.",
            "First-pitch swing% = swings on 0-0 / 0-0 pitches.",
            "Whiff% = swinging-strike results / swings.",
            "OPS from Synergy plateAppearanceResult when PA completes in sample.",
            "Bunts from contactIntent/PA bunt results; SB-from-base via runner start→end heuristic on non-BIP pitches.",
            "Per-162 uses Synergy distinct games in sample, or LBPRC season G when name-matched.",
            "Spray points include risp + two_strikes flags for multi-select filters.",
        ],
    }
    (OUT / "advance_opposing_hitters.json").write_text(json.dumps(payload, indent=2))
    print(
        f"wrote advance_opposing_hitters.json hitters={len(hitters)} "
        f"pitches={sample_full['pitches']} winter_pitches={sample_winter['pitches']}"
    )

    if not args.skip_pitchers:
        pull_pr_pitcher_usage(
            token,
            lbprc_ids=lbprc_ids,
            season_meta=season_meta,
            year_set=year_set,
            max_events_per_team=args.max_events_per_team,
            max_events_per_pitcher=args.max_events_per_pitcher,
            gate=gate,
        )

    merge_spray_into_pregame(hitters)
    print("DONE")
    print("SAMPLE winter_only", sample_winter)
    print("SAMPLE winter+summer", {k: sample_full[k] for k in ("hitters", "pitches", "pa", "spray_bip")})
    print("LEAGUES", dict(events_by_league))


if __name__ == "__main__":
    main()

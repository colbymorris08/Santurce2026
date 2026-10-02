#!/usr/bin/env python3
"""
Build Santurce Advance tab caches from Synergy (+ seed Rodriguez packet).

Outputs (under data/):
  advance_rodriguez.json          — Dereck Rodríguez packet (PDF sections, live JSON)
  advance_opposing_hitters.json   — Select-all + per-hitter pitch-type / RISP / bunts+SB
  pregame_spray_charts.json       — enriched with Synergy spray (keeps MLB Statcast when present)

Auth: pitch-tips/.env.synergy via Playwright OIDC (Chrome channel).
Never writes secrets into site JSON.

Refresh:
  cd /Users/colbymorris/Santurce2026
  python3 build_advance_synergy.py
  # optional: --max-events-per-team 4000 --years 2025 2026 --headed
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
LOGGING_PHASE_PITCH = 16

OPP_TEAMS = {
    "CAG": "5dd2ce614b8b50a3e8e461a2",
    "CAR": "5dd2ce884b8b50a3e8e461a8",
    "MAY": "5dd2ceb04b8b50a3e8e461ae",
    "PON": "6274570a77783bd4debe46be",
}
SAN_TEAM = "5dd2cf384b8b50a3e8e461bc"
CHI_TEAM = "65fc6ecbe369791707cf9760"

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
    team_id: str,
    season_ids: list[str] | str,
    max_events: int,
    take: int = 200,
) -> list[dict]:
    if isinstance(season_ids, str):
        season_ids = [season_ids]
    season_ids = [s for s in season_ids if s]
    if not season_ids:
        return []
    out: list[dict] = []
    skip = 0
    while len(out) < max_events:
        body = {
            "teamIds": [team_id],
            "seasonIds": season_ids,
            "loggingPhases": [LOGGING_PHASE_PITCH],
            "skip": skip,
            "take": min(take, max_events - len(out)),
        }
        try:
            doc = api_post(EVENTS_FILTER_URL, token, body)
        except urllib.error.HTTPError as exc:
            print(f"  events HTTP {exc.code} team={team_id} skip={skip}")
            break
        rows = doc.get("result") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        total = int(doc.get("totalRecords") or 0)
        skip += len(rows)
        print(f"  fetched {len(out)}/{min(max_events, total or max_events)} (team page)")
        if skip >= total or len(rows) < take:
            break
        time.sleep(0.15)
    return out


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
    p = ev.get("pitcher") or {}
    if not isinstance(p, dict):
        return "", ""
    pid = str(p.get("id") or "")
    name = f"{p.get('nameFirst') or ''} {p.get('nameLast') or ''}".strip()
    return pid, name


def aggregate_pitcher_count_mixes(events: list[dict]) -> dict[str, dict]:
    """Build Synergy 0-0 / 2-strike pitch-type usage by pitcher when attribution exists.

    Returns {synergy_pitcher_id: {name, pitches, count_mix: {0-0, 2_strikes}, seasons_seen}}.
    """
    by: dict[str, dict] = {}
    for ev in events:
        pid, name = _pitcher(ev)
        if not pid:
            continue
        pitch = ev.get("pitch") or {}
        kind = _norm_pitch(pitch.get("pitchKind"))
        c = ev.get("count") or {}
        try:
            balls, strikes = int(c.get("balls") or 0), int(c.get("strikes") or 0)
        except (TypeError, ValueError):
            balls, strikes = 0, 0
        rec = by.setdefault(
            pid,
            {
                "synergy_id": pid,
                "name": name,
                "pitches": 0,
                "zero_zero": defaultdict(int),
                "two_strikes": defaultdict(int),
                "overall": defaultdict(int),
            },
        )
        if name and not rec.get("name"):
            rec["name"] = name
        rec["pitches"] += 1
        rec["overall"][kind] += 1
        if balls == 0 and strikes == 0:
            rec["zero_zero"][kind] += 1
        if strikes >= 2:
            rec["two_strikes"][kind] += 1

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
        out[pid] = {
            "synergy_id": pid,
            "name": rec["name"],
            "pitches": rec["pitches"],
            "count_mix": {
                "overall": pack(rec["overall"], ov_n),
                "0-0": pack(rec["zero_zero"], zz_n),
                "2_strikes": pack(rec["two_strikes"], tw_n),
            },
            "source": "Synergy events (pitcher-attributed)",
        }
    return out


def merge_synergy_count_into_arsenals(pitcher_mixes: dict[str, dict]) -> None:
    """Attach Synergy count_mix onto pregame_pitcher_arsenals by fuzzy name when present."""
    path = OUT / "pregame_pitcher_arsenals.json"
    if not path.is_file() or not pitcher_mixes:
        return
    data = json.loads(path.read_text())
    by_name = {}
    for rec in pitcher_mixes.values():
        key = (rec.get("name") or "").strip().lower()
        if key:
            by_name[key] = rec
    n = 0
    for p in data.get("pitchers") or []:
        key = (p.get("name") or "").strip().lower()
        syn = by_name.get(key)
        if not syn:
            continue
        # Prefer Synergy count mixes when they have signal; keep Statcast as fallback under statcast_count_mix
        cm = syn.get("count_mix") or {}
        if cm.get("0-0") or cm.get("2_strikes"):
            if p.get("count_mix") and not p.get("statcast_count_mix"):
                p["statcast_count_mix"] = p.get("count_mix")
            p["count_mix"] = cm
            p["synergy_count_source"] = syn.get("source")
            p["synergy_count_pitches"] = syn.get("pitches")
            n += 1
    data["synergy_count_merged"] = n
    data["updated_synergy_counts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(data, indent=2))
    print(f"merged Synergy count mixes into arsenals for {n} pitchers")



def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-events-per-team", type=int, default=4000)
    ap.add_argument("--years", nargs="+", type=int, default=[2025, 2026],
                    help="Synergy season calendar years to include (name prefix YYYY)")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--skip-fetch", action="store_true", help="Rebuild Rodriguez only")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    token = login_token(headed=args.headed)
    print("Synergy login ok")

    target_years = tuple(args.years)
    lbprc_seasons = seasons_for_years(LEAGUE_LBPRC, token, target_years)
    lmb_seasons = seasons_for_years(LEAGUE_LMB, token, target_years)
    lbprc_ids = [str(s.get("id")) for s in lbprc_seasons if s.get("id")]
    lmb_ids = [str(s.get("id")) for s in lmb_seasons if s.get("id")]
    season_meta = {
        "target_years": list(target_years),
        "lbprc": [
            {"id": s.get("id"), "name": s.get("name"), "year": season_year(s)}
            for s in lbprc_seasons
        ],
        "lmb": [
            {"id": s.get("id"), "name": s.get("name"), "year": season_year(s)}
            for s in lmb_seasons
        ],
    }
    print("seasons LBPRC", [(s.get("name"), s.get("id")) for s in lbprc_seasons])
    print("seasons LMB", [(s.get("name"), s.get("id")) for s in lmb_seasons])

    # Rodriguez advance packet — pull across LMB 2025+2026 when available
    packet = enrich_rodriguez_from_synergy(token, lmb_ids or None)
    packet["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    packet["season_meta"] = {"target_years": list(target_years), "lmb": season_meta["lmb"]}
    (OUT / "advance_rodriguez.json").write_text(json.dumps(packet, indent=2))
    print("wrote advance_rodriguez.json")

    if args.skip_fetch:
        return

    if not lbprc_ids:
        raise SystemExit("No LBPRC seasons for target years")

    season_games = load_season_games()
    all_raw: dict[str, dict] = {}
    all_events: list[dict] = []
    events_by_team: dict[str, int] = {}
    for abbr, tid in OPP_TEAMS.items():
        print(f"=== Opposing {abbr} · seasons={len(lbprc_ids)} ===")
        events = fetch_events_pages(
            token,
            team_id=tid,
            season_ids=lbprc_ids,
            max_events=args.max_events_per_team,
        )
        raw = aggregate_events(events, abbr)
        print(f"  hitters={len(raw)} events={len(events)}")
        events_by_team[abbr] = len(events)
        all_events.extend(events)
        # Merge hitters across teams/seasons (same synergy batter id)
        for bid, h in raw.items():
            if bid not in all_raw:
                all_raw[bid] = h
            else:
                # Prefer keeping first; serialize_hitters reads frozen buckets — merge pitch buckets
                dest = all_raw[bid]
                for ptype, bucket in h["by_pitch"].items():
                    db = dest["by_pitch"][ptype]
                    for k, v in bucket.items():
                        if k == "games":
                            db["games"] |= set(v or [])
                        elif isinstance(v, (int, float)):
                            db[k] = int(db.get(k) or 0) + int(v)
                for ptype, bucket in h["risp_by_pitch"].items():
                    db = dest["risp_by_pitch"][ptype]
                    for k, v in bucket.items():
                        if k == "games":
                            db["games"] |= set(v or [])
                        elif isinstance(v, (int, float)):
                            db[k] = int(db.get(k) or 0) + int(v)
                for split, pts in h["spray"].items():
                    dest["spray"].setdefault(split, []).extend(pts)

    mlb_ids = load_mlb_id_map()
    hitters = serialize_hitters(all_raw, season_games, mlb_ids)
    select_all = select_all_aggregate(hitters)
    tot_p = sum(int((h.get("overall") or {}).get("pitches") or 0) for h in hitters)
    tot_pa = sum(int((h.get("overall") or {}).get("pa") or 0) for h in hitters)
    payload = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "Synergy events/filter · LBPRC opposing teams (CAG/CAR/MAY/PON)",
        "season_id": lbprc_ids[0] if len(lbprc_ids) == 1 else None,
        "season_ids": lbprc_ids,
        "season_meta": season_meta,
        "max_events_per_team": args.max_events_per_team,
        "events_by_team": events_by_team,
        "sample": {
            "hitters": len(hitters),
            "pitches": tot_p,
            "pa": tot_pa,
            "events_total": len(all_events),
            "with_mlb_id": sum(1 for h in hitters if h.get("mlb_id")),
            "max_events_per_team": args.max_events_per_team,
            "season_years": list(target_years),
        },
        "select_all": select_all,
        "hitters": hitters,
        "notes": [
            f"Synergy seasons: {', '.join(s.get('name') or s.get('id') for s in lbprc_seasons)} (target years {list(target_years)}).",
            "First-pitch swing% = swings on 0-0 / 0-0 pitches.",
            "Whiff% = swinging-strike results / swings.",
            "OPS from Synergy plateAppearanceResult when PA completes in sample.",
            "Bunts from contactIntent/PA bunt results; SB-from-base via runner start→end heuristic on non-BIP pitches.",
            "Per-162 uses Synergy distinct games in sample, or LBPRC season G when name-matched.",
            "Spray points include risp + two_strikes flags for multi-select filters.",
        ],
    }
    (OUT / "advance_opposing_hitters.json").write_text(json.dumps(payload, indent=2))
    print(f"wrote advance_opposing_hitters.json hitters={len(hitters)} pitches={tot_p}")

    # Pitcher 0-0 / 2K mixes from the same multi-season event pull (when pitcher attributed)
    pitcher_mixes = aggregate_pitcher_count_mixes(all_events)
    (OUT / "advance_opposing_pitchers.json").write_text(
        json.dumps(
            {
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": "Synergy events pitcher attribution · LBPRC opposing team filters",
                "season_meta": season_meta,
                "pitchers": sorted(pitcher_mixes.values(), key=lambda r: -(r.get("pitches") or 0)),
                "note": "Pitcher field is often blank on Synergy team queries; rows here are attribution hits only. Statcast count_mix remains fallback in arsenals.",
            },
            indent=2,
        )
    )
    print(f"wrote advance_opposing_pitchers.json pitchers={len(pitcher_mixes)}")
    merge_synergy_count_into_arsenals(pitcher_mixes)

    merge_spray_into_pregame(hitters)
    print("DONE")


if __name__ == "__main__":
    main()

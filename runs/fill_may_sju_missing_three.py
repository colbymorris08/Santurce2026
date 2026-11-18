#!/usr/bin/env python3
"""Fill MAY Quiñones + Stofel and SJU Tenuta for advance export + one-page.

Quiñones MLBAM is 687879 (was stubbed as -3554043138 / misspelled Quinonez).
Stofel already has Prospect Savant movement; wire advance + Stats API summer.
Tenuta has no PS/Statcast tracking — Stats API LMB summer + BR register + Synergy.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from copy import deepcopy
from pathlib import Path

import build_advance_synergy as bas
import build_ligapr_daily as ligapr
import ensure_roster_player as erp
import fill_remaining_holes as holes

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORT = ROOT / "runs" / "fill_may_sju_missing_three_report.json"

TARGETS = [
    {
        "name": "Luis Quiñones",
        "team": "MAY",
        "mlb_id": 687879,
        "throws": "R",
        "jersey": "51",
        "old_ids": [-3554043138],
        "aliases": ["Luis Quinonez", "Luis Quinones"],
    },
    {
        "name": "Luke Stofel",
        "team": "MAY",
        "mlb_id": 814477,
        "throws": "R",
        "jersey": "64",
        "old_ids": [],
        "aliases": [],
    },
    {
        "name": "Matt Tenuta",
        "team": "SJU",
        "mlb_id": 621343,
        "throws": "L",
        "jersey": "14",
        "old_ids": [],
        "aliases": [],
    },
]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load(name: str):
    return json.loads((DATA / name).read_text())


def dump(name: str, doc) -> None:
    (DATA / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def dump_compact(name: str, doc) -> None:
    (DATA / name).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")


def rewrite_id_name(rows: list, *, id_keys: tuple[str, ...], target: dict) -> dict:
    mid = int(target["mlb_id"])
    names = {ligapr.norm_name(target["name"]), *(ligapr.norm_name(a) for a in target["aliases"])}
    old = set(int(x) for x in target.get("old_ids") or [])
    stats = {"renamed": 0, "id_fixed": 0, "touched": 0}
    for row in rows:
        rid = None
        for k in id_keys:
            if row.get(k) is not None:
                try:
                    rid = int(row.get(k))
                except (TypeError, ValueError):
                    rid = None
                if rid is not None:
                    break
        n = ligapr.norm_name(row.get("name") or "")
        hit = (rid == mid) or (rid in old) or (n in names)
        if not hit:
            continue
        stats["touched"] += 1
        if row.get("name") != target["name"]:
            row["name"] = target["name"]
            stats["renamed"] += 1
        for k in id_keys:
            if k in row or k.endswith("_id"):
                if row.get(k) != mid:
                    row[k] = mid
                    stats["id_fixed"] += 1
        if row.get("team") in (None, "", target["team"]):
            row["team"] = target["team"]
        if target.get("throws") and not row.get("throws"):
            row["throws"] = target["throws"]
    return stats


def dedupe_by_id(rows: list, id_key: str, prefer_name: str | None = None) -> int:
    """Keep one row per positive mlb id; prefer prefer_name when colliding."""
    best: dict[int, dict] = {}
    order: list[int | None] = []
    removed = 0
    for row in rows:
        try:
            rid = int(row.get(id_key) or 0)
        except (TypeError, ValueError):
            rid = 0
        if rid <= 0:
            order.append(None)
            continue
        if rid not in best:
            best[rid] = row
            order.append(rid)
            continue
        removed += 1
        cur = best[rid]
        if prefer_name and ligapr.norm_name(row.get("name") or "") == ligapr.norm_name(prefer_name):
            # merge non-empty fields into preferred
            merged = dict(cur)
            merged.update({k: v for k, v in row.items() if v not in (None, "", [], {})})
            merged["name"] = prefer_name
            best[rid] = merged
        else:
            merged = dict(row)
            merged.update({k: v for k, v in cur.items() if v not in (None, "", [], {})})
            best[rid] = merged
        # mark slot to reuse same id position
        order.append(rid)
    # rebuild unique preserving first-seen order
    seen = set()
    out = []
    extras = [r for r in rows if int(r.get(id_key) or 0) <= 0]
    for rid in order:
        if rid is None:
            continue
        if rid in seen:
            continue
        seen.add(rid)
        out.append(best[rid])
    # keep non-id rows at end
    out.extend(extras)
    rows[:] = out
    return removed


def sync_arsenal_into_advance(ars_row: dict, adv_row: dict) -> list[str]:
    copied = []
    for key in (
        "arsenal",
        "platoon",
        "count_mix",
        "count_hand_mix",
        "usage_by_count",
        "pitches",
        "ps_source",
        "ps_season",
        "synergy_id",
        "synergy_count_source",
        "synergy_hand_source",
        "synergy_hand_n",
        "hand_n",
        "summer_line",
        "throws",
        "arm_slot_image",
        "view",
        "note",
    ):
        val = ars_row.get(key)
        if val in (None, "", [], {}):
            continue
        if key == "arsenal" and not adv_row.get("arsenal"):
            adv_row["arsenal"] = deepcopy(val)
            copied.append(key)
        elif key == "arsenal" and adv_row.get("arsenal"):
            # fill missing movement onto existing
            by = {str(a.get("type") or "").upper(): a for a in adv_row["arsenal"]}
            for a in val:
                code = str(a.get("type") or "").upper()
                if code not in by:
                    adv_row["arsenal"].append(deepcopy(a))
                    copied.append(f"arsenal+{code}")
                else:
                    for fld in ("velo", "spin", "ivb", "hb_pitcher", "hb_hitter", "whiff", "usage", "pitches", "ps_stuff"):
                        if by[code].get(fld) in (None, "") and a.get(fld) not in (None, ""):
                            by[code][fld] = a[fld]
                            copied.append(f"{code}.{fld}")
        elif key in ("platoon", "count_mix", "count_hand_mix", "usage_by_count"):
            if not adv_row.get(key):
                adv_row[key] = deepcopy(val)
                copied.append(key)
        elif key == "summer_line":
            if not adv_row.get("summer_line"):
                adv_row["summer_line"] = deepcopy(val)
                copied.append(key)
        else:
            if adv_row.get(key) in (None, "", [], {}):
                adv_row[key] = deepcopy(val) if isinstance(val, (dict, list)) else val
                copied.append(key)
    adv_row["player_id"] = ars_row.get("player_id") or adv_row.get("mlb_id")
    return copied


def attach_summer(mlb_ids: list[int]) -> dict[int, dict]:
    return bas.fetch_2026_pitcher_summer_lines(mlb_ids)


def attach_winter(row: dict, team: str, name: str) -> bool:
    """Pull 2026-27 LBPRC winter line onto advance/arsenal row when present."""
    pitching = load("lbprc_2026_27_pitching.json")
    # file shapes vary; try common
    rows = pitching.get("pitchers") if isinstance(pitching, dict) else pitching
    if isinstance(pitching, dict) and not rows:
        # equipo style
        for block in pitching.values():
            if isinstance(block, dict) and "pitchers" in block:
                rows = block["pitchers"]
                break
    if not isinstance(rows, list):
        # try equipo map
        equipo = pitching.get("equipo") if isinstance(pitching, dict) else None
        rows = []
        if isinstance(equipo, dict):
            for _t, pack in equipo.items():
                if isinstance(pack, dict):
                    rows.extend(pack.get("pitchers") or [])
                elif isinstance(pack, list):
                    rows.extend(pack)
    want = ligapr.norm_name(name)
    hit = None
    for r in rows or []:
        if ligapr.norm_name(r.get("name") or r.get("playerFullName") or "") == want:
            hit = r
            break
    # Prefer lookup helper if available
    try:
        line = ligapr.lookup_equipo_row  # type: ignore[attr-defined]
    except Exception:
        line = None
    winter = row.get("winter_line") or row.get("lbprc_line")
    if winter:
        return False
    # Use attach via build from ligapr daily structures if present on arsenal already
    return False


def br_tenuta_summer() -> dict:
    """Parse BR register for 2026 Leon line as fallback/enrichment."""
    url = "https://www.baseball-reference.com/register/player.fcgi?id=tenuta000mat"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Santurce2026"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        page = resp.read().decode("utf-8", "replace")
    m = re.search(r'<table[^>]*id="standard_pitching"[^>]*>([\s\S]*?)</table>', page)
    if not m:
        return {}
    tbl = m.group(0)
    rows = []
    for tr in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", tbl):
        cells = []
        for cell in re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", tr):
            text = re.sub(r"<br\s*/?>", " ", cell)
            text = re.sub(r"<[^>]+>", "", text)
            text = (
                text.replace("\xa0", " ")
                .replace("&nbsp;", " ")
                .strip()
            )
            cells.append(text)
        if cells:
            rows.append(cells)
    # Find 2026 Leon row
    for r in rows:
        if len(r) < 20:
            continue
        if r[0] == "2026" and "Leon" in r[3]:
            ip = r[18]
            return {
                "team": "LEO",
                "team_name": "Bravos de León",
                "season": "Summer 2026",
                "league": "LMB",
                "circuit": "LMB",
                "league_name": "Mexican League",
                "level": "IND",
                "ip": ip,
                "g": int(r[12] or 0),
                "gs": int(r[13] or 0),
                "w": int(r[7] or 0),
                "l": int(r[8] or 0),
                "era": r[10],
                "h": int(r[19] or 0) if r[19].isdigit() else None,
                "source": "Baseball Reference register tenuta000mat",
                "source_url": url,
                "field_sources": {"team_through_hr": "Baseball Reference register"},
            }
    return {}


def refresh_ps(ars_row: dict, mlb_id: int) -> dict:
    pitches, year = holes.pack_ps(mlb_id)
    if not pitches:
        return {"status": "empty"}
    if not ars_row.get("arsenal"):
        ars_row["arsenal"] = pitches
        ars_row["pitches"] = sum(int(p.get("pitches") or 0) for p in pitches)
        ars_row["ps_season"] = year
        ars_row["ps_source"] = "Prospect Savant"
        ars_row["level_hint"] = "MiLB"
        return {"status": "filled", "n": len(pitches), "season": year, "pitches": ars_row["pitches"]}
    # merge movement onto existing
    by = {str(a.get("type") or "").upper(): a for a in ars_row["arsenal"]}
    filled = 0
    for p in pitches:
        code = str(p.get("type") or "").upper()
        if code not in by:
            ars_row["arsenal"].append(p)
            filled += 1
            continue
        for fld in ("velo", "spin", "ivb", "hb_pitcher", "hb_hitter", "whiff", "ps_stuff", "usage", "pitches"):
            if by[code].get(fld) in (None, "", 0) and p.get(fld) not in (None, ""):
                by[code][fld] = p[fld]
                filled += 1
    ars_row["ps_season"] = year or ars_row.get("ps_season")
    ars_row["ps_source"] = "Prospect Savant"
    return {"status": "merged", "filled_fields": filled, "season": year}


def main() -> None:
    report: dict = {"updated": now(), "players": {}}

    # --- identity fix Quiñones across official + lbprc ---
    official = load("official_active_pitchers.json")
    for p in official["teams"]["MAY"]:
        if ligapr.norm_name(p.get("name") or "") in {
            "luisquinones",
            "luisquinonez",
        } or int(p.get("id") or 0) in (-3554043138, 687879):
            p["id"] = 687879
            p["name"] = "Luis Quiñones"
            p["throws"] = "R"
    official["updated"] = now()
    dump("official_active_pitchers.json", official)

    rosters = load("lbprc_2025_rosters.json")
    for pack in rosters.values():
        if pack.get("teamAbbrev") != "MAY":
            continue
        for p in pack.get("players") or []:
            if ligapr.norm_name(p.get("name") or "") in {"luisquinones", "luisquinonez"} or int(
                p.get("id") or 0
            ) in (-3554043138, 687879):
                p["id"] = 687879
                p["name"] = "Luis Quiñones"
    dump("lbprc_2025_rosters.json", rosters)

    ars = load("pregame_pitcher_arsenals.json")
    adv = load("advance_opposing_pitchers.json")
    heat = load("advance_heatmaps.json")

    # Rewrite names/ids
    for t in TARGETS:
        report["players"][t["name"]] = {
            "mlb_id": t["mlb_id"],
            "arsenal_rewrite": rewrite_id_name(
                ars.get("pitchers") or [], id_keys=("player_id", "mlb_id"), target=t
            ),
            "advance_rewrite": rewrite_id_name(
                adv.get("pitchers") or [], id_keys=("mlb_id", "player_id"), target=t
            ),
        }
        # heat key
        heats = heat.get("pitchers") or {}
        if isinstance(heats, dict):
            for oid in t.get("old_ids") or []:
                if str(oid) in heats and str(t["mlb_id"]) not in heats:
                    heats[str(t["mlb_id"])] = heats.pop(str(oid))
            if str(t["mlb_id"]) in heats:
                heats[str(t["mlb_id"])]["name"] = t["name"]
                heats[str(t["mlb_id"])]["team"] = t["team"]
                heats[str(t["mlb_id"])]["mlb_id"] = t["mlb_id"]

    # Ensure stubs exist
    for t in TARGETS:
        player = {
            "name": t["name"],
            "team": t["team"],
            "id": t["mlb_id"],
            "throws": t["throws"],
            "jersey": t["jersey"],
            "position": "P",
        }
        ensure_actions = erp.ensure_pitcher(player)
        ensure_actions.update(erp.ensure_shared(player))
        report["players"][t["name"]]["ensure"] = ensure_actions

    # Reload after ensure (it rewrote files)
    ars = load("pregame_pitcher_arsenals.json")
    adv = load("advance_opposing_pitchers.json")

    def find(rows, mlb_id, team=None):
        for r in rows:
            try:
                rid = int(r.get("mlb_id") or r.get("player_id") or 0)
            except (TypeError, ValueError):
                rid = 0
            if rid == mlb_id and (team is None or r.get("team") == team):
                return r
        for r in rows:
            try:
                rid = int(r.get("mlb_id") or r.get("player_id") or 0)
            except (TypeError, ValueError):
                rid = 0
            if rid == mlb_id:
                return r
        return None

    # Summer lines
    summer = attach_summer([t["mlb_id"] for t in TARGETS])
    report["summer_ids"] = sorted(summer.keys())

    # BR Tenuta fallback
    br_line = br_tenuta_summer()
    report["tenuta_br"] = {k: br_line.get(k) for k in ("ip", "era", "g", "source") if br_line}

    for t in TARGETS:
        mid = t["mlb_id"]
        ar = find(ars["pitchers"], mid, t["team"]) or find(ars["pitchers"], mid)
        ad = find(adv["pitchers"], mid, t["team"]) or find(adv["pitchers"], mid)
        if ar is None or ad is None:
            report["players"][t["name"]]["error"] = f"missing ar={ar is not None} adv={ad is not None}"
            continue
        ar["name"] = t["name"]
        ad["name"] = t["name"]
        ar["team"] = t["team"]
        ad["team"] = t["team"]
        ar["player_id"] = mid
        ad["mlb_id"] = mid
        ar["throws"] = t["throws"]
        ad["throws"] = t["throws"]

        # PS refresh (Stofel/Quiñones; Tenuta expected empty)
        ps = refresh_ps(ar, mid)
        report["players"][t["name"]]["prospect_savant"] = ps

        if mid in summer:
            ar["summer_line"] = summer[mid]
            ad["summer_line"] = summer[mid]
            report["players"][t["name"]]["summer"] = {
                "team": summer[mid].get("team"),
                "ip": summer[mid].get("ip"),
                "era": summer[mid].get("era"),
                "source": summer[mid].get("source"),
            }
        elif t["name"] == "Matt Tenuta" and br_line:
            ar["summer_line"] = br_line
            ad["summer_line"] = br_line
            report["players"][t["name"]]["summer"] = report["tenuta_br"]

        copied = sync_arsenal_into_advance(ar, ad)
        report["players"][t["name"]]["synced_to_advance"] = copied

    # Dedupe Quinones duplicates if both stub+real existed
    report["arsenal_dedupe"] = dedupe_by_id(ars["pitchers"], "player_id", prefer_name="Luis Quiñones")
    report["advance_dedupe"] = dedupe_by_id(adv["pitchers"], "mlb_id", prefer_name="Luis Quiñones")

    ars["updated"] = now()
    adv["updated"] = now()
    dump("pregame_pitcher_arsenals.json", ars)
    dump("advance_opposing_pitchers.json", adv)
    heat["updated"] = now()
    dump_compact("advance_heatmaps.json", heat)

    # Synergy fill for Stofel + Tenuta (Quiñones already has)
    for t in TARGETS:
        if t["mlb_id"] == 687879:
            report["players"][t["name"]]["synergy"] = {"status": "skipped_already_filled"}
            continue
        player = {
            "name": t["name"],
            "team": t["team"],
            "id": t["mlb_id"],
            "throws": t["throws"],
            "jersey": t["jersey"],
            "position": "P",
        }
        try:
            fill = erp.fill_synergy_now(player)
        except Exception as exc:  # noqa: BLE001
            fill = {"status": "error", "error": str(exc)}
        report["players"][t["name"]]["synergy"] = {
            "status": fill.get("status"),
            "synergy_id": fill.get("synergy_id"),
            "window": fill.get("window"),
            "pitches": fill.get("pitches") or fill.get("events"),
            "candidates": fill.get("candidates"),
            "error": fill.get("error"),
        }
        # Re-sync arsenal→advance after synergy
        ars = load("pregame_pitcher_arsenals.json")
        adv = load("advance_opposing_pitchers.json")
        ar = find(ars["pitchers"], t["mlb_id"], t["team"]) or find(ars["pitchers"], t["mlb_id"])
        ad = find(adv["pitchers"], t["mlb_id"], t["team"]) or find(adv["pitchers"], t["mlb_id"])
        if ar and ad:
            # keep PS movement if synergy wiped it
            if t["mlb_id"] == 814477 and not any(
                a.get("ivb") is not None for a in (ar.get("arsenal") or [])
            ):
                refresh_ps(ar, 814477)
            copied = sync_arsenal_into_advance(ar, ad)
            report["players"][t["name"]]["post_synergy_sync"] = copied
            # re-attach summer if dropped
            if t["mlb_id"] in summer:
                ar["summer_line"] = summer[t["mlb_id"]]
                ad["summer_line"] = summer[t["mlb_id"]]
            elif t["name"] == "Matt Tenuta" and br_line:
                ar.setdefault("summer_line", br_line)
                ad.setdefault("summer_line", br_line)
            ar["name"] = t["name"]
            ad["name"] = t["name"]
            dump("pregame_pitcher_arsenals.json", ars)
            dump("advance_opposing_pitchers.json", adv)

    # Attach winter lines via ligapr helper used by site
    try:
        # rebuild winter onto advance rows from daily ligapr tables
        pitching = load("lbprc_2026_27_pitching.json")
        # structure: {updated, teams: {MAY: {pitchers: [...]}}} or flat
        def winter_lookup(team: str, name: str):
            want = ligapr.norm_name(name)
            candidates = []
            if isinstance(pitching, dict):
                teams = pitching.get("teams") or pitching.get("equipo") or {}
                if isinstance(teams, dict):
                    pack = teams.get(team) or {}
                    if isinstance(pack, dict):
                        candidates = pack.get("pitchers") or pack.get("players") or []
                    elif isinstance(pack, list):
                        candidates = pack
                if not candidates and isinstance(pitching.get("pitchers"), list):
                    candidates = [
                        r
                        for r in pitching["pitchers"]
                        if str(r.get("team") or "").upper() == team
                    ]
            for r in candidates:
                nm = r.get("name") or r.get("playerFullName") or r.get("playerName") or ""
                if ligapr.norm_name(nm) == want:
                    return r
            # fuzzy last name
            last = want[-6:] if len(want) > 6 else want
            hits = [
                r
                for r in candidates
                if last in ligapr.norm_name(r.get("name") or r.get("playerFullName") or "")
            ]
            return hits[0] if len(hits) == 1 else None

        adv = load("advance_opposing_pitchers.json")
        for t in TARGETS:
            ad = find(adv["pitchers"], t["mlb_id"], t["team"]) or find(adv["pitchers"], t["mlb_id"])
            if not ad:
                continue
            if ad.get("winter_line") or ad.get("lbprc_line"):
                report["players"][t["name"]]["winter"] = "already"
                continue
            # use attach_winter_lines path: call ligapr after writing temp — simpler: leave to rebuild
            report["players"][t["name"]]["winter"] = "deferred_rebuild"
        dump("advance_opposing_pitchers.json", adv)
    except Exception as exc:  # noqa: BLE001
        report["winter_error"] = str(exc)

    # Rebuild one-page (drops Quiñones stub / merges)
    one = ligapr.build_pitcher_one_page()
    ligapr.write_json("pitcher_one_page.json", one)
    report["one_page"] = "rebuilt"

    # Re-attach winter lines onto advance via ligapr daily attach if possible
    try:
        adv = load("advance_opposing_pitchers.json")
        # Mimic attach: read current winter from one-page appearance / lbprc pitching
        from build_ligapr_daily import attach_winter_lines  # noqa: F401
    except Exception:
        pass

    # Final export-match check
    official = load("official_active_pitchers.json")
    adv = load("advance_opposing_pitchers.json")
    one = load("pitcher_one_page.json")

    def export_ok(team, name, mid):
        rows = [p for p in adv["pitchers"] if p.get("team") == team]
        by_id = {int(p.get("mlb_id") or 0): p for p in rows}
        by_name = {ligapr.norm_name(p.get("name") or ""): p for p in rows}
        hit = by_id.get(mid) or by_name.get(ligapr.norm_name(name))
        one_rows = one["teams"][team]["pitchers"]
        o_by_id = {int(p.get("mlb_id") or 0): p for p in one_rows}
        o_hit = o_by_id.get(mid) or next(
            (p for p in one_rows if ligapr.norm_name(p.get("name") or "") == ligapr.norm_name(name)),
            None,
        )
        return {
            "advance": bool(hit),
            "advance_arsenal_n": len((hit or {}).get("arsenal") or []),
            "advance_has_count_mix": bool((hit or {}).get("count_mix")),
            "advance_summer": bool((hit or {}).get("summer_line")),
            "one_page": bool(o_hit),
            "one_needs": bool((o_hit or {}).get("needs_data")),
            "one_velo": bool(
                any(
                    str(v).strip()
                    for v in (((o_hit or {}).get("velo") or {}) if isinstance((o_hit or {}).get("velo"), dict) else [])
                )
            ),
        }

    report["coverage"] = {
        t["name"]: export_ok(t["team"], t["name"], t["mlb_id"]) for t in TARGETS
    }

    # official id check
    report["official_quinones"] = next(
        (
            p
            for p in official["teams"]["MAY"]
            if "quinon" in ligapr.norm_name(p.get("name") or "")
        ),
        None,
    )

    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

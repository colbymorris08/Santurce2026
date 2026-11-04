#!/usr/bin/env python3
"""Parse official roster Word docs → sync jersey #s across site caches.

Sources (copied under runs/official_rosters/ from Mail Downloads):
  CAG.docx CAR.docx MAY.docx PON.docx SJU.doc(+txt)

Updates: lbprc_2025_rosters.json, pitcher_one_page.json, pregame_card_stats.json,
pregame_tth_pop.json, game_day_state jersey overrides when present.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import docx

import build_ligapr_daily as ligapr

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEST = ROOT / "runs" / "official_rosters"
REPORT = ROOT / "runs" / "official_jersey_sync_report.json"

TEAM_FILES = {
    "CAG": DEST / "CAG.docx",
    "CAR": DEST / "CAR.docx",
    "MAY": DEST / "MAY.docx",
    "PON": DEST / "PON.docx",
    "SJU": DEST / "SJU.txt",
}

# Manual aliases: official-doc norm → roster norm (when names diverge).
# Map official-doc normalized name → roster normalized name (or reverse via index).
ALIASES: dict[str, dict[str, str]] = {
    "SJU": {
        "FREDDIE CABRERA": "FREDDY CABRERA",
        "JEAN FELIX ORTEGA": "JEAN ORTEGA",
        "MATTEW RIVERA": "MATTHEW RIVERA",
        "NARITA HARUSE": "HARUSE NARITA",
        "ROBERTO ENRIQUEZ": "ROBBIE ENRIQUEZ",
    },
    "CAG": {
        "BRYAN GONZALEZ": "BRYAN GONZALEZ GARCIA",
    },
    "CAR": {
        "MJ MELENDEZ": "MERVYL MELENDEZ",
        "SLATER DE BRUN": "SLATER DEBRUN",
    },
    "MAY": {
        "JONATHAN HAPP": "JONATHAN HAAB",
    },
}

VALID_POS = {
    "C",
    "SS",
    "INF",
    "OF",
    "1B",
    "2B",
    "3B",
    "LF",
    "CF",
    "RF",
    "DH",
    "RHP",
    "LHP",
    "P",
    "LHS",
    "RHS",
    "LHR",
    "RHR",
    "INF/OF",
    "OF/1B",
    "C/INF",
    "INF/C",
    "CATCHER",
}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def norm_name(s: str) -> str:
    s = (s or "").upper().replace("*", " ")
    trans = str.maketrans("ÁÉÍÓÚÑÜáéíóúñü", "AEIOUNUAEIOUNU")
    s = s.translate(trans)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def title_name(s: str) -> str:
    s = re.sub(r"\*+", "", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    if s.isupper() or s.islower():
        return " ".join(w[:1].upper() + w[1:].lower() for w in s.split())
    return s


def parse_docx_tables(path: Path) -> list[dict]:
    d = docx.Document(str(path))
    rows: list[dict] = []
    for table in d.tables:
        for row in table.rows:
            cells = [re.sub(r"\s+", " ", (c.text or "").replace("\xa0", " ").strip()) for c in row.cells]
            if len(cells) < 2:
                continue
            num, name = cells[0], cells[1]
            pos = cells[2] if len(cells) > 2 else ""
            if not name:
                continue
            up = name.upper()
            if any(
                x in up
                for x in (
                    "RECEPTOR",
                    "CUADRO",
                    "JARDINERO",
                    "LANZADOR",
                    "EXTRAS",
                    "INACTIV",
                    "PLAYER",
                    "ORGANIZATION",
                    "ROSTER",
                )
            ):
                continue
            if name.upper() in {"#", "PLAYER", "POS", "YE"}:
                continue
            if not re.fullmatch(r"\d{1,3}", num or ""):
                continue
            rows.append(
                {
                    "jersey": str(int(num)),
                    "name": title_name(name),
                    "pos": pos,
                    "norm": norm_name(name),
                }
            )
    return rows


def parse_sju_bell_text(path: Path) -> list[dict]:
    """SJU .doc → textutil uses BEL (\\x07) as cell separators on one long line."""
    text = path.read_text(encoding="utf-8", errors="replace")
    cut = re.split(r"JUGADORES INACTIVOS", text, maxsplit=1, flags=re.I)[0]
    tokens = [t.strip() for t in cut.split("\x07")]
    rows: list[dict] = []
    i = 0
    while i < len(tokens) - 2:
        tok = tokens[i]
        if re.fullmatch(r"\d{1,3}", tok or ""):
            name = tokens[i + 1]
            pos = tokens[i + 2]
            pos_key = re.sub(r"\s+", "", (pos or "")).upper()
            nrm = norm_name(name)
            # Require a real player name + baseball position — skip weight/org tokens.
            if (
                name
                and "PLAYER" not in name.upper()
                and len(nrm.split()) >= 2
                and len(nrm) >= 5
                and (pos_key in VALID_POS or any(pos_key.startswith(p) for p in ("LHP", "RHP", "INF", "OF", "C/")))
                and int(tok) <= 99
            ):
                rows.append(
                    {
                        "jersey": str(int(tok)),
                        "name": title_name(name),
                        "pos": pos,
                        "norm": nrm,
                    }
                )
                i += 3
                continue
        i += 1
    return rows


def load_official() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for team, path in TEAM_FILES.items():
        if not path.exists():
            raise SystemExit(f"missing roster doc: {path}")
        if team == "SJU":
            rows = parse_sju_bell_text(path)
        else:
            rows = parse_docx_tables(path)
        # de-dupe by norm, keep first
        seen: set[str] = set()
        clean = []
        for r in rows:
            if r["norm"] in seen:
                continue
            seen.add(r["norm"])
            clean.append(r)
        out[team] = clean
    return out


def last_first_key(norm: str) -> str:
    parts = norm.split()
    if len(parts) < 2:
        return norm
    return f"{parts[-1]} {' '.join(parts[:-1])}"


def build_index(rows: list[dict], team: str) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for r in rows:
        idx[r["norm"]] = r
        idx[last_first_key(r["norm"])] = r
        alias = (ALIASES.get(team) or {}).get(r["norm"])
        if alias:
            idx[norm_name(alias)] = r
    # also index aliases reverse
    for src, dst in (ALIASES.get(team) or {}).items():
        if norm_name(src) in idx:
            idx[norm_name(dst)] = idx[norm_name(src)]
    return idx


def match_row(idx: dict[str, dict], name: str) -> dict | None:
    n = norm_name(name)
    if n in idx:
        return idx[n]
    parts = n.split()
    if len(parts) >= 3:
        for key in (f"{parts[0]} {parts[-1]}", f"{parts[0]} {parts[1]}", " ".join(parts[:2])):
            if key in idx:
                return idx[key]
    lf = last_first_key(n)
    if lf in idx:
        return idx[lf]
    if not parts:
        return None
    last = parts[-1]
    hits = [r for k, r in idx.items() if k.endswith(" " + last) or k == last]
    uniq = {r["norm"]: r for r in hits}
    if len(uniq) == 1:
        return next(iter(uniq.values()))
    for r in uniq.values():
        rn = r["norm"].split()
        if len(parts) >= 2 and len(rn) >= 2 and parts[0] == rn[0] and parts[-1] == rn[-1]:
            return r
        # roster has extra token (Garcia) or doc is shorter
        if len(parts) >= 2 and len(rn) >= 2 and parts[0] == rn[0] and (parts[-1] == rn[-1] or rn[-1] == parts[1]):
            return r
    # de-spaced: DEBRUN vs DE BRUN
    compact = n.replace(" ", "")
    for r in idx.values():
        if r["norm"].replace(" ", "") == compact:
            return r
    return None


def sync() -> dict:
    official = load_official()
    (DEST / "parsed_jerseys.json").write_text(json.dumps(official, indent=2, ensure_ascii=False) + "\n")

    rost = json.loads((DATA / "lbprc_2025_rosters.json").read_text())
    updates: list[dict] = []
    unmatched_doc: list[dict] = []
    unmatched_roster: list[dict] = []

    for pack in rost.values():
        team = pack.get("teamAbbrev")
        if team not in official:
            continue
        idx = build_index(official[team], team)
        matched_norms: set[str] = set()
        for player in pack.get("players") or []:
            hit = match_row(idx, player.get("name") or "")
            if not hit:
                unmatched_roster.append({"team": team, "name": player.get("name"), "old": player.get("jersey")})
                continue
            matched_norms.add(hit["norm"])
            old = str(player.get("jersey") or "").strip()
            new = hit["jersey"]
            if old != new:
                player["jersey"] = new
                updates.append(
                    {
                        "team": team,
                        "name": player.get("name"),
                        "id": player.get("id"),
                        "old": old or None,
                        "new": new,
                        "doc_name": hit["name"],
                    }
                )
            else:
                player["jersey"] = new  # normalize type to str
        for row in official[team]:
            if row["norm"] not in matched_norms:
                unmatched_doc.append({"team": team, "name": row["name"], "jersey": row["jersey"], "pos": row["pos"]})

    (DATA / "lbprc_2025_rosters.json").write_text(json.dumps(rost, indent=2, ensure_ascii=False) + "\n")

    # pitcher one-page
    one = ligapr.build_pitcher_one_page()
    # also force jersey from roster onto one-page rows
    roster_jersey = {}
    for pack in rost.values():
        team = pack.get("teamAbbrev")
        for p in pack.get("players") or []:
            roster_jersey[(team, norm_name(p.get("name") or ""))] = str(p.get("jersey") or "")
    one_updates = 0
    for team, pack in (one.get("teams") or {}).items():
        for p in pack.get("pitchers") or []:
            key = (team, norm_name(p.get("name") or ""))
            j = roster_jersey.get(key)
            if j is not None and str(p.get("jersey") or "") != j:
                p["jersey"] = j
                one_updates += 1
            elif j is not None:
                p["jersey"] = j
    ligapr.write_json("pitcher_one_page.json", one)

    # card stats
    card_path = DATA / "pregame_card_stats.json"
    card_updates = 0
    if card_path.is_file():
        card = json.loads(card_path.read_text())
        players = card.get("players") if isinstance(card, dict) else card
        if isinstance(players, list):
            for p in players:
                team = p.get("team")
                key = (team, norm_name(p.get("name") or ""))
                j = roster_jersey.get(key)
                if j is not None and str(p.get("jersey") or "") != j:
                    p["jersey"] = j
                    card_updates += 1
                elif j is not None:
                    p["jersey"] = j
            card_path.write_text(json.dumps(card, ensure_ascii=False, separators=(",", ":")) + "\n")

    # TTH
    tth_path = DATA / "pregame_tth_pop.json"
    tth_updates = 0
    if tth_path.is_file():
        tth = json.loads(tth_path.read_text())
        for p in tth.get("players") or []:
            key = (p.get("team"), norm_name(p.get("name") or ""))
            j = roster_jersey.get(key)
            if j is not None and str(p.get("jersey") or "") != j:
                p["jersey"] = j
                tth_updates += 1
            elif j is not None:
                p["jersey"] = j
        tth_path.write_text(json.dumps(tth, indent=2, ensure_ascii=False) + "\n")

    # game_day jersey overrides — clear/replace for synced ids so site shows roster #
    gd_path = DATA / "game_day_state.json"
    gd_cleared = 0
    if gd_path.is_file():
        gd = json.loads(gd_path.read_text())
        jerseys = gd.get("jerseys") or gd.get("jersey_overrides") or {}
        if isinstance(jerseys, dict) and jerseys:
            # map id → official jersey; drop overrides that conflict so roster wins
            id_to_jersey = {}
            for pack in rost.values():
                for p in pack.get("players") or []:
                    try:
                        pid = int(p.get("id") or 0)
                    except (TypeError, ValueError):
                        pid = 0
                    if pid:
                        id_to_jersey[str(pid)] = str(p.get("jersey") or "")
            for pid, j in list(jerseys.items()):
                want = id_to_jersey.get(str(pid))
                if want is not None and str(j) != want:
                    jerseys[str(pid)] = want
                    gd_cleared += 1
                elif want is not None:
                    jerseys[str(pid)] = want
            if "jerseys" in gd:
                gd["jerseys"] = jerseys
            elif "jersey_overrides" in gd:
                gd["jersey_overrides"] = jerseys
            gd["updated"] = now()
            gd_path.write_text(json.dumps(gd, indent=2, ensure_ascii=False) + "\n")

    report = {
        "updated": now(),
        "doc_counts": {t: len(v) for t, v in official.items()},
        "jersey_changes": updates,
        "change_count": len(updates),
        "one_page_jersey_writes": one_updates,
        "card_jersey_writes": card_updates,
        "tth_jersey_writes": tth_updates,
        "game_day_override_updates": gd_cleared,
        "unmatched_roster": unmatched_roster,
        "unmatched_doc_active": unmatched_doc,
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


if __name__ == "__main__":
    rep = sync()
    print(
        json.dumps(
            {
                "doc_counts": rep["doc_counts"],
                "change_count": rep["change_count"],
                "changes": [
                    f"{c['team']} {c['name']}: {c['old']}→{c['new']}" for c in rep["jersey_changes"][:40]
                ],
                "unmatched_roster_n": len(rep["unmatched_roster"]),
                "unmatched_doc_n": len(rep["unmatched_doc_active"]),
                "one_page": rep["one_page_jersey_writes"],
                "card": rep["card_jersey_writes"],
                "tth": rep["tth_jersey_writes"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"report → {REPORT}")

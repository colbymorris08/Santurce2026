# Santurce2026 — Cangrejeros de Santurce Analytics

**Live site:** https://colbymorris08.github.io/Santurce2026/

Santurce Advance Scouting creado por Colby Morris

## Pregame tabs

Open [`pregame.html`](pregame.html). Advance is split into two tabs:

### Pitcher Advance
https://colbymorris08.github.io/Santurce2026/pregame.html#pitcher-advance

- **Team** dropdown → **Pitcher** dropdown (LBPRC arms from `pregame_pitcher_arsenals.json`)
- **Dereck Rodríguez** — full packet (arsenal, HB×IVB movement, **full-body** arm-slot / RelX·RelZ, usage vs hand, stuff+performance, approach, short vs-RHH)
- Other pitchers — Prospect Savant / arsenal **scaffold** when tracked (movement + usage; no Synergy packet yet)

### Hitter Advance
https://colbymorris08.github.io/Santurce2026/pregame.html#hitter-advance

- **Team** dropdown → **Hitter** dropdown + **Select All** (aggregates the filtered roster)
- Synergy tables + bar charts: FP swing%, OPS, whiff by pitch type (overall + RISP); bunts + SB-from-1B/2B/3B per 162
- Headshots via MLBAM id when roster-matched; spray chart with **multi-select** RISP/non-RISP + 2K/non-2K toggles
- Teams covered: CAG / CAR / MAY / PON

### Pitcher Advance extras
- Full arsenal sheet (hand splits when Statcast)
- Click-to-upload **delivery** + **heat map** images (stored as data URLs in `localStorage` keyed by pitcher id — browser-local on GitHub Pages; use **Save file…** if you want to commit under `assets/`)
- Count usage panels: Overall / 0–0 / 2 strikes
- Mass PDF export: vertical A–Z (last name) checklist → print-to-PDF
  - Pitcher: **landscape**, exactly one page, three equal thirds (charts | shapes+heat | delivery) — slim name strip with **compact roster headshot** (~3% print scale)
  - Hitter: **portrait**, exactly one page — compact roster headshot in name strip, colored pitch tables filling width, RISP/non-RISP sprays at **native field aspect** (√2, no stretch), bunts/SB

### Strategy card
https://colbymorris08.github.io/Santurce2026/pregame.html#strategy

- Opponent dropdown on the right. Santurce home/away swaps which side of the sheet Santurce occupies.
- Check hitters into today’s lineup, drag to set the order, pick a position (1–9 or DH). The starter is pinned at the bottom of the lineup and leads the pitcher block; other checked arms are the bullpen; everyone else on the hitting roster is the bench.
- Lineup, starter, and pen persist in this browser (`localStorage` key `santurce_strategy_lineup_v1`).
- Export PDF: one portrait page, scaled to fit.

### Baserunning and bunting card
https://colbymorris08.github.io/Santurce2026/pregame.html#br

- Team dropdown fills that LBPRC roster. 2B / 3B are steals of second and third when a split exists; otherwise the total SB/SBA spans both columns.
- SAC is sacrifice bunts (LBPRC 2025 + 2026 summer). Frequent >5 green, occasional 2–5 white, rare <2 red.
- Notes turn red when one base, or one bunt situation, is much more common than the other.
- Team notes persist in this browser (`localStorage` key `santurce_smallball_notes_v1`).

### Other
Spray charts still live on the Spray tab (Synergy `landingLocationX/Y` + MLB Statcast toggle).

Card rates are built with `python3 build_pregame_cards.py` → `data/pregame_card_stats.json`. Rosters stay on current LBPRC teams.

## Refresh Synergy data

Credentials live in the pitch-tips Synergy env (never commit):

- `/Users/colbymorris/apexstats/pitch-tips/.env.synergy`
- loader: `pitch-tips/cv/preflight/synergy_env.py`

```bash
cd /Users/colbymorris/Santurce2026
python3 build_advance_synergy.py --years 2025 2026 --max-events-per-team 4000
# optional: --headed   (visible Chrome login)
git add data/advance_*.json data/pregame_spray_charts.json
git add pregame.html i18n.js shared.css build_advance_synergy.py README.md
git commit -m "Refresh Synergy advance / spray caches"
git push origin main
```

Outputs:

| File | Role |
| --- | --- |
| `data/advance_rodriguez.json` | Rodriguez full pitcher-advance packet |
| `data/advance_opposing_hitters.json` | Select-all + per-hitter Synergy tables |
| `data/pregame_pitcher_arsenals.json` | Prospect Savant arsenals used to scaffold other pitchers |
| `data/pregame_spray_charts.json` | Existing MLB/AAA caches **plus** `synergy` spray list |

## Pitch usage / movement provenance (MiLB)

See Pitch Usage / Pitcher Advance notes. **MiLB arsenals are Prospect Savant**, not Synergy:

- Builder: `build_pregame_arsenal.py`
- API: `https://oriolebird.pythonanywhere.com/stuff/{mlbam}/{season}`
- Fields: `pfx_x` / `pfx_z` → IVB & HB, `release_speed`, `release_spin_rate`, `usage`, `swing_miss_percent`
- Count / hand splits: **Synergy pitcherId** events (`count.balls/strikes`, `batterInfo.battingSide`) for overall / 0–0 / 2 strikes + vs LHH/RHH; Statcast remains fallback for MLB-debut arms\n- Refresh pitchers only: `python3 build_advance_synergy.py --pitchers-only --years 2025 2026`


### Synergy seasons

- **Previously (live JSON until re-pull):** one LBPRC season from `latest_season_id()` — currently `season_id=6785e5e9501a906266421884` with `--max-events-per-team 600` (~1411 pitches / 360 PA).
- **Now (builder):** all LBPRC seasons whose Synergy name starts with **2025** or **2026** (same for LMB on Rodriguez live attach). Pitcher 0–0 / 2K mixes merge from Synergy pitcher-attributed events when present; Statcast remains fallback.

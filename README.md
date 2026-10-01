# Santurce2026 — Cangrejeros de Santurce Analytics

**Live site:** https://colbymorris08.github.io/Santurce2026/

Santurce Advance Scouting creado por Colby Morris

## Pregame tabs

Open [`pregame.html`](pregame.html). New **Advance** tab includes:

1. **Dereck Rodríguez packet** — automated from the advance PDF builders (`arsenal`, usage vs hand, approach, short vs-RHH mix). Seeded from `pitch-tips/scripts/build_rodriguez_advance.py`, enriched with a live Synergy Chihuahua sample when available.
2. **Opposing hitters (Synergy Select All)** — first-pitch swing%, OPS, whiff% by pitch type; same with RISP; bunts + SB-from-1B/2B/3B per 162.
3. **Spray charts** — Synergy `landingLocationX/Y` for LBPRC opposing hitters (source toggle keeps MLB Statcast sprays).

## Refresh Synergy data

Credentials live in the pitch-tips Synergy env (never commit):

- `/Users/colbymorris/apexstats/pitch-tips/.env.synergy`
- loader: `pitch-tips/cv/preflight/synergy_env.py`

```bash
cd /Users/colbymorris/Santurce2026
python3 build_advance_synergy.py --max-events-per-team 1000
# optional: --headed   (visible Chrome login)
git add data/advance_*.json data/pregame_spray_charts.json
git add pregame.html i18n.js shared.css build_advance_synergy.py README.md
git commit -m "Refresh Synergy advance / spray caches"
git push origin main
```

Outputs:

| File | Role |
| --- | --- |
| `data/advance_rodriguez.json` | Rodriguez advance packet for the Advance tab |
| `data/advance_opposing_hitters.json` | Select-all + per-hitter Synergy tables |
| `data/pregame_spray_charts.json` | Existing MLB/AAA caches **plus** `synergy` spray list |

## Pitch usage / movement provenance (MiLB)

See Advance / Pitch Usage tabs notes. **MiLB arsenals are Prospect Savant**, not Synergy:

- Builder: `build_pregame_arsenal.py`
- API: `https://oriolebird.pythonanywhere.com/stuff/{mlbam}/{season}`
- Fields: `pfx_x` / `pfx_z` → IVB & HB, `release_speed`, `release_spin_rate`, `usage`, `swing_miss_percent`
- Count / platoon splits: MLB Statcast via `pybaseball.statcast_pitcher` **only when** the pitcher has an MLB debut

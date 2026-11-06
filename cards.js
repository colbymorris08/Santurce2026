/* Strategy card + small-ball card.
   Lineup / pen state: localStorage santurce_strategy_lineup_v1
   Team notes: localStorage santurce_smallball_notes_v1
   Jersey overrides: localStorage santurce_jersey_overrides_v1  { [playerId]: "36" }
   Shape: { opponent, santurceHome, teams: { ABBR: { sp, pen: [id], lineup: [{id, pos}], bench: [{id, pos}] } } }
   Notes: { team, notes: { ABBR: "..." } }
*/
(function () {
  const STRATEGY_KEY = 'santurce_strategy_lineup_v1';
  const NOTES_KEY = 'santurce_smallball_notes_v1';
  const JERSEY_KEY = 'santurce_jersey_overrides_v1';
  const POS = [
    ['1', 'P'], ['2', 'C'], ['3', '1B'], ['4', '2B'], ['5', '3B'],
    ['6', 'SS'], ['7', 'LF'], ['8', 'CF'], ['9', 'RF'], ['DH', 'DH'],
  ];
  const STARTER_POS = ['2', '3', '4', '5', '6', '7', '8', '9', 'DH'];
  const POS_LABEL = Object.fromEntries(POS);
  const DEFAULT_POS = { C: '2', '1B': '3', '2B': '4', '3B': '5', SS: '6', LF: '7', CF: '8', RF: '9', OF: '8', IF: '4', INF: '4', UTL: 'DH', DH: 'DH', P: '1' };

  // Never leave DATA null — OF positioning / GameDayPersist can call into CardApp
  // before boot finishes (or if cardStats fetch fails).
  let DATA = { teams: [], players: [], gaps: {} };
  let byId = new Map();
  let advById = new Map();
  let booted = false;

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function es() { return (typeof getLang === 'function') && getLang() === 'es'; }
  function tr(en, sp) { return es() ? sp : en; }

  function loadStrategy() {
    let raw = {};
    try { raw = JSON.parse(localStorage.getItem(STRATEGY_KEY) || '{}') || {}; } catch (e) { raw = {}; }
    if (!raw.teams || typeof raw.teams !== 'object' || Array.isArray(raw.teams)) raw.teams = {};
    if (!raw.opponent) raw.opponent = 'CAG';
    if (typeof raw.santurceHome !== 'boolean') raw.santurceHome = true;
    return raw;
  }
  function saveStrategy(state) {
    try {
      state.updated = new Date().toISOString();
      localStorage.setItem(STRATEGY_KEY, JSON.stringify(state));
    } catch (e) {}
  }
  function loadNotes() {
    let raw = {};
    try { raw = JSON.parse(localStorage.getItem(NOTES_KEY) || '{}') || {}; } catch (e) { raw = {}; }
    if (!raw.notes || typeof raw.notes !== 'object') raw.notes = {};
    if (!raw.team) raw.team = 'CAG';
    return raw;
  }
  function saveNotesState(state) {
    try { localStorage.setItem(NOTES_KEY, JSON.stringify(state)); } catch (e) {}
  }
  function loadJerseyOverrides() {
    try { return JSON.parse(localStorage.getItem(JERSEY_KEY) || '{}') || {}; }
    catch (e) { return {}; }
  }
  function saveJerseyOverride(playerId, value) {
    if (playerId == null || playerId === '') return;
    const key = String(playerId);
    const all = loadJerseyOverrides();
    const text = String(value ?? '').trim();
    if (!text) delete all[key];
    else all[key] = text;
    try { localStorage.setItem(JERSEY_KEY, JSON.stringify(all)); } catch (e) {}
  }
  function jerseyFor(player) {
    if (!player) return '';
    const key = String(player.id);
    const ov = loadJerseyOverrides();
    if (Object.prototype.hasOwnProperty.call(ov, key)) return String(ov[key] ?? '');
    if (player.jersey != null && String(player.jersey).trim() !== '') return String(player.jersey).trim();
    return '';
  }
  function jerseyInput(player) {
    if (!player || player.id == null) return '';
    const val = jerseyFor(player);
    return `<input class="sc-jersey" type="text" inputmode="numeric" maxlength="3" value="${esc(val)}" data-pid="${esc(player.id)}" title="${tr('Jersey #', 'Dorsal')}" aria-label="${tr('Jersey number', 'Número de jersey')}" onmousedown="event.stopPropagation()" onchange="CardApp.saveJersey(this.dataset.pid, this.value)" oninput="CardApp.saveJersey(this.dataset.pid, this.value)">`;
  }

  function teamMeta(abbrev) {
    return ((DATA && DATA.teams) || []).find((t) => t.abbrev === abbrev) || { abbrev, name: abbrev, color: '#333', logo: null };
  }
  function normPersonName(s){
    return String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z ]/g,' ').replace(/\s+/g,' ').trim();
  }
  function roster(abbrev) {
    const rows = ((DATA && DATA.players) || []).filter((p) => p.team === abbrev);
    const by = new Map();
    rows.forEach((p) => {
      const key = `${p.role || ''}|${normPersonName(p.name)}`;
      const prev = by.get(key);
      if (!prev || (!(Number(prev.id) > 0) && Number(p.id) > 0)) by.set(key, p);
    });
    return [...by.values()];
  }
  function ensureTeam(state, abbrev) {
    if (!state.teams[abbrev]) state.teams[abbrev] = { sp: null, pen: [], lineup: [], bench: [] };
    const st = state.teams[abbrev];
    const ids = new Set(roster(abbrev).map((p) => Number(p.id)));
    const keepRow = (r) => {
      if (!r || !ids.has(Number(r.id))) return null;
      const player = byId.get(Number(r.id));
      return { id: Number(r.id), pos: String(r.pos || (player ? defaultPos(player) : 'DH')) };
    };
    const slots = Array(9).fill(null);
    const lineupSeen = new Set();
    const used = new Set();
    const overflow = [];
    (st.lineup || []).forEach((row, i) => {
      const keptRow = keepRow(row);
      if (!keptRow || lineupSeen.has(keptRow.id)) return;
      let pos = STARTER_POS.includes(keptRow.pos) ? keptRow.pos : '';
      if (!pos || used.has(pos)) pos = STARTER_POS.find((code) => !used.has(code)) || '';
      if (!pos) { overflow.push(keptRow); return; }
      let idx = i < 9 && !slots[i] ? i : slots.findIndex((spot) => !spot);
      if (idx < 0) { overflow.push(keptRow); return; }
      used.add(pos);
      lineupSeen.add(keptRow.id);
      slots[idx] = { id: keptRow.id, pos };
    });
    st.lineup = slots;
    const benchSeen = new Set(st.lineup.filter(Boolean).map((r) => r.id));
    st.bench = (st.bench || []).map(keepRow).filter(Boolean).filter((r) => {
      if (!benchHitterRow(r)) return false;
      if (benchSeen.has(r.id)) return false;
      benchSeen.add(r.id);
      return true;
    });
    overflow.forEach((row) => {
      if (!benchHitterRow(row)) return;
      if (!st.bench.some((b) => b.id === row.id)) st.bench.push(row);
    });
    st.pen = (st.pen || []).map(Number).filter((id) => ids.has(id) && id !== Number(st.sp));
    st.sp = st.sp != null && ids.has(Number(st.sp)) ? Number(st.sp) : null;
    return st;
  }

  function fmtPct(v) {
    if (v == null || !isFinite(Number(v))) return '';
    return Math.round(Number(v)) + '%';
  }
  function fmtAvg(v) {
    if (v == null || !isFinite(Number(v))) return '';
    const n = Number(v);
    const s = n.toFixed(3);
    return s.replace(/^(-?)0\./, '$1.');
  }
  function posName(code) { return POS_LABEL[code] || code || ''; }
  function defaultPos(player) { return DEFAULT_POS[player.roster_pos] || 'DH'; }
  function isPitcherPlayer(player) {
    if (!player) return false;
    if (player.role === 'P') return true;
    const pos = String(player.roster_pos || '').toUpperCase();
    return pos === 'P' || pos === 'RHP' || pos === 'LHP';
  }
  function benchHitterRow(row) {
    if (!row) return null;
    const player = byId.get(Number(row.id));
    if (isPitcherPlayer(player) || String(row.pos) === '1') return null;
    return row;
  }
  function openStarterPos(lineup, prefer) {
    const used = new Set((lineup || []).filter(Boolean).map((r) => r.pos));
    if (prefer && STARTER_POS.includes(prefer) && !used.has(prefer)) return prefer;
    return STARTER_POS.find((pos) => !used.has(pos)) || '';
  }
  function cardLast(player, peers) {
    const same = peers.filter((p) => p.last === player.last && p.role === player.role);
    if (same.length < 2) return player.last;
    const ini = (player.name.split(/\s+/)[0] || '').slice(0, 1);
    return ini ? `${ini}. ${player.last}` : player.last;
  }
  function batsClass(player) {
    if (player.role === 'P') return player.throws === 'L' ? 'sc-lhb' : '';
    if (player.bats === 'L') return 'sc-lhb';
    if (player.bats === 'S') return 'sc-shb';
    return '';
  }

  function toneHitter(metric, val, sample) {
    // Colors need 15+ PA (or 15+ swings when whiff uses a swing sample).
    if (val == null || sample == null || sample < 15) return '';
    const v = Number(val);
    if (metric === 'k' || metric === 'wh') {
      const g = 18, r = metric === 'k' ? 27 : 32;
      if (v <= g) return 'sc-good';
      if (v >= r) return 'sc-bad';
      return '';
    }
    if (metric === 'gb') {
      // High GB = soft contact / roll-over (red). Low GB = more loft (green).
      if (v >= 48) return 'sc-bad';
      if (v <= 33) return 'sc-good';
      return '';
    }
    if (metric === 'avg') {
      if (v >= 0.29) return 'sc-good';
      if (v <= 0.22) return 'sc-bad';
      return '';
    }
    if (metric === 'ops') {
      if (v >= 0.8) return 'sc-good';
      if (v <= 0.66) return 'sc-bad';
    }
    return '';
  }
  function tonePitcher(metric, val, sample) {
    if (val == null || sample == null || sample < 20) return '';
    const v = Number(val);
    if (metric === 'k') return v >= 24 ? 'sc-good' : (v <= 16 ? 'sc-bad' : '');
    if (metric === 'gb') return v >= 48 ? 'sc-good' : (v <= 36 ? 'sc-bad' : '');
    if (metric === 'wh') return v >= 28 ? 'sc-good' : (v <= 18 ? 'sc-bad' : '');
    if (metric === 'bb') return v <= 7.5 ? 'sc-good' : (v >= 12 ? 'sc-bad' : '');
    if (metric === 'ops') return v <= 0.68 ? 'sc-good' : (v >= 0.82 ? 'sc-bad' : '');
    return '';
  }

  function tthRow(id) {
    const key = String(id);
    let base = null;
    try {
      const pack = (typeof CACHE !== 'undefined' && CACHE && CACHE.tth) || null;
      const players = (pack && pack.players) || [];
      base = players.find((p) => String(p.player_id || p.mlb_id || p.id) === key) || null;
    } catch (e) {}
    let ov = null;
    try {
      const map = (typeof TTH_OVERRIDES !== 'undefined' && TTH_OVERRIDES)
        || JSON.parse(localStorage.getItem('santurce_tth_pop_overrides_v1') || '{}');
      if (map[key] && typeof map[key] === 'object') ov = map[key];
    } catch (e) {}
    if (!base && !ov) return null;
    return Object.assign({}, base || {}, ov || {});
  }
  function ttpFor(id) {
    const row = tthRow(id);
    if (row && row.time_to_home != null && String(row.time_to_home).trim() !== '') {
      return String(row.time_to_home).trim();
    }
    return '';
  }
  function popFor(id) {
    const row = tthRow(id);
    if (row && row.catcher_pop_time != null && String(row.catcher_pop_time).trim() !== '') {
      return String(row.catcher_pop_time).trim();
    }
    return '';
  }

  function sbRatio(sb, cs) {
    return `${sb || 0}/${(sb || 0) + (cs || 0)}`;
  }
  function summerSteal(h) {
    if (!h) return null;
    const levels = h.summer_levels || [];
    if (levels.length) {
      return {
        sb: levels.reduce((n, row) => n + (Number(row.sb) || 0), 0),
        cs: levels.reduce((n, row) => n + (Number(row.cs) || 0), 0),
      };
    }
    if (h.summer_sb || h.summer_cs) return { sb: Number(h.summer_sb) || 0, cs: Number(h.summer_cs) || 0 };
    return null;
  }
  function sbText(h) {
    const line = summerSteal(h);
    return line ? sbRatio(line.sb, line.cs) : '';
  }

  function aggWhiff(rows) {
    if (!rows || !rows.length) return null;
    let swings = 0, whiffs = 0, pa = 0;
    rows.forEach((r) => {
      swings += Number(r.swings) || 0;
      whiffs += Number(r.whiffs) || 0;
      pa += Number(r.pa) || 0;
    });
    if (!swings) return null;
    return { wh: Math.round((1000 * whiffs) / swings) / 10, pa, swings };
  }

  function indexAdvance(payload) {
    advById = new Map();
    ((payload && payload.hitters) || []).forEach((h) => {
      const id = Number(h.mlb_id);
      if (!id) return;
      const overall = h.overall || {};
      advById.set(id, {
        wh: overall.whiff_pct != null ? Number(overall.whiff_pct) : null,
        L: aggWhiff(h.vs_l_by_pitch_type),
        R: aggWhiff(h.vs_r_by_pitch_type),
      });
    });
  }

  function sideWh(h, playerId, side) {
    const adv = playerId != null ? advById.get(Number(playerId)) : null;
    if (adv) {
      const hand = adv[side];
      if (hand && hand.wh != null) {
        return { wh: hand.wh, pa: hand.pa, swings: hand.swings, src: 'hand' };
      }
      if (adv.wh != null) return { wh: adv.wh, pa: null, swings: null, src: 'overall' };
    }
    const handWh = side === 'L' ? h.wh_l : h.wh_r;
    const handPa = side === 'L' ? h.wh_l_pa : h.wh_r_pa;
    const handSw = side === 'L' ? h.wh_l_swings : h.wh_r_swings;
    if (handWh != null) return { wh: handWh, pa: handPa, swings: handSw, src: 'hand' };
    const split = side === 'L' ? h.vs_l : h.vs_r;
    if (split && split.wh != null) {
      return { wh: split.wh, pa: split.pa, swings: split.swings, src: 'hand' };
    }
    if (h.wh != null) return { wh: h.wh, pa: null, swings: null, src: 'overall' };
    return { wh: null, pa: null, swings: null, src: null };
  }

  function sideLine(h, side, playerId) {
    const split = side === 'L' ? h.vs_l : h.vs_r;
    const whInfo = sideWh(h, playerId, side);
    // Prefer swings for whiff color gates (rate is whiffs/swings).
    const whSample = whInfo.swings != null && whInfo.swings > 0
      ? whInfo.swings
      : (whInfo.src === 'hand' ? whInfo.pa : h.pa);
    return {
      k: h.k, gb: h.gb,
      wh: whInfo.wh,
      whSample,
      whSrc: whInfo.src,
      whPa: whInfo.pa,
      avg: split && split.avg != null ? split.avg : h.avg,
      ops: split && split.ops != null ? split.ops : h.ops,
      sample: split ? split.pa : h.pa,
      split: !!split,
    };
  }

  function statCell(text, cls, title) {
    const t = title ? ` title="${esc(title)}"` : '';
    return `<td class="${cls}"${t}>${text}</td>`;
  }

  function hitterStatRows(player, whoHtml, sbHtml) {
    const h = player && player.hitter;
    if (!h) {
      return `<tr>${whoHtml}<td rowspan="2"></td><td>L</td><td></td><td></td><td></td><td></td><td></td></tr>
        <tr><td>R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
    }
    const rows = ['L', 'R'].map((side, i) => {
      const line = sideLine(h, side, player.id);
      const sample = line.sample || 0;
      const seasonLabel = h.src || 'LBPRC 2025';
      const seasonTitle = tr(`${seasonLabel} season line (no platoon split)`, `Línea ${seasonLabel} (sin split de platoon)`);
      const splitTitle = tr(`Synergy vs ${side}HP · ${line.sample} PA`, `Synergy vs ${side}HP · ${line.sample} PA`);
      const whTitle = line.whSrc === 'hand'
        ? tr(`Synergy whiff vs ${side}HP · ${line.whPa} PA`, `Whiff Synergy vs ${side}HP · ${line.whPa} PA`)
        : (line.whSrc === 'overall'
          ? tr('Synergy overall whiff % (hitter advance)', 'Whiff general Synergy (advance de bateadores)')
          : tr('No whiff in the hitter advance file', 'Sin whiff en el archivo de advance'));
      const cells = [
        statCell(fmtPct(line.k), toneHitter('k', line.k, h.pa), seasonTitle),
        statCell(fmtPct(line.gb), toneHitter('gb', line.gb, h.pa), seasonTitle),
        statCell(fmtPct(line.wh), toneHitter('wh', line.wh, line.whSample), whTitle),
        statCell(fmtAvg(line.avg), toneHitter('avg', line.avg, line.split ? sample : h.pa), line.split ? splitTitle : seasonTitle),
        statCell(fmtAvg(line.ops), toneHitter('ops', line.ops, line.split ? sample : h.pa), line.split ? splitTitle : seasonTitle),
      ].join('');
      if (i === 0) return `<tr>${whoHtml}<td class="sc-sb" rowspan="2">${sbHtml}</td><td class="sc-vs">${side}</td>${cells}</tr>`;
      return `<tr><td class="sc-vs">${side}</td>${cells}</tr>`;
    });
    return rows.join('');
  }

  function starterStatRows(player, whoHtml) {
    const p = player && player.pitcher;
    if (!p) {
      return `<tr>${whoHtml}<td rowspan="2"></td><td>L</td><td></td><td></td><td></td><td></td><td></td></tr>
        <tr><td>R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
    }
    const seasonLabel = p.src || '';
    const title = seasonLabel
      ? tr(`${seasonLabel} pitching line, repeated on L and R`, `Línea de pitcheo ${seasonLabel}, repetida en L y R`)
      : tr('Pitching line, repeated on L and R', 'Línea de pitcheo, repetida en L y R');
    const sample = p.bf || 0;
    const whFor = (side) => (side === 'L' && p.wh_l != null) ? p.wh_l : ((side === 'R' && p.wh_r != null) ? p.wh_r : p.wh);
    const avgTone = (v) => {
      if (v == null || sample < 20) return '';
      const n = Number(v);
      if (n <= 0.22) return 'sc-good';
      if (n >= 0.29) return 'sc-bad';
      return '';
    };
    const rowFor = (side) => [
      statCell(fmtPct(p.k), tonePitcher('k', p.k, sample), title),
      statCell(fmtPct(p.gb), tonePitcher('gb', p.gb, sample), title),
      statCell(fmtPct(whFor(side)), tonePitcher('wh', whFor(side), sample), whFor(side) == null ? tr('No whiff in the arsenal cache', 'Sin whiff en el caché de arsenal') : title),
      statCell(fmtAvg(p.avg), avgTone(p.avg), title),
      statCell(fmtAvg(p.ops), tonePitcher('ops', p.ops, sample), title),
    ].join('');
    return `<tr>${whoHtml}<td class="sc-sb" rowspan="2"></td><td class="sc-vs">L</td>${rowFor('L')}</tr><tr><td class="sc-vs">R</td>${rowFor('R')}</tr>`;
  }

  function pitcherStatRows(player, whoHtml) {
    const p = player && player.pitcher;
    const ttp = player ? esc(ttpFor(player.id)) : '';
    const ttpCell = `<td class="sc-ttp" rowspan="2">${ttp}</td>`;
    if (!p) {
      return `<tr>${whoHtml}${ttpCell}<td>L</td><td></td><td></td><td></td><td></td><td></td></tr>
        <tr><td>R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
    }
    const seasonLabel = p.src || 'LBPRC 2025';
    const title = tr(`${seasonLabel} season line, repeated on L and R (no platoon split)`, `Línea ${seasonLabel}, repetida en L y R (sin split de platoon)`);
    const sample = p.bf || 0;
    const whFor = (side) => (side === 'L' && p.wh_l != null) ? p.wh_l : ((side === 'R' && p.wh_r != null) ? p.wh_r : p.wh);
    const rowFor = (side) => [
      statCell(fmtPct(p.k), tonePitcher('k', p.k, sample), title),
      statCell(fmtPct(p.gb), tonePitcher('gb', p.gb, sample), title),
      statCell(fmtPct(whFor(side)), tonePitcher('wh', whFor(side), sample), whFor(side) == null ? tr('No whiff in the arsenal cache', 'Sin whiff en el caché de arsenal') : title),
      statCell(fmtPct(p.bb), tonePitcher('bb', p.bb, sample), title),
      statCell(fmtAvg(p.ops), tonePitcher('ops', p.ops, sample), title),
    ].join('');
    return `<tr>${whoHtml}${ttpCell}<td class="sc-vs">L</td>${rowFor('L')}</tr><tr><td class="sc-vs">R</td>${rowFor('R')}</tr>`;
  }

  function whoCell(order, pos, last, bats, rowspan, player) {
    const bits = [];
    if (order) bits.push(`<span class="sc-ord">${esc(order)}</span>`);
    if (pos) bits.push(`<span class="sc-pos">${esc(pos)}</span>`);
    const jersey = jerseyInput(player);
    if (jersey) bits.push(jersey);
    bits.push(`<span class="sc-last ${bats || ''}">${esc(last || '')}</span>`);
    return `<td class="sc-who" rowspan="${rowspan || 2}">${bits.join(' ')}</td>`;
  }

  function emptyHitterPair(order) {
    const who = whoCell(order, '', '', '', 2, null);
    return `<tr>${who}<td rowspan="2"></td><td class="sc-vs">L</td><td></td><td></td><td></td><td></td><td></td></tr>
      <tr><td class="sc-vs">R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
  }

  function gridCols() {
    return `<colgroup>
      <col class="sc-col-who"><col class="sc-col-sb"><col class="sc-col-vs">
      <col class="sc-col-stat"><col class="sc-col-stat"><col class="sc-col-stat"><col class="sc-col-stat"><col class="sc-col-stat">
    </colgroup>`;
  }
  function hitterTable(players, rowsHtml, extraClass) {
    const cls = extraClass ? `sc-grid ${extraClass}` : 'sc-grid';
    return `<table class="${cls}">
      ${gridCols()}
      <thead><tr>
        <th>${tr('PLAYER', 'JUGADOR')}</th><th>SB/SBA</th><th>VS</th><th>K</th><th>GB</th><th>WH</th><th>AVG</th><th>OPS</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  }
  function pitcherTable(rowsHtml, extraClass) {
    const cls = extraClass ? `sc-grid ${extraClass}` : 'sc-grid';
    return `<table class="${cls}">
      ${gridCols()}
      <thead><tr>
        <th>${tr('PLAYER', 'JUGADOR')}</th><th>TTP</th><th>VS</th><th>K</th><th>GB</th><th>WH</th><th>BB</th><th>OPS</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  }

  function sheetSide(abbrev, state) {
    const meta = teamMeta(abbrev);
    const people = roster(abbrev);
    const st = ensureTeam(state, abbrev);
    const hitters = people.filter((p) => p.role === 'H');
    const pitchers = people.filter((p) => p.role === 'P');
    const slots = (st.lineup || []).slice(0, 9);
    while (slots.length < 9) slots.push(null);
    const spotRows = slots.map((slot, i) => {
      if (!slot) return emptyHitterPair(String(i + 1));
      const player = byId.get(Number(slot.id));
      if (!player) return emptyHitterPair(String(i + 1));
      const who = whoCell(String(i + 1), posName(slot.pos), cardLast(player, hitters), batsClass(player), 2, player);
      return hitterStatRows(player, who, esc(sbText(player.hitter)));
    });
    const sp = st.sp ? byId.get(Number(st.sp)) : null;
    const pitcherWho = sp
      ? whoCell('', 'P', cardLast(sp, pitchers), batsClass(sp), 2, sp)
      : whoCell('', 'P', '—', '', 2, null);
    // Pitcher always after batting order #9 — never mid-lineup (e.g. after DH).
    const pitcherRow = starterStatRows(sp, pitcherWho);
    const hitterRows = spotRows.join('') + pitcherRow;
    const benchSlots = st.bench.length ? st.bench : [null];
    const benchRows = benchSlots.map((slot) => {
      if (!slot) return emptyHitterPair('PH');
      const player = byId.get(Number(slot.id));
      if (!player) return emptyHitterPair('PH');
      const who = whoCell('PH', posName(slot.pos), cardLast(player, hitters), batsClass(player), 2, player);
      return hitterStatRows(player, who, esc(sbText(player.hitter)));
    }).join('');

    const penIds = st.pen.filter((id) => Number(id) !== Number(st.sp));
    const pen = penIds.map((id) => byId.get(Number(id))).filter(Boolean).sort((a, b) => a.last.localeCompare(b.last));
    let pitRows = '';
    if (sp) {
      const who = whoCell('SP', '', cardLast(sp, pitchers), batsClass(sp), 2, sp);
      pitRows += pitcherStatRows(sp, who);
    } else {
      pitRows += `<tr>${whoCell('SP', '', '—', '', 2, null)}<td rowspan="2"></td><td class="sc-vs">L</td><td></td><td></td><td></td><td></td><td></td></tr><tr><td class="sc-vs">R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
    }
    pitRows += `<tr class="sc-subhead"><td colspan="8">BULLPEN</td></tr>`;
    if (!pen.length) {
      pitRows += `<tr>${whoCell('RP', '', '', '', 2, null)}<td rowspan="2"></td><td class="sc-vs">L</td><td></td><td></td><td></td><td></td><td></td></tr><tr><td class="sc-vs">R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
    } else {
      pen.forEach((p) => {
        const who = whoCell('RP', '', cardLast(p, pitchers), batsClass(p), 2, p);
        pitRows += pitcherStatRows(p, who);
      });
    }

    const logo = meta.logo
      ? `<img class="sc-logo" src="${esc(meta.logo)}" alt="">`
      : `<span class="sc-mono">${esc(meta.abbrev)}</span>`;
    const dateStr = new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
    return `<section class="sc-side">
      <header class="sc-head" style="background:${esc(meta.color)}">
        <div class="sc-head-name">${esc(meta.name)}</div>
        ${logo}
      </header>
      <div class="sc-date">${esc(dateStr)}</div>
      <div class="sc-bar">HITTERS</div>
      ${hitterTable(hitters, hitterRows, 'sc-grid-lineup')}
      <div class="sc-bar">BENCH / PH</div>
      ${hitterTable(hitters, benchRows, 'sc-grid-bench')}
      <div class="sc-bar">PITCHERS</div>
      ${pitcherTable(pitRows, 'sc-grid-pen')}
    </section>`;
  }

  function posOptions(selected, taken) {
    const block = taken || new Set();
    return STARTER_POS.filter((code) => code === selected || !block.has(code)).map((code) => {
      const name = POS_LABEL[code] || code;
      const label = code === 'DH' ? 'DH' : code + ' ' + name;
      return `<option value="${code}"${code === selected ? ' selected' : ''}>${label}</option>`;
    }).join('');
  }

  const NAME_SUFFIXES = new Set(['jr', 'sr', 'ii', 'iii', 'iv', 'v']);
  const NAME_PARTICLES = new Set(['de', 'del', 'la', 'los', 'las', 'da', 'dos', 'van', 'von']);
  function lastComma(name) {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    const suffix = [];
    while (parts.length && NAME_SUFFIXES.has(parts[parts.length - 1].toLowerCase().replace(/\./g, ''))) suffix.unshift(parts.pop());
    if (!parts.length) return suffix.join(' ');
    let n = 1;
    if (parts.length >= 3 && NAME_PARTICLES.has(parts[parts.length - 3].toLowerCase()) && NAME_PARTICLES.has(parts[parts.length - 2].toLowerCase())) n = 3;
    else if (parts.length >= 2 && NAME_PARTICLES.has(parts[parts.length - 2].toLowerCase())) n = 2;
    const last = parts.slice(-n).join(' ');
    const first = parts.slice(0, -n).join(' ');
    const lastBit = suffix.length ? `${last} ${suffix.join(' ')}`.trim() : last;
    return first ? `${lastBit}, ${first}` : lastBit;
  }

  function playerOptions(hitters, selectedId, blocked) {
    const blank = `<option value="">${tr('Select hitter', 'Elegir bateador')}</option>`;
    const opts = hitters.slice().sort((a, b) => a.last.localeCompare(b.last) || a.name.localeCompare(b.name)).map((p) => {
      if (blocked.has(p.id) && p.id !== selectedId) return '';
      return `<option value="${p.id}"${p.id === selectedId ? ' selected' : ''}>${esc(lastComma(p.name))}</option>`;
    }).join('');
    return blank + opts;
  }

  function hitterSlot(abbrev, listName, row, hitters, blocked, taken) {
    const selected = row ? row.id : '';
    const pos = row ? row.pos : 'DH';
    const drag = listName === 'lineup' && row
      ? `draggable="true" data-id="${row.id}" data-team="${esc(abbrev)}" ondragstart="CardApp.dragStart(event)" ondragover="CardApp.dragOver(event)" ondrop="CardApp.dropLineup(event)"`
      : '';
    return `<li ${drag}>
      ${listName === 'lineup' && row ? `<span class="sc-grip" title="${tr('Drag to reorder', 'Arrastra para ordenar')}">↕</span>` : ''}
      <select class="sc-pos-select" onmousedown="event.stopPropagation()" ${row ? '' : 'disabled'} onchange="CardApp.setPos('${esc(abbrev)}', '${listName}', ${row ? row.id : 0}, this.value)">${posOptions(pos, listName === 'lineup' ? taken : null)}</select>
      <select class="sc-name-select" onmousedown="event.stopPropagation()" onchange="CardApp.setRowPlayer('${esc(abbrev)}', '${listName}', ${row ? row.id : 0}, this.value)">${playerOptions(hitters, selected, blocked)}</select>
    </li>`;
  }

  function byLast(a, b) {
    return a.last.localeCompare(b.last) || a.name.localeCompare(b.name);
  }
  function editorSide(abbrev, state) {
    const st = ensureTeam(state, abbrev);
    const people = roster(abbrev);
    const hitters = people.filter((p) => p.role === 'H').slice().sort(byLast);
    const pitchers = people.filter((p) => p.role === 'P').slice().sort(byLast);
    const slots = (st.lineup || []).slice(0, 9);
    while (slots.length < 9) slots.push(null);
    const lineupIds = new Set(slots.filter(Boolean).map((r) => r.id));
    const benchIds = new Set(st.bench.map((r) => r.id));
    const lineupFull = lineupIds.size >= 9;
    const hitterChecks = hitters.map((p) => {
      const on = lineupIds.has(p.id);
      const dis = !on && lineupFull ? ' disabled' : '';
      return `<label class="sc-roster-row">
        <input type="checkbox" ${on ? 'checked' : ''}${dis} onchange="CardApp.toggleLineup('${esc(abbrev)}', ${p.id}, this.checked)">
        <span draggable="true" data-id="${p.id}" data-team="${esc(abbrev)}" ondragstart="CardApp.dragStart(event)">${esc(lastComma(p.name))}</span>
      </label>`;
    }).join('');
    const starterSelect = `<select class="sc-sp-select" onchange="CardApp.setSp('${esc(abbrev)}', this.value)">
      <option value="">${tr('Select starter', 'Elegir abridor')}</option>
      ${pitchers.map((p) => `<option value="${p.id}"${Number(st.sp) === Number(p.id) ? ' selected' : ''}>${esc(lastComma(p.name))}</option>`).join('')}
    </select>`;
    const spots = slots.map((row, i) => {
      const taken = new Set(slots.filter((other) => other && (!row || other.id !== row.id)).map((other) => other.pos));
      const player = row ? byId.get(Number(row.id)) : null;
      const body = player
        ? `<span class="sc-edit-name" draggable="true" data-id="${row.id}" data-team="${esc(abbrev)}" ondragstart="CardApp.dragStart(event)">${esc(lastComma(player.name))}</span>
           <select class="sc-pos-select" onmousedown="event.stopPropagation()" onchange="CardApp.setPos('${esc(abbrev)}', 'lineup', ${row.id}, this.value)">${posOptions(row.pos, taken)}</select>`
        : `<span class="sc-empty">${tr('Drop hitter', 'Suelta un bateador')}</span>`;
      return `<li class="sc-spot${player ? ' sc-filled' : ''}" data-slot="${i}" data-team="${esc(abbrev)}" ondragover="CardApp.dragOver(event)" ondrop="CardApp.dropOnSpot(event)">
        <span class="sc-spot-num">${i + 1}</span>
        ${body}
      </li>`;
    }).join('');
    const benchChecks = hitters.filter((p) => !lineupIds.has(p.id)).map((p) => {
      const on = benchIds.has(p.id);
      return `<label class="sc-roster-row">
        <input type="checkbox" ${on ? 'checked' : ''} onchange="CardApp.toggleBench('${esc(abbrev)}', ${p.id}, this.checked)">
        <span>${esc(lastComma(p.name))}</span>
      </label>`;
    }).join('') || `<div class="sc-empty">${tr('Every hitter is in the lineup.', 'Todos los bateadores están en la alineación.')}</div>`;
    const penChecks = pitchers.filter((p) => p.id !== st.sp).map((p) => {
      const on = st.pen.includes(p.id);
      return `<label class="sc-roster-row">
        <input type="checkbox" ${on ? 'checked' : ''} onchange="CardApp.togglePen('${esc(abbrev)}', ${p.id}, this.checked)">
        <span>${esc(lastComma(p.name))}</span>
      </label>`;
    }).join('') || `<div class="sc-empty">${tr('No other pitchers.', 'No hay más pitchers.')}</div>`;
    return `<div class="sc-editor">
      <div class="sc-editor-h">${esc(teamMeta(abbrev).abbrev)} · ${tr('Today\'s card', 'Carta de hoy')}</div>
      <div class="sc-editor-label">${tr('Lineup — check 9 hitters', 'Alineación — marca 9 bateadores')}</div>
      <div class="sc-roster-list">${hitterChecks}</div>
      <div class="sc-editor-label">${tr('Order — drag a name onto a spot, then pick a position', 'Orden — arrastra un nombre a un turno y elige posición')}</div>
      <ol class="sc-spots">${spots}</ol>
      <div class="sc-editor-label">${tr('Starter — one pitcher. Prints after #9. Does not hit.', 'Abridor — un pitcher. Va después del #9. No batea.')}</div>
      <div class="sc-sp-wrap">${starterSelect}</div>
      <div class="sc-editor-label">${tr('Bench — hitters left off the lineup', 'Banca — bateadores fuera de la alineación')}</div>
      <div class="sc-roster-list">${benchChecks}</div>
      <div class="sc-editor-label">${tr('Bullpen — pitchers other than the starter', 'Bullpen — pitchers que no abren')}</div>
      <div class="sc-roster-list">${penChecks}</div>
      <div class="sc-editor-actions">
        <button type="button" onclick="CardApp.fillBench('${esc(abbrev)}')">${tr('Rest to bench', 'Resto a la banca')}</button>
        <button type="button" onclick="CardApp.checkAllPen('${esc(abbrev)}', true)">${tr('Rest to pen', 'Resto al bullpen')}</button>
        <button type="button" onclick="CardApp.clearLineup('${esc(abbrev)}')">${tr('Clear lineup', 'Vaciar alineación')}</button>
        <button type="button" onclick="CardApp.clearBench('${esc(abbrev)}')">${tr('Clear bench', 'Vaciar banca')}</button>
        <button type="button" onclick="CardApp.clearSp('${esc(abbrev)}')">${tr('Clear SP', 'Quitar abridor')}</button>
        <button type="button" onclick="CardApp.checkAllPen('${esc(abbrev)}', false)">${tr('Clear pen', 'Vaciar bullpen')}</button>
      </div>
    </div>`;
  }

  function renderStrategy() {
    const root = document.getElementById('strategy-root');
    if (!root) return;
    if (!DATA) {
      root.innerHTML = `<div class="note">${tr('Loading card rosters…', 'Cargando rosters…')}</div>`;
      return;
    }
    const state = loadStrategy();
    const opponents = ((DATA && DATA.teams) || []).filter((t) => t.abbrev !== 'SAN');
    if (!opponents.some((t) => t.abbrev === state.opponent)) state.opponent = opponents[0] ? opponents[0].abbrev : 'CAG';
    const left = state.santurceHome ? 'SAN' : state.opponent;
    const right = state.santurceHome ? state.opponent : 'SAN';
    const oppOpts = opponents.map((t) => `<option value="${esc(t.abbrev)}"${t.abbrev === state.opponent ? ' selected' : ''}>${esc(t.name)}</option>`).join('');
    const gaps = (DATA && DATA.gaps) || {};
    root.innerHTML = `
      <div class="sc-toolbar">
        <div class="sc-home-toggle" role="group" aria-label="Santurce home or away">
          <span>${tr('Santurce', 'Santurce')}</span>
          <button type="button" class="${state.santurceHome ? 'on' : ''}" onclick="CardApp.setHome(true)">${tr('Home', 'Home')}</button>
          <button type="button" class="${state.santurceHome ? '' : 'on'}" onclick="CardApp.setHome(false)">${tr('Away', 'Visita')}</button>
        </div>
        <p class="sc-legend">${tr(
          'Green = a strength, red = a soft spot. Colors need 15+ PA (or 15+ swings for WH). High GB is red (roll-over); low GB is green. K/GB are the season line on both hands. WH is Synergy whiff vs that hand, or overall when that side has no swings. AVG/OPS use a platoon split at 15+ PA. TTP is time-to-home from the pop-times tab.',
          'Verde = fortaleza, rojo = punto débil. Los colores piden 15+ PA (o 15+ swings para WH). GB alto es rojo (rodado); GB bajo es verde. K/GB son la línea de temporada en ambas manos. WH es el whiff Synergy contra esa mano, o el general si ese lado no tiene swings. AVG/OPS usan el split con 15+ PA. TTP es el tiempo al home de la pestaña de pop times.'
        )}</p>
        <div class="sc-toolbar-right">
          <label>${tr('Home team', 'Equipo de home')}
            <select onchange="CardApp.setHomeTeam(this.value)">
              <option value="SAN"${state.santurceHome ? ' selected' : ''}>${esc(teamMeta('SAN').name)}</option>
              <option value="${esc(state.opponent)}"${state.santurceHome ? '' : ' selected'}>${esc(teamMeta(state.opponent).name)}</option>
            </select>
          </label>
          <label>${tr('Opponent', 'Rival')}
            <select onchange="CardApp.setOpponent(this.value)">${oppOpts}</select>
          </label>
          <button type="button" class="sc-export" onclick="CardApp.exportStrategy()">${tr('Export PDF', 'Exportar PDF')}</button>
          <button type="button" class="sc-export" onclick="publishGameDayNow()" title="${tr('Push lineup / notes / jersey / overrides to the live site JSON', 'Publicar alineación / notas / dorsales al JSON del sitio')}">${tr('Publish lineup', 'Publicar alineación')}</button>
        </div>
      </div>
      <p class="note" id="gameday-persist-note" style="margin:6px 0 0"></p>
      <p class="note sc-gap">${esc(gaps.pitcher_platoon || '')} ${esc(gaps.hitter_platoon_k_gb || '')}</p>
      <div class="sc-editors">
        ${editorSide(left, state)}
        ${editorSide(right, state)}
      </div>
      <div class="sc-pair" id="strategy-sheet">
        ${sheetSide(left, state)}
        ${sheetSide(right, state)}
      </div>`;
    // Do not persist on render. ensureTeam drops IDs that are not on the current
    // roster lookup; writing that back is what emptied SAN after hydrate and
    // stripped Ponce bench rows that are not in card-stats.
  }

  function band(n) {
    if (n == null || !isFinite(Number(n))) return null;
    const v = Number(n);
    if (v > 5) return 'freq';
    if (v >= 2) return 'occ';
    return 'rare';
  }
  function bandWord(b) {
    if (b === 'freq') return tr('Frequent', 'Frecuente');
    if (b === 'occ') return tr('Occasional', 'Ocasional');
    if (b === 'rare') return tr('Rare', 'Raro');
    return '';
  }
  function sacTotal(h) {
    if (!h || (h.sac == null && !h.summer_sac)) return null;
    return (h.sac || 0) + (h.summer_sac || 0);
  }
  function stealScore(h) {
    const line = summerSteal(h);
    return line ? line.sb : 0;
  }
  function groupScore(h) {
    if (!h) return 0;
    return Math.max(stealScore(h), sacTotal(h) || 0, h.bunts || 0);
  }
  function skewed(a, b) {
    return a >= 3 && a >= 2 * Math.max(b, 0) && (a - b) >= 2;
  }

  function runNotes() {
    return { text: '', major: false };
  }

  function buntNotes(h) {
    if (!h) return { text: '', major: false };
    const bits = [];
    let major = false;
    if (h.in_synergy) {
      const risp = h.bunts_risp || 0;
      const other = Math.max(0, (h.bunts || 0) - risp);
      if (skewed(risp, other)) {
        major = true;
        bits.push(tr(
          `Bunches bunts with RISP (${risp}) much more than other situations (${other}).`,
          `Toque mucho más con corredores en posición de anotar (${risp}) que en otras situaciones (${other}).`
        ));
      } else if (skewed(other, risp)) {
        major = true;
        bits.push(tr(
          `Bunches bunts without RISP (${other}) much more than with RISP (${risp}).`,
          `Toque mucho más sin corredores en posición de anotar (${other}) que con ellos (${risp}).`
        ));
      }
      const vl = h.bunts_vl || 0;
      const vr = h.bunts_vr || 0;
      if (skewed(vl, vr)) {
        major = true;
        bits.push(tr(
          `Bunts much more vs LHP (${vl}) than vs RHP (${vr}).`,
          `Toque mucho más vs zurdos (${vl}) que vs derechos (${vr}).`
        ));
      } else if (skewed(vr, vl)) {
        major = true;
        bits.push(tr(
          `Bunts much more vs RHP (${vr}) than vs LHP (${vl}).`,
          `Toque mucho más vs derechos (${vr}) que vs zurdos (${vl}).`
        ));
      }
    }
    if (h.sac != null || h.summer_sac || (h.summer_levels || []).some((row) => row.sac)) {
      const parts = [];
      if (h.sac != null) parts.push(`LBPRC: ${h.sac}`);
      const levels = (h.summer_levels || []).filter((row) => row.sac);
      if (levels.length) levels.forEach((row) => parts.push(`${row.league}: ${row.sac}`));
      else if (h.summer_sac) {
        const src = (h.summer_sources || []).filter((s) => s && s !== 'confirmed').join('/') || '2026';
        parts.push(`${src}: ${h.summer_sac}`);
      }
      bits.push(tr(`Sac bunts ${parts.join(' ')}.`, `Toques de sacrificio ${parts.join(' ')}.`));
    }
    if (h.in_synergy && h.bunts != null && !major) {
      bits.push(tr(`Synergy bunt attempts: ${h.bunts}.`, `Intentos de toque Synergy: ${h.bunts}.`));
    }
    return { text: bits.join(' '), major };
  }

  function aggLabel(h) {
    const line = summerSteal(h);
    if (!line) return '';
    const att = line.sb + line.cs;
    const pct = att ? line.sb / att : 0;
    if (att >= 8 && pct >= 0.7) return 'AGG';
    if (att >= 2) return tr('Will pick his spot', 'Elige el momento');
    return tr('Not', 'No');
  }

  function countCell(n, extra, title) {
    const b = band(n);
    if (b == null) return `<td title="${esc(title || '')}">—</td>`;
    const extraHtml = extra ? `<small class="sb-extra">${extra}</small>` : '';
    return `<td class="sb-${b}" title="${esc(title || bandWord(b))}"><b>${esc(String(n))}</b><small>${esc(bandWord(b))}</small>${extraHtml}</td>`;
  }

  function sbCells(h) {
    const text = sbText(h);
    if (!text) return '<td colspan="2">—</td>';
    const b = band(stealScore(h));
    const cls = b ? `sb-${b}` : '';
    const title = tr('2026 summer stolen bases / attempts', 'Bases robadas / intentos, verano 2026');
    return `<td class="${cls}" colspan="2" title="${esc(title)}"><b>${esc(text)}</b></td>`;
  }

  function sacText(h) {
    if (!h) return '';
    const bits = [];
    if (h.sac != null) bits.push(`LBPRC: ${h.sac}`);
    const levels = (h.summer_levels || []).filter((row) => row.sac);
    if (levels.length) levels.forEach((row) => bits.push(`${row.league}: ${row.sac}`));
    else if (h.summer_sac) {
      const src = (h.summer_sources || []).filter((s) => s && s !== 'confirmed').join('/') || '2026';
      bits.push(`${src}: ${h.summer_sac}`);
    }
    return bits.join(' ');
  }
  function sacCell(h) {
    const text = sacText(h);
    if (!text) return '<td>—</td>';
    const n = sacTotal(h);
    const b = band(n);
    const cls = b ? `sb-${b}` : '';
    const title = tr(
      'Sacrifice bunts. LBPRC is the winter line. The other label is the league: MLB, AAA, AA, A+, A, LMB, or Indy.',
      'Toques de sacrificio. LBPRC es la línea de invierno. La otra etiqueta es la liga: MLB, AAA, AA, A+, A, LMB o Indy.'
    );
    const lines = [`<b>${esc(text)}</b>`];
    if (b) lines.push(`<small>${esc(bandWord(b))}</small>`);
    return `<td class="${cls}" title="${esc(title)}">${lines.join('')}</td>`;
  }
  function hitCell(h) {
    if (!h || !h.in_synergy || h.bunts == null) {
      return `<td title="${esc(tr('Bunt-for-hit is not split out. Synergy bunt attempts show here when the player is in the advance file.', 'El toque de hit no está separado. Los intentos Synergy aparecen aquí si el jugador está en el archivo de advance.'))}">—</td>`;
    }
    return countCell(h.bunts, '', tr('Synergy bunt attempts (sac vs hit is not separated)', 'Intentos de toque Synergy (sacrificio vs hit no está separado)'));
  }

  function renderSmallBall() {
    const root = document.getElementById('smallball-root');
    if (!root) return;
    if (!DATA) {
      root.innerHTML = `<div class="note">${tr('Loading card rosters…', 'Cargando rosters…')}</div>`;
      return;
    }
    const notesState = loadNotes();
    const teams = (DATA && DATA.teams) || [];
    if (!teams.some((t) => t.abbrev === notesState.team)) notesState.team = teams[0] ? teams[0].abbrev : 'SAN';
    const abbrev = notesState.team;
    const meta = teamMeta(abbrev);
    const hitters = roster(abbrev).filter((p) => p.role === 'H').map((p) => ({ p, score: groupScore(p.hitter), band: band(groupScore(p.hitter)) || 'rare' }));
    const groups = [
      ['freq', tr('FREQUENT  >5', 'FRECUENTE  >5')],
      ['occ', tr('OCCASIONAL  2–5', 'OCASIONAL  2–5')],
      ['rare', tr('RARE / NONE  <2', 'RARO / NADA  <2')],
    ];
    let body = '';
    groups.forEach(([key, label]) => {
      const rows = hitters.filter((r) => r.band === key).sort((a, b) => b.score - a.score || a.p.last.localeCompare(b.p.last));
      if (!rows.length) return;
      body += `<tr class="sb-group sb-${key}"><td colspan="9">${esc(label)}</td></tr>`;
      rows.forEach((r, i) => {
        const h = r.p.hitter;
        const run = runNotes(h);
        const bunt = buntNotes(h);
        const stripe = i % 2 ? 'sb-alt' : '';
        body += `<tr class="${stripe}">
          <td class="sb-player">${esc(r.p.name)}</td>
          <td class="sb-spd" title="${esc(tr('No sprint speed in the cache', 'No hay velocidad de sprint en el caché'))}"></td>
          ${sbCells(h)}
          <td class="sb-agg">${esc(aggLabel(h))}</td>
          <td class="sb-note${run.major ? ' sb-major' : ''}">${esc(run.text)}</td>
          ${sacCell(h)}
          ${hitCell(h)}
          <td class="sb-note${bunt.major ? ' sb-major' : ''}">${esc(bunt.text)}</td>
        </tr>`;
      });
    });
    const opts = teams.map((t) => `<option value="${esc(t.abbrev)}"${t.abbrev === abbrev ? ' selected' : ''}>${esc(t.name)}</option>`).join('');
    const logo = meta.logo ? `<img src="${esc(meta.logo)}" alt="">` : `<span class="sc-mono">${esc(meta.abbrev)}</span>`;
    const dateStr = new Date().toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', year: '2-digit' });
    const noteVal = notesState.notes[abbrev] || '';
    root.innerHTML = `
      <div class="sc-toolbar">
        <p class="sc-legend">${tr(
          'SB/SBA is the 2026 summer line only, stolen bases over attempts. SAC = sacrifice bunts. Frequent >5 green, occasional 2–5 white, rare <2 red. A bunt note turns red when one situation is much more common than the other.',
          'BR/INT es solo la línea de verano 2026, bases robadas sobre intentos. SAC = toques de sacrificio. Frecuente >5 verde, ocasional 2–5 blanco, raro <2 rojo. La nota de toque se pone roja cuando una situación es mucho más común que la otra.'
        )}</p>
        <div class="sc-toolbar-right">
          <label>${tr('Team', 'Equipo')}
            <select onchange="CardApp.setSbTeam(this.value)">${opts}</select>
          </label>
          <button type="button" class="sc-export" onclick="CardApp.exportSmallBall()">${tr('Export PDF', 'Exportar PDF')}</button>
        </div>
      </div>
      <div id="smallball-sheet" class="sb-sheet">
        <div class="sb-top">
          <div class="sb-teamhead" style="background:${esc(meta.color)}">
            ${logo}
            <div>
              <div class="sb-teamname">${esc(meta.name)}</div>
              <div class="sb-date">${esc(dateStr)}</div>
            </div>
          </div>
          <div class="sb-title">SMALL BALL</div>
        </div>
        <table class="sb-table">
          <thead>
            <tr class="sb-band">
              <th colspan="6">BASERUNNING</th>
              <th colspan="3">BUNTING</th>
            </tr>
            <tr class="sb-cols">
              <th>Player</th><th>SPD</th><th colspan="2">SB/SBA</th><th>AGG</th><th>NOTES</th><th>SAC</th><th>HIT</th><th>NOTES</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
          <tfoot>
            <tr class="sb-teamnotes">
              <th>TEAM NOTES</th>
              <td colspan="8"><textarea rows="3" oninput="CardApp.saveTeamNotes('${esc(abbrev)}', this.value)">${esc(noteVal)}</textarea></td>
            </tr>
          </tfoot>
        </table>
      </div>`;
  }

  function refresh() {
    if (document.getElementById('panel-strategy')?.classList.contains('active')) renderStrategy();
    if (document.getElementById('panel-br')?.classList.contains('active')) renderSmallBall();
  }

  function queuePersist() {
    try {
      if (typeof window.queueGameDayPublish === 'function') window.queueGameDayPublish();
    } catch (e) {}
  }

  function withStrategy(fn) {
    const x = window.scrollX;
    const y = window.scrollY;
    const editors = [...document.querySelectorAll('.sc-editor')].map((el) => el.scrollTop);
    const state = loadStrategy();
    fn(state);
    saveStrategy(state);
    queuePersist();
    renderStrategy();
    // Keep hitter / spray PDF export lists + INF/OF cards in strategy lineup order.
    try {
      if (typeof window.fillAdvanceHitters === 'function'
          && document.getElementById('panel-hitter-advance')?.classList.contains('active')) {
        window.fillAdvanceHitters();
      }
      if (typeof window.renderSpray === 'function'
          && document.getElementById('panel-spray')?.classList.contains('active')) {
        window.renderSpray();
      }
      if (window.PosApp && typeof PosApp.render === 'function') PosApp.render();
      else if (typeof window.renderOfPos === 'function') window.renderOfPos();
    } catch (e) {}
    const restore = () => {
      window.scrollTo(x, y);
      document.querySelectorAll('.sc-editor').forEach((el, i) => { el.scrollTop = editors[i] || 0; });
    };
    restore();
    requestAnimationFrame(restore);
  }

  const CARD_CSS = `
    html,body{margin:0;background:#fff;color:#111}
    body{font-family:Arial,Helvetica,sans-serif}
    .sc-pair{display:grid;grid-template-columns:1fr 1fr;gap:8px;align-items:start}
    .sc-side{background:#fff;color:#111;border:1px solid #c5c9d0}
    .sc-head{display:flex;align-items:center;justify-content:space-between;color:#fff;padding:6px 10px;min-height:42px}
    .sc-head-name{font-weight:800;font-size:15px;letter-spacing:.01em}
    .sc-logo{width:34px;height:34px;object-fit:contain;background:#fff;border-radius:50%}
    .sc-mono{font-weight:800;font-size:12px;border:1px solid rgba(255,255,255,.7);border-radius:50%;width:34px;height:34px;display:flex;align-items:center;justify-content:center}
    .sc-date{text-align:center;font-size:11px;color:#444;padding:3px 0 4px}
    .sc-bar{background:#d5d8de;color:#222;text-align:center;font-weight:800;font-size:11px;letter-spacing:.08em;padding:3px 0;border-top:1px solid #b7bcc6;border-bottom:1px solid #b7bcc6}
    .sc-pair{min-width:0}
    .sc-side{min-width:0;overflow:hidden}
    .sc-grid{width:100%;border-collapse:collapse;font-size:8px;line-height:1.15;table-layout:fixed}
    .sc-col-who{width:34%}
    .sc-col-sb{width:11%}
    .sc-col-vs{width:6%}
    .sc-col-stat{width:9.8%}
    .sc-grid th{background:#e6e8ec;color:#222;font-weight:700;font-size:7px;letter-spacing:.02em;padding:2px 1px;border:1px solid #c5c9d0;text-align:center;overflow:hidden}
    .sc-grid td{border:1px solid #e1e4ea;padding:1px 2px;text-align:center;background:#fff;color:#111;white-space:nowrap;overflow:hidden}
    .sc-ttp{color:#111;font-weight:800;-webkit-text-fill-color:#111}
    .sc-grid td.sc-who,.sc-who{text-align:left !important;white-space:nowrap;font-weight:700;color:#111;-webkit-text-fill-color:#111;overflow:hidden;text-overflow:ellipsis;max-width:0;width:34%}
    .sc-side .sc-grid td{color:#111;-webkit-text-fill-color:#111}
    .sc-good{color:#fff!important;-webkit-text-fill-color:#fff!important}
    .sc-bad{color:#fff!important;-webkit-text-fill-color:#fff!important}
    .sc-lhb{color:#c0392b!important;-webkit-text-fill-color:#c0392b!important}
    .sc-shb{color:#1a5276!important;-webkit-text-fill-color:#1a5276!important}
    .sc-jersey-print{display:inline-block;min-width:1.2em;margin-right:2px;font-weight:800}
    .sc-sb{overflow:hidden}
    .sc-ord,.sc-pos{display:inline-block;min-width:1em;margin-right:2px;font-weight:800}
    .sc-last{font-weight:800}
    .sc-vs{font-weight:700;color:#333;-webkit-text-fill-color:#333}
    .sc-good{background:#2e9e4f !important;color:#fff !important;-webkit-text-fill-color:#fff!important;font-weight:800}
    .sc-bad{background:#e04b4b !important;color:#fff !important;-webkit-text-fill-color:#fff!important;font-weight:800}
    .sc-subhead td{background:#eceff3 !important;text-align:left !important;font-weight:800;letter-spacing:.06em;font-size:9px}
    .sb-sheet{background:#fff;color:#111;border:1px solid #c5c9d0}
    .sb-top{display:grid;grid-template-columns:1.1fr 1fr}
    .sb-teamhead{display:flex;gap:8px;align-items:center;color:#fff;padding:8px 10px}
    .sb-teamhead img{width:36px;height:36px;object-fit:contain;background:#fff;border-radius:50%}
    .sb-teamname{font-weight:800;font-size:16px}
    .sb-date{font-size:12px;opacity:.9}
    .sb-title{background:#111;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;letter-spacing:.14em;font-size:18px}
    .sb-table{width:100%;border-collapse:collapse;font-size:10px;table-layout:fixed}
    .sb-table th,.sb-table td{border:1px solid #d5d8de;padding:3px 4px;vertical-align:middle;text-align:center}
    .sb-band th{background:#b42318;color:#fff;letter-spacing:.08em;font-size:11px}
    .sb-cols th{background:#c0392b;color:#fff;font-size:10px}
    .sb-player{text-align:left !important;font-weight:700}
    .sb-alt td{background:#f3f7fb}
    .sb-group td{font-weight:800;letter-spacing:.06em;text-align:left !important}
    .sb-freq,.sb-group.sb-freq td{background:#1e8f45 !important;color:#fff !important}
    .sb-occ{background:#fff !important;color:#111 !important}
    .sb-group.sb-occ td{background:#f4f4f4 !important;color:#111 !important}
    .sb-rare,.sb-group.sb-rare td{background:#d64545 !important;color:#fff !important}
    .sb-major{background:#c0392b !important;color:#fff !important;font-weight:700}
    .sb-note{text-align:left !important;font-size:9px;line-height:1.25;white-space:normal !important}
    .sb-spd{background:#eef2f6}
    .sb-table small{display:block;font-size:8px;font-weight:700;letter-spacing:.02em}
    .sb-extra{font-weight:600 !important}
    .sb-agg{font-size:9px}
    .sb-teamnotes th{background:#f6e27a;color:#111;font-weight:800;letter-spacing:.04em}
    .sb-teamnotes td{background:#fff8e8;text-align:left !important}
    .sb-teamnotes textarea{width:100%;border:none;background:transparent;font:inherit;font-size:11px;resize:vertical;color:#111}
    .sc-front-page{display:flex;flex-direction:column;box-sizing:border-box}
    .sc-front-body{flex:1 1 auto;min-height:0;overflow:hidden}
    .sc-front-scale{display:block;transform-origin:top left}
    .sc-front-notes{flex:0 0 1.5in;height:1.5in;border:1px solid #c5c9d0;background:#fff;display:flex;flex-direction:column;overflow:hidden;margin-top:auto}
    .sc-front-notes .sc-bar{flex:0 0 auto}
    .sc-back{display:flex;flex-direction:column;gap:14px;box-sizing:border-box}
    .sc-ttp-table{width:100%;border-collapse:collapse;font-size:14px;table-layout:fixed;color:#111}
    .sc-ttp-table th{background:#e6e8ec;border:1px solid #c5c9d0;padding:6px 8px;font-size:11px;letter-spacing:.04em;color:#111}
    .sc-ttp-table td{border:1px solid #e1e4ea;padding:7px 8px;background:#fff;color:#111}
    .sc-ttp-role{width:12%;font-weight:800;text-align:center;color:#111}
    .sc-ttp-name{width:58%;text-align:left;font-weight:700;color:#111}
    .sc-ttp-val{width:30%;text-align:center;font-weight:800;font-size:16px;letter-spacing:.02em;color:#111}
    .sc-back-lines{flex:1 1 auto;background:repeating-linear-gradient(to bottom,#fff 0,#fff 21px,#e8ebf0 22px);min-height:1.1in}
    @media print { .sc-good,.sc-bad,.sb-freq,.sb-rare,.sb-major,.sb-band th,.sb-cols th,.sc-head,.sb-teamhead,.sb-title,.sb-teamnotes th{-webkit-print-color-adjust:exact;print-color-adjust:exact} }
  `;

  function sideNaturalHeight(front) {
    let max = 0;
    front.querySelectorAll('.sc-side').forEach((side) => {
      const r = side.getBoundingClientRect().height;
      max = Math.max(max, r || side.scrollHeight || 0);
    });
    return max;
  }
  function clearScale(el) {
    if (!el) return;
    el.style.zoom = '1';
    el.style.transform = '';
    el.style.transformOrigin = 'top left';
    el.style.marginBottom = '';
    el.style.width = '';
  }
  function clearWhoHeights(root) {
    if (!root) return;
    root.querySelectorAll('td.sc-who').forEach((td) => {
      td.style.height = '';
      td.style.verticalAlign = '';
    });
  }
  /** Put leftover pixels into player-pair row height (rowspan-2 .sc-who cells). */
  function addWhoHeights(table, extraPx) {
    if (!table || extraPx <= 0) return;
    const whos = [...table.querySelectorAll('td.sc-who')];
    if (!whos.length) return;
    const add = extraPx / whos.length;
    whos.forEach((td) => {
      const cs = getComputedStyle(td);
      const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
      const border = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
      const content = td.getBoundingClientRect().height - pad - border;
      td.style.height = `${Math.max(content + add, content)}px`;
      td.style.verticalAlign = 'middle';
    });
  }
  /** CSS zoom when the engine honors it; transform + negative margin otherwise. */
  function applyZoom(el, z) {
    if (!el) return;
    z = Math.max(0.22, Math.min(1, z));
    clearScale(el);
    if (z >= 0.995) return;
    el.style.zoom = String(z);
    const layoutH = el.getBoundingClientRect().height;
    const natural = el.scrollHeight || layoutH;
    if (layoutH > natural * z + 6) {
      el.style.zoom = '1';
      el.style.transform = `scale(${z})`;
      el.style.width = `${100 / z}%`;
      el.style.marginBottom = `${(z - 1) * natural}px`;
    }
  }

  /**
   * Grow every pair in a column (lineup, bench, and pen together) so that
   * column meets the notes. Each side sizes itself — a shorter pen must
   * not leave a hole above the band. max-width:0 on the name cell is what
   * lets the row height actually increase.
   */
  function growSide(side, extraVisualPx, zoom) {
    if (!side || extraVisualPx <= 2) return;
    const z = zoom > 0 ? zoom : 1;
    const whos = [...side.querySelectorAll('td.sc-who')];
    if (!whos.length) return;
    const add = (extraVisualPx / whos.length) / z;
    whos.forEach((td) => {
      const cs = getComputedStyle(td);
      const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
      const border = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
      const content = td.getBoundingClientRect().height / z - pad - border;
      td.style.height = `${Math.max(content + add, content)}px`;
      td.style.verticalAlign = 'middle';
    });
  }

  /**
   * Fit the stacked front (hitters, then bench, then pen) above the 1.5" notes.
   * Tighten the whole card (dense → xdense → zoom) before clipping.
   * If the stack is still short, grow every pair so the longer column
   * reaches the notes instead of dying mid-page.
   */
  function fitFrontBody(front, body, bodyAvail) {
    if (!front || !body) return;
    const scale = body.querySelector('.sc-front-scale') || body;
    front.classList.remove('sc-dense', 'sc-xdense', 'sc-aux-dense', 'sc-aux-xdense', 'sc-fill', 'sc-measuring');
    clearScale(scale);
    clearWhoHeights(front);
    body.style.overflow = 'hidden';

    let h = sideNaturalHeight(front) || scale.scrollHeight || 1;
    if (h > bodyAvail + 1) {
      front.classList.add('sc-dense');
      h = sideNaturalHeight(front) || h;
    }
    if (h > bodyAvail + 1) {
      front.classList.add('sc-xdense');
      h = sideNaturalHeight(front) || h;
    }
    let z = 1;
    if (h > bodyAvail + 0.5) {
      z = Math.max(0.42, bodyAvail / Math.max(h, 1));
      applyZoom(scale, z);
      for (let i = 0; i < 6; i++) {
        const layoutH = scale.getBoundingClientRect().height;
        if (layoutH <= bodyAvail + 0.5) break;
        z = Math.max(0.42, z * (bodyAvail / Math.max(layoutH, 1)));
        applyZoom(scale, z);
      }
    }
    const zoomProp = parseFloat(scale.style.zoom || '');
    const scaleMatch = /scale\(([\d.]+)\)/.exec(scale.style.transform || '');
    const applied = (zoomProp && zoomProp < 0.995) ? zoomProp : (scaleMatch ? parseFloat(scaleMatch[1]) : 1);
    for (let pass = 0; pass < 3; pass++) {
      front.querySelectorAll('.sc-side').forEach((side) => {
        const room = bodyAvail - side.getBoundingClientRect().height - 2;
        if (room > 4) growSide(side, room, applied);
      });
    }
  }

  /** Fit strategy/small-ball print pages in the popup (also used by measure scripts). */
  function fitPrintPages(doc, tallIn, notesIn) {
    const pages = [...doc.querySelectorAll('.sc-print-page')];
    const avail = tallIn * 96;
    if (!pages.length) {
      const el = doc.getElementById('fit');
      if (!el) return;
      el.style.zoom = '';
      const h = el.scrollHeight || el.getBoundingClientRect().height;
      if (h > avail) el.style.zoom = String(Math.max(0.35, avail / h));
      return;
    }
    pages.forEach((p) => {
      p.style.zoom = '';
      // Front: densify + scale ONLY the lineup body so the 1.5" notes strip stays fixed at bottom.
      const body = p.querySelector('.sc-front-body');
      if (body && p.classList.contains('sc-print-front')) {
        const notesH = (notesIn + 0.08) * 96;
        const bodyAvail = Math.max(120, avail - notesH);
        fitFrontBody(p, body, bodyAvail);
        return;
      }
      // Back (TTP / catcher pop): measure unconstrained content, then scale to one page.
      const inner = p.querySelector('.sc-back') || p;
      const prevOverflow = p.style.overflow;
      const prevMax = p.style.maxHeight;
      const prevH = p.style.height;
      p.style.overflow = 'visible';
      p.style.maxHeight = 'none';
      p.style.height = 'auto';
      const h = inner.scrollHeight || inner.getBoundingClientRect().height;
      p.style.overflow = prevOverflow;
      p.style.maxHeight = prevMax;
      p.style.height = prevH;
      if (h > avail) {
        inner.style.minHeight = '0';
        applyZoom(inner, Math.max(0.35, avail / h));
      }
    });
  }

  function openPrint(title, inner, orientation, extraCss) {
    const w = window.open('', '_blank');
    if (!w) {
      alert(tr('Pop-up blocked — allow pop-ups to export PDF.', 'El navegador bloqueó la ventana. Permite pop-ups para exportar el PDF.'));
      return;
    }
    const portrait = orientation === 'portrait';
    const page = portrait ? 'letter portrait' : 'letter landscape';
    const wide = portrait ? '8.14in' : '10.7in';
    // Letter usable height after ~0.18in margins.
    const tallIn = portrait ? 10.5 : 7.55;
    const notesIn = 1.5;
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title>
      <style>
        @page { size: ${page}; margin: 0.18in; }
        ${CARD_CSS}
        ${extraCss || ''}
        html,body{margin:0;padding:0;background:#fff}
        #fit{width:${wide};transform-origin:top left}
        .sc-print-page{width:${wide};box-sizing:border-box;page-break-after:always;break-after:page;page-break-inside:avoid;break-inside:avoid}
        .sc-print-page:last-child{page-break-after:auto;break-after:auto}
        .sc-print-front.sc-front-page{
          height:${tallIn}in;max-height:${tallIn}in;min-height:${tallIn}in;overflow:hidden;
          display:flex;flex-direction:column;
        }
        .sc-print-back{
          height:${tallIn}in;max-height:${tallIn}in;min-height:${tallIn}in;overflow:hidden;
          page-break-before:always;break-before:page;
        }
        @media print{
          .sc-print-page{page-break-after:always;break-after:page;page-break-inside:avoid;break-inside:avoid}
          .sc-print-page:last-child{page-break-after:auto;break-after:auto}
          .sc-print-front.sc-front-page{height:${tallIn}in;max-height:${tallIn}in;overflow:hidden}
          .sc-print-back{height:${tallIn}in;max-height:${tallIn}in;overflow:hidden;page-break-before:always;break-before:page}
        }
      </style></head><body><div id="fit">${inner}</div>
      <script>
        (function(){
          var tallIn = ${tallIn};
          var notesIn = ${notesIn};
          function sideH(front){
            var max = 0;
            Array.prototype.forEach.call(front.querySelectorAll('.sc-side'), function(side){
              var r = side.getBoundingClientRect().height;
              max = Math.max(max, r || side.scrollHeight || 0);
            });
            return max;
          }
          function clearScale(el){
            if(!el) return;
            el.style.zoom = '1';
            el.style.transform = '';
            el.style.transformOrigin = 'top left';
            el.style.marginBottom = '';
            el.style.width = '';
          }
          function clearWhoHeights(root){
            if(!root) return;
            Array.prototype.forEach.call(root.querySelectorAll('td.sc-who'), function(td){
              td.style.height = '';
              td.style.verticalAlign = '';
            });
          }
          function addWhoHeights(table, extraPx){
            if(!table || extraPx <= 0) return;
            var whos = table.querySelectorAll('td.sc-who');
            if(!whos.length) return;
            var add = extraPx / whos.length;
            Array.prototype.forEach.call(whos, function(td){
              var cs = getComputedStyle(td);
              var pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
              var border = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
              var content = td.getBoundingClientRect().height - pad - border;
              td.style.height = Math.max(content + add, content) + 'px';
              td.style.verticalAlign = 'middle';
            });
          }
          function applyZoom(el, z){
            if(!el) return;
            z = Math.max(0.22, Math.min(1, z));
            clearScale(el);
            if(z >= 0.995) return;
            el.style.zoom = String(z);
            var layoutH = el.getBoundingClientRect().height;
            var natural = el.scrollHeight || layoutH;
            if(layoutH > natural * z + 6){
              el.style.zoom = '1';
              el.style.transform = 'scale(' + z + ')';
              el.style.width = (100 / z) + '%';
              el.style.marginBottom = ((z - 1) * natural) + 'px';
            }
          }
          function growSide(side, extraVisualPx, zoom){
            if(!side || extraVisualPx <= 2) return;
            var z = zoom > 0 ? zoom : 1;
            var whos = side.querySelectorAll('td.sc-who');
            if(!whos.length) return;
            var add = (extraVisualPx / whos.length) / z;
            Array.prototype.forEach.call(whos, function(td){
              var cs = getComputedStyle(td);
              var pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
              var border = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
              var content = td.getBoundingClientRect().height / z - pad - border;
              td.style.height = Math.max(content + add, content) + 'px';
              td.style.verticalAlign = 'middle';
            });
          }
          function fitFront(front, body, bodyAvail){
            if(!front || !body) return;
            var scale = body.querySelector('.sc-front-scale') || body;
            front.classList.remove('sc-dense', 'sc-xdense', 'sc-aux-dense', 'sc-aux-xdense', 'sc-fill', 'sc-measuring');
            clearScale(scale);
            clearWhoHeights(front);
            body.style.overflow = 'hidden';
            var h = sideH(front) || scale.scrollHeight || 1;
            if(h > bodyAvail + 1){
              front.classList.add('sc-dense');
              h = sideH(front) || h;
            }
            if(h > bodyAvail + 1){
              front.classList.add('sc-xdense');
              h = sideH(front) || h;
            }
            var z = 1;
            if(h > bodyAvail + 0.5){
              z = Math.max(0.42, bodyAvail / Math.max(h, 1));
              applyZoom(scale, z);
              for(var i = 0; i < 6; i++){
                var layoutH2 = scale.getBoundingClientRect().height;
                if(layoutH2 <= bodyAvail + 0.5) break;
                z = Math.max(0.42, z * (bodyAvail / Math.max(layoutH2, 1)));
                applyZoom(scale, z);
              }
            }
            var zoomProp = parseFloat(scale.style.zoom || '');
            var scaleMatch = /scale\(([\d.]+)\)/.exec(scale.style.transform || '');
            var applied = (zoomProp && zoomProp < 0.995) ? zoomProp : (scaleMatch ? parseFloat(scaleMatch[1]) : 1);
            for(var pass = 0; pass < 3; pass++){
              Array.prototype.forEach.call(front.querySelectorAll('.sc-side'), function(side){
                var room = bodyAvail - side.getBoundingClientRect().height - 2;
                if(room > 4) growSide(side, room, applied);
              });
            }
          }
          function fit(){
            var pages = Array.prototype.slice.call(document.querySelectorAll('.sc-print-page'));
            var avail = tallIn * 96;
            if(!pages.length){
              var el = document.getElementById('fit');
              if(!el) return;
              el.style.zoom = '';
              var h0 = el.scrollHeight || el.getBoundingClientRect().height;
              if(h0 > avail) el.style.zoom = String(Math.max(0.35, avail / h0));
              return;
            }
            pages.forEach(function(p){
              p.style.zoom = '';
              var body = p.querySelector('.sc-front-body');
              if(body && p.classList.contains('sc-print-front')){
                var notesH = (notesIn + 0.08) * 96;
                fitFront(p, body, Math.max(120, avail - notesH));
                return;
              }
              var inner = p.querySelector('.sc-back') || p;
              var prevOverflow = p.style.overflow, prevMax = p.style.maxHeight, prevH = p.style.height;
              p.style.overflow = 'visible';
              p.style.maxHeight = 'none';
              p.style.height = 'auto';
              var h = inner.scrollHeight || inner.getBoundingClientRect().height;
              p.style.overflow = prevOverflow;
              p.style.maxHeight = prevMax;
              p.style.height = prevH;
              if(h > avail){
                inner.style.minHeight = '0';
                applyZoom(inner, Math.max(0.35, avail / h));
              }
            });
          }
          window.__scFitPrintPages = fit;
          if(document.readyState === 'complete') fit();
          else window.addEventListener('load', fit);
        })();
      <\/script>
      </body></html>`);
    w.document.close();
    const fit = () => {
      try {
        if (typeof w.__scFitPrintPages === 'function') w.__scFitPrintPages();
        else fitPrintPages(w.document, tallIn, notesIn);
      } catch (e) {}
      setTimeout(() => { try { w.focus(); w.print(); } catch (e) {} }, 250);
    };
    setTimeout(fit, 80);
  }

  function strategyPitcherIds(abbrev, state) {
    const st = ensureTeam(state, abbrev);
    const ids = [];
    if (st.sp) ids.push(Number(st.sp));
    (st.pen || []).forEach((id) => {
      const n = Number(id);
      if (n && n !== Number(st.sp) && !ids.includes(n)) ids.push(n);
    });
    return ids;
  }

  /** Opposing staff for the back page: SP + pen, then remaining roster pitchers. */
  function opponentPitcherIds(abbrev, state) {
    const people = roster(abbrev).filter((p) => p.role === 'P');
    const st = ensureTeam(state, abbrev);
    const ordered = strategyPitcherIds(abbrev, state);
    const seen = new Set(ordered);
    people.slice().sort((a, b) => a.last.localeCompare(b.last)).forEach((p) => {
      if (!seen.has(p.id)) { ordered.push(p.id); seen.add(p.id); }
    });
    return ordered;
  }

  function opponentCatcherIds(abbrev, state) {
    const st = ensureTeam(state, abbrev);
    const ids = [];
    const push = (slot) => {
      if (!slot || !slot.id) return;
      const p = byId.get(Number(slot.id));
      if (!p) return;
      const pos = String(slot.pos || '');
      const def = String(p.roster_pos || '').toUpperCase();
      if (pos === '2' || def === 'C') ids.push(Number(p.id));
    };
    (st.lineup || []).forEach(push);
    (st.bench || []).forEach(push);
    roster(abbrev).filter((p) => p.role === 'H' && String(p.roster_pos || '').toUpperCase() === 'C')
      .forEach((p) => { if (!ids.includes(p.id)) ids.push(p.id); });
    return ids;
  }

  /** Back of strategy PDF: opponent time-to-plate + catcher pop from the TTH tab. */
  function buildStrategyBack(opponent, state) {
    const meta = teamMeta(opponent);
    const people = roster(opponent);
    const pitchers = people.filter((p) => p.role === 'P');
    const hitters = people.filter((p) => p.role === 'H');
    const pitIds = opponentPitcherIds(opponent, state);
    const catchIds = opponentCatcherIds(opponent, state);
    const spId = ensureTeam(state, opponent).sp;
    const penSet = new Set((ensureTeam(state, opponent).pen || []).map(Number));
    const pitRows = (pitIds.length ? pitIds : [null]).map((id) => {
      const p = id ? byId.get(id) : null;
      let role = 'P';
      if (id && spId && Number(id) === Number(spId)) role = 'SP';
      else if (id && penSet.has(Number(id))) role = 'RP';
      const name = p ? cardLast(p, pitchers) : '—';
      const ttp = p ? esc(ttpFor(p.id)) : '';
      return `<tr><td class="sc-ttp-role">${esc(role)}</td><td class="sc-ttp-name">${esc(name)}</td><td class="sc-ttp-val">${ttp}</td></tr>`;
    }).join('');
    const catchRows = (catchIds.length ? catchIds : [null]).map((id) => {
      const p = id ? byId.get(id) : null;
      const name = p ? cardLast(p, hitters) : '—';
      const pop = p ? esc(popFor(p.id)) : '';
      return `<tr><td class="sc-ttp-role">C</td><td class="sc-ttp-name">${esc(name)}</td><td class="sc-ttp-val">${pop}</td></tr>`;
    }).join('');
    const logo = meta.logo
      ? `<img class="sc-logo" src="${esc(meta.logo)}" alt="">`
      : `<span class="sc-mono">${esc(meta.abbrev)}</span>`;
    return `<div class="sc-back">
      <header class="sc-head" style="background:${esc(meta.color)}">
        <div class="sc-head-name">${esc(meta.name)} · ${tr('Running game', 'Juego en bases')}</div>
        ${logo}
      </header>
      <div class="sc-bar">${tr('TIME TO PLATE', 'TIEMPO AL HOME')} · ${esc(meta.abbrev)}</div>
      <table class="sc-ttp-table">
        <thead><tr><th></th><th>${tr('PITCHER', 'PITCHER')}</th><th>TTP</th></tr></thead>
        <tbody>${pitRows}</tbody>
      </table>
      <div class="sc-bar">${tr('CATCHER POP', 'POP DEL RECEPTOR')} · ${esc(meta.abbrev)}</div>
      <table class="sc-ttp-table">
        <thead><tr><th></th><th>${tr('CATCHER', 'RECEPTOR')}</th><th>POP</th></tr></thead>
        <tbody>${catchRows}</tbody>
      </table>
    </div>`;
  }

  const CardApp = {
    boot(payload, advance) {
      DATA = (payload && typeof payload === 'object')
        ? payload
        : { teams: [], players: [], gaps: {} };
      if (!Array.isArray(DATA.players)) DATA.players = [];
      if (!Array.isArray(DATA.teams)) DATA.teams = [];
      if (!DATA.gaps || typeof DATA.gaps !== 'object') DATA.gaps = {};
      byId = new Map((DATA.players || []).map((p) => [Number(p.id), p]).filter((row) => Number.isFinite(row[0])));
      indexAdvance(advance);
      booted = true;
    },
    renderStrategy,
    renderSmallBall,
    refresh() {
      if (!booted && !((DATA && DATA.players) || []).length) return;
      return refresh();
    },
    setOpponent(v) { withStrategy((s) => { s.opponent = v; }); },
    setHome(flag) { withStrategy((s) => { s.santurceHome = !!flag; }); },
    setHomeTeam(abbrev) { withStrategy((s) => { s.santurceHome = abbrev === 'SAN'; }); },
    toggleLineup(team, id, on) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const pid = Number(id);
        const slots = st.lineup;
        if (on) {
          if (slots.some((r) => r && r.id === pid)) return;
          const idx = slots.findIndex((r) => !r);
          if (idx < 0) return;
          const player = byId.get(pid);
          const pos = openStarterPos(slots, player ? defaultPos(player) : 'DH');
          if (!pos) return;
          slots[idx] = { id: pid, pos };
          st.bench = st.bench.filter((b) => b.id !== pid);
        } else {
          st.lineup = slots.map((r) => (r && r.id === pid ? null : r));
        }
      });
    },
    toggleBench(team, id, on) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const pid = Number(id);
        if (on) {
          const player = byId.get(pid);
          if (!player || player.role !== 'H') return;
          st.lineup = st.lineup.map((r) => (r && r.id === pid ? null : r));
          if (!st.bench.some((b) => b.id === pid)) {
            st.bench.push({ id: pid, pos: defaultPos(player) });
          }
        } else {
          st.bench = st.bench.filter((b) => b.id !== pid);
        }
      });
    },
    fillBench(team) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const inLine = new Set(st.lineup.filter(Boolean).map((r) => r.id));
        roster(team).filter((p) => p.role === 'H' && !isPitcherPlayer(p) && !inLine.has(p.id)).forEach((p) => {
          if (!st.bench.some((b) => b.id === p.id)) st.bench.push({ id: p.id, pos: defaultPos(p) });
        });
      });
    },
    setPos(team, listName, id, pos) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const list = listName === 'bench' ? st.bench : st.lineup;
        const row = list.find((r) => r && r.id === Number(id));
        if (!row) return;
        if (listName !== 'bench' && (pos === '1' || !STARTER_POS.includes(pos) || list.some((other) => other && other.id !== row.id && other.pos === pos))) return;
        row.pos = pos;
      });
    },
    setRowPlayer(team, listName, oldId, newId) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const list = listName === 'bench' ? st.bench : st.lineup;
        const other = listName === 'bench' ? st.lineup : st.bench;
        const pid = Number(newId);
        const previous = Number(oldId);
        if (!pid) {
          if (listName === 'bench') st.bench = list.filter((r) => r && r.id !== previous);
          else st.lineup = list.filter((r) => r && r.id !== previous);
          return;
        }
        if (list.some((r) => r && r.id === pid) || other.some((r) => r && r.id === pid)) return;
        const player = byId.get(pid);
        if (listName === 'bench' && (!player || player.role !== 'H' || isPitcherPlayer(player))) return;
        const row = list.find((r) => r && r.id === previous);
        const others = listName === 'bench' ? [] : st.lineup.filter((r) => r && (!row || r.id !== row.id));
        const pos = listName === 'bench'
          ? (player ? defaultPos(player) : 'DH')
          : openStarterPos(others, player ? defaultPos(player) : 'DH');
        if (listName !== 'bench' && !pos) return;
        if (row) {
          row.id = pid;
          row.pos = pos;
        } else if (listName === 'bench') st.bench.push({ id: pid, pos });
        else if (st.lineup.length < 9) st.lineup.push({ id: pid, pos });
      });
    },
    clearLineup(team) { withStrategy((s) => { ensureTeam(s, team).lineup = []; }); },
    clearBench(team) { withStrategy((s) => { ensureTeam(s, team).bench = []; }); },
    clearSp(team) { withStrategy((s) => { ensureTeam(s, team).sp = null; }); },
    setSp(team, id) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const pid = Number(id);
        if (!pid) { st.sp = null; return; }
        const player = byId.get(pid);
        if (!player || player.role !== 'P' || player.team !== team) return;
        st.sp = player.id;
        st.pen = st.pen.filter((x) => x !== st.sp);
      });
    },
    togglePen(team, id, on) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const pid = Number(id);
        if (pid === st.sp) return;
        if (on) { if (!st.pen.includes(pid)) st.pen.push(pid); }
        else st.pen = st.pen.filter((x) => x !== pid);
      });
    },
    checkAllPen(team, on) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        if (!on) { st.pen = []; return; }
        st.pen = roster(team).filter((p) => p.role === 'P' && p.id !== st.sp).map((p) => p.id);
      });
    },
    dragStart(e) {
      e.dataTransfer.setData('text/plain', e.currentTarget.dataset.id);
      e.dataTransfer.effectAllowed = 'move';
    },
    dragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; },
    dropOnSpot(e) {
      e.preventDefault();
      const id = Number(e.dataTransfer.getData('text/plain'));
      const team = e.currentTarget.dataset.team;
      const slot = Number(e.currentTarget.dataset.slot);
      if (!id || !(slot >= 0 && slot < 9)) return;
      const player = byId.get(id);
      if (!player || player.role !== 'H' || player.team !== team) return;
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const slots = st.lineup.slice(0, 9);
        while (slots.length < 9) slots.push(null);
        const from = slots.findIndex((r) => r && r.id === id);
        if (from === slot) return;
        const target = slots[slot];
        if (from >= 0) {
          slots[slot] = slots[from];
          slots[from] = target;
        } else {
          const pos = (target && target.pos) || openStarterPos(slots.map((r, i) => (i === slot ? null : r)), defaultPos(player));
          if (!pos) return;
          slots[slot] = { id, pos };
          st.bench = st.bench.filter((b) => b.id !== id);
        }
        st.lineup = slots;
      });
    },
    dropLineup(e) { CardApp.dropOnSpot(e); },
    setSbTeam(v) {
      const state = loadNotes();
      state.team = v;
      saveNotesState(state);
      queuePersist();
      renderSmallBall();
    },
    saveTeamNotes(team, text) {
      const state = loadNotes();
      state.notes[team] = text;
      saveNotesState(state);
      queuePersist();
    },
    saveJersey(playerId, value) {
      saveJerseyOverride(playerId, value);
      queuePersist();
    },
    exportStrategy() {
      const el = document.getElementById('strategy-sheet');
      if (!el) return;
      const state = loadStrategy();
      const opponent = state.opponent;
      const sheet = el.cloneNode(true);
      sheet.querySelectorAll('input.sc-jersey').forEach((inp) => {
        const span = document.createElement('span');
        span.className = 'sc-jersey-print';
        span.textContent = inp.value || '';
        inp.replaceWith(span);
      });
      const front = `<div class="sc-print-page sc-print-front sc-front-page">
        <div class="sc-front-body"><div class="sc-front-scale">${sheet.outerHTML}</div></div>
        <section class="sc-front-notes">
          <div class="sc-bar">${tr('NOTES / RUNNING GAME', 'NOTAS / JUEGO EN BASES')}</div>
          <div class="sc-back-lines" aria-hidden="true"></div>
        </section>
      </div>`;
      const back = `<div class="sc-print-page sc-print-back">${buildStrategyBack(opponent, state)}</div>`;
      openPrint('Strategy card', front + back, 'portrait', `
        /* Page 1 matches the 11-4 Ponce card: two teams side by side,
           each column stacked hitters → bench → pen, 1.5in notes at the bottom.
           Name column stays 34% with max-width:0 so rows can still grow. */
        .sc-print-front .sc-front-body{flex:1 1 auto;min-height:0;max-height:calc(10.5in - 1.5in);overflow:hidden}
        .sc-print-front .sc-front-scale{display:block;width:100%;transform-origin:top left}
        .sc-print-front .sc-front-notes{flex:0 0 1.5in;height:1.5in;min-height:1.5in;max-height:1.5in;margin-top:auto;position:relative;z-index:2;background:#fff}
        .sc-print-front .sc-pair{gap:12px;align-items:start;min-width:0}
        .sc-print-front .sc-side{overflow:hidden;min-width:0}
        .sc-print-front .sc-col-who{width:34%}
        .sc-print-front .sc-col-sb{width:11%}
        .sc-print-front .sc-col-vs{width:6%}
        .sc-print-front .sc-col-stat{width:9.8%}
        .sc-print-front .sc-grid{font-size:12px;line-height:1.32;table-layout:fixed;width:100%}
        .sc-print-front .sc-grid th{font-size:10px;padding:4px 2px}
        .sc-print-front .sc-grid td{padding:4px 3px}
        .sc-print-front .sc-head{min-height:48px;padding:8px 10px}
        .sc-print-front .sc-head-name{font-size:17px}
        .sc-print-front .sc-logo,.sc-print-front .sc-mono{width:40px;height:40px}
        .sc-print-front .sc-grid td.sc-who,.sc-print-front .sc-who{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:0;width:34%}
        .sc-print-front .sc-bar{font-size:13px;padding:5px 0;letter-spacing:.1em}
        .sc-print-front .sc-date{font-size:13px;padding:5px 0 6px}
        .sc-print-front .sc-subhead td{font-size:11px;padding:4px 4px}
        .sc-print-front.sc-dense .sc-grid{font-size:10px;line-height:1.18}
        .sc-print-front.sc-dense .sc-grid th{font-size:8px;padding:2px 1px}
        .sc-print-front.sc-dense .sc-grid td{padding:2px 2px}
        .sc-print-front.sc-dense .sc-head{min-height:36px;padding:5px 8px}
        .sc-print-front.sc-dense .sc-head-name{font-size:14px}
        .sc-print-front.sc-dense .sc-logo,.sc-print-front.sc-dense .sc-mono{width:32px;height:32px}
        .sc-print-front.sc-dense .sc-who{font-size:10px}
        .sc-print-front.sc-dense .sc-bar{font-size:11px;padding:3px 0}
        .sc-print-front.sc-dense .sc-date{font-size:11px;padding:3px 0 4px}
        .sc-print-front.sc-dense .sc-subhead td{font-size:9px;padding:2px 3px}
        .sc-print-front.sc-dense .sc-pair{gap:8px}
        .sc-print-front.sc-xdense .sc-grid{font-size:8px;line-height:1.08}
        .sc-print-front.sc-xdense .sc-grid th{font-size:7px;padding:1px}
        .sc-print-front.sc-xdense .sc-grid td{padding:1px 1px}
        .sc-print-front.sc-xdense .sc-head{min-height:28px;padding:3px 6px}
        .sc-print-front.sc-xdense .sc-head-name{font-size:12px}
        .sc-print-front.sc-xdense .sc-logo,.sc-print-front.sc-xdense .sc-mono{width:26px;height:26px}
        .sc-print-front.sc-xdense .sc-who{font-size:8px}
        .sc-print-front.sc-xdense .sc-bar{font-size:9px;padding:2px 0;letter-spacing:.06em}
        .sc-print-front.sc-xdense .sc-date{font-size:9px;padding:2px 0}
        .sc-print-front.sc-xdense .sc-subhead td{font-size:8px;padding:1px 2px}
        .sc-print-front.sc-xdense .sc-pair{gap:4px}
        /* Page 2: opponent TTP + catcher pop only. */
        .sc-print-back .sc-back{min-height:10.0in;height:100%;padding-top:4px;box-sizing:border-box}
        .sc-print-back .sc-head{min-height:52px;padding:10px 12px}
        .sc-print-back .sc-head-name{font-size:18px}
        .sc-print-back .sc-bar{font-size:14px;padding:6px 0}
        .sc-print-back .sc-ttp-table{font-size:15px}
        .sc-print-back .sc-ttp-val{font-size:18px}
      `);
    },
    exportSmallBall() {
      const el = document.getElementById('smallball-sheet');
      if (!el) return;
      const clone = el.cloneNode(true);
      clone.querySelectorAll('textarea').forEach((ta) => {
        const div = document.createElement('div');
        div.textContent = ta.value;
        div.style.whiteSpace = 'pre-wrap';
        div.style.minHeight = '36px';
        ta.replaceWith(div);
      });
      openPrint('Small ball', clone.outerHTML, 'landscape');
    },
    /** Lineup slot order (1–9) for a team from the strategy card. Empty slots omitted. */
    getLineupIds(abbrev) {
      const state = loadStrategy();
      const st = ensureTeam(state, abbrev);
      return (st.lineup || []).filter(Boolean).map((r) => Number(r.id)).filter((id) => id > 0);
    },
    /** Bench hitter ids selected on the strategy card for that day. */
    getBenchIds(abbrev) {
      const state = loadStrategy();
      const st = ensureTeam(state, abbrev);
      return (st.bench || []).filter(Boolean).map((r) => Number(r.id)).filter((id) => id > 0);
    },
    /**
     * Game-day hitters for PDF export: batting order 1–9, then checked bench.
     * Omits other roster hitters not selected for that day.
     */
    getActiveHitterIds(abbrev) {
      const lineup = this.getLineupIds(abbrev);
      const seen = new Set(lineup);
      const out = lineup.slice();
      this.getBenchIds(abbrev).forEach((id) => {
        if (!seen.has(id)) {
          seen.add(id);
          out.push(id);
        }
      });
      return out;
    },
    /** All team abbrevs that have card roster players. */
    teamAbbrevs() {
      return ((DATA && DATA.teams) || []).map((t) => t.abbrev).filter(Boolean);
    },
  };
  window.CardApp = CardApp;
})();

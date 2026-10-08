/* Strategy card + small-ball card.
   Lineup / pen state: localStorage santurce_strategy_lineup_v1
   Team notes: localStorage santurce_smallball_notes_v1
   Shape: { opponent, santurceHome, teams: { ABBR: { sp, pen: [id], lineup: [{id, pos}] } } }
   Notes: { team, notes: { ABBR: "..." } }
*/
(function () {
  const STRATEGY_KEY = 'santurce_strategy_lineup_v1';
  const NOTES_KEY = 'santurce_smallball_notes_v1';
  const POS = [
    ['1', 'P'], ['2', 'C'], ['3', '1B'], ['4', '2B'], ['5', '3B'],
    ['6', 'SS'], ['7', 'LF'], ['8', 'CF'], ['9', 'RF'], ['DH', 'DH'],
  ];
  const POS_LABEL = Object.fromEntries(POS);
  const DEFAULT_POS = { C: '2', '1B': '3', '2B': '4', '3B': '5', SS: '6', LF: '7', CF: '8', RF: '9', OF: '8', IF: '4', DH: 'DH', P: '1' };

  let DATA = null;
  let byId = new Map();

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function es() { return (typeof getLang === 'function') && getLang() === 'es'; }
  function tr(en, sp) { return es() ? sp : en; }

  function loadStrategy() {
    let raw = {};
    try { raw = JSON.parse(localStorage.getItem(STRATEGY_KEY) || '{}') || {}; } catch (e) { raw = {}; }
    if (!raw.teams || typeof raw.teams !== 'object') raw.teams = {};
    if (!raw.opponent) raw.opponent = 'CAG';
    if (typeof raw.santurceHome !== 'boolean') raw.santurceHome = true;
    return raw;
  }
  function saveStrategy(state) {
    try { localStorage.setItem(STRATEGY_KEY, JSON.stringify(state)); } catch (e) {}
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

  function teamMeta(abbrev) {
    return (DATA.teams || []).find((t) => t.abbrev === abbrev) || { abbrev, name: abbrev, color: '#333', logo: null };
  }
  function roster(abbrev) {
    return (DATA.players || []).filter((p) => p.team === abbrev);
  }
  function ensureTeam(state, abbrev) {
    if (!state.teams[abbrev]) state.teams[abbrev] = { sp: null, pen: [], lineup: [] };
    const st = state.teams[abbrev];
    const ids = new Set(roster(abbrev).map((p) => p.id));
    st.lineup = (st.lineup || []).filter((r) => r && ids.has(Number(r.id))).map((r) => ({ id: Number(r.id), pos: String(r.pos || 'DH') }));
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
    if (val == null || sample == null || sample < 15) return '';
    const v = Number(val);
    if (metric === 'k' || metric === 'wh') {
      const g = 18, r = metric === 'k' ? 27 : 32;
      if (v <= g) return 'sc-good';
      if (v >= r) return 'sc-bad';
      return '';
    }
    if (metric === 'gb') {
      if (v >= 48) return 'sc-good';
      if (v <= 33) return 'sc-bad';
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

  function ttpFor(id) {
    try {
      const ov = (typeof TTH_OVERRIDES !== 'undefined' && TTH_OVERRIDES) || JSON.parse(localStorage.getItem('santurce_tth_pop_overrides_v1') || '{}');
      const row = ov[String(id)];
      if (row && row.time_to_home != null && String(row.time_to_home).trim() !== '') return String(row.time_to_home).trim();
    } catch (e) {}
    return '';
  }

  function sbText(h) {
    if (!h || h.sb == null) return '';
    const bits = [`${h.sb}/${(h.sb || 0) + (h.cs || 0)}`];
    if (h.summer_sb || h.summer_cs) bits.push(`Su ${h.summer_sb || 0}/${(h.summer_sb || 0) + (h.summer_cs || 0)}`);
    return bits.join(' ');
  }

  function sideLine(h, side) {
    const split = side === 'L' ? h.vs_l : h.vs_r;
    return {
      k: h.k, gb: h.gb,
      wh: split && split.wh != null ? split.wh : h.wh,
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
      const line = sideLine(h, side);
      const sample = line.sample || 0;
      const seasonTitle = tr('LBPRC 2025 season line (no platoon split)', 'Línea de temporada LBPRC 2025 (sin split de platoon)');
      const splitTitle = tr(`Synergy vs ${side}HP · ${line.sample} PA`, `Synergy vs ${side}HP · ${line.sample} PA`);
      const cells = [
        statCell(fmtPct(line.k), toneHitter('k', line.k, h.pa), seasonTitle),
        statCell(fmtPct(line.gb), toneHitter('gb', line.gb, h.pa), seasonTitle),
        statCell(fmtPct(line.wh), toneHitter('wh', line.wh, line.split ? sample : h.pa), line.split ? splitTitle : seasonTitle),
        statCell(fmtAvg(line.avg), toneHitter('avg', line.avg, line.split ? sample : h.pa), line.split ? splitTitle : seasonTitle),
        statCell(fmtAvg(line.ops), toneHitter('ops', line.ops, line.split ? sample : h.pa), line.split ? splitTitle : seasonTitle),
      ].join('');
      if (i === 0) return `<tr>${whoHtml}<td class="sc-sb" rowspan="2">${sbHtml}</td><td class="sc-vs">${side}</td>${cells}</tr>`;
      return `<tr><td class="sc-vs">${side}</td>${cells}</tr>`;
    });
    return rows.join('');
  }

  function pitcherStatRows(player, whoHtml) {
    const p = player && player.pitcher;
    const ttp = player ? esc(ttpFor(player.id)) : '';
    const ttpCell = `<td class="sc-ttp" rowspan="2">${ttp}</td>`;
    if (!p) {
      return `<tr>${whoHtml}${ttpCell}<td>L</td><td></td><td></td><td></td><td></td><td></td></tr>
        <tr><td>R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
    }
    const title = tr('LBPRC 2025 season line, repeated on L and R (no platoon split)', 'Línea LBPRC 2025, repetida en L y R (sin split de platoon)');
    const sample = p.bf || 0;
    const cells = [
      statCell(fmtPct(p.k), tonePitcher('k', p.k, sample), title),
      statCell(fmtPct(p.gb), tonePitcher('gb', p.gb, sample), title),
      statCell(fmtPct(p.wh), tonePitcher('wh', p.wh, sample), p.wh == null ? tr('No whiff in the arsenal cache', 'Sin whiff en el caché de arsenal') : title),
      statCell(fmtPct(p.bb), tonePitcher('bb', p.bb, sample), title),
      statCell(fmtAvg(p.ops), tonePitcher('ops', p.ops, sample), title),
    ].join('');
    return `<tr>${whoHtml}${ttpCell}<td class="sc-vs">L</td>${cells}</tr><tr><td class="sc-vs">R</td>${cells}</tr>`;
  }

  function whoCell(order, pos, last, bats, rowspan) {
    const bits = [];
    if (order) bits.push(`<span class="sc-ord">${esc(order)}</span>`);
    if (pos) bits.push(`<span class="sc-pos">${esc(pos)}</span>`);
    bits.push(`<span class="sc-last ${bats || ''}">${esc(last || '')}</span>`);
    return `<td class="sc-who" rowspan="${rowspan || 2}">${bits.join(' ')}</td>`;
  }

  function emptyHitterPair(order) {
    const who = whoCell(order, '', '', '');
    return `<tr>${who}<td rowspan="2"></td><td class="sc-vs">L</td><td></td><td></td><td></td><td></td><td></td></tr>
      <tr><td class="sc-vs">R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
  }

  function hitterTable(players, rowsHtml) {
    return `<table class="sc-grid">
      <thead><tr>
        <th>${tr('PLAYER', 'JUGADOR')}</th><th>SB/SBA</th><th>VS</th><th>K</th><th>GB</th><th>WH</th><th>AVG</th><th>OPS</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  }
  function pitcherTable(rowsHtml) {
    return `<table class="sc-grid">
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
    const inOrder = st.lineup.map((r) => byId.get(r.id)).filter(Boolean);
    const lineupIds = new Set(st.lineup.map((r) => r.id));
    const slots = st.lineup.slice();
    while (slots.length < 9) slots.push(null);
    let hitterRows = slots.map((slot, i) => {
      if (!slot) return emptyHitterPair(String(i + 1));
      const player = byId.get(slot.id);
      if (!player) return emptyHitterPair(String(i + 1));
      const who = whoCell(String(i + 1), posName(slot.pos), cardLast(player, hitters), batsClass(player));
      return hitterStatRows(player, who, esc(sbText(player.hitter)));
    }).join('');
    const sp = st.sp ? byId.get(st.sp) : null;
    if (sp) {
      const who = whoCell('', 'P', cardLast(sp, pitchers), batsClass(sp));
      hitterRows += hitterStatRows(null, who, '');
    } else {
      const who = whoCell('', 'P', '—', '');
      hitterRows += hitterStatRows(null, who, '');
    }
    const bench = hitters.filter((p) => !lineupIds.has(p.id)).sort((a, b) => a.last.localeCompare(b.last));
    const benchRows = bench.map((p) => {
      const who = whoCell('B', '', cardLast(p, hitters), batsClass(p));
      return hitterStatRows(p, who, esc(sbText(p.hitter)));
    }).join('') || emptyHitterPair('B');

    const penIds = st.pen.filter((id) => id !== st.sp);
    const pen = penIds.map((id) => byId.get(id)).filter(Boolean).sort((a, b) => a.last.localeCompare(b.last));
    let pitRows = '';
    if (sp) {
      const who = whoCell('SP', '', cardLast(sp, pitchers), batsClass(sp));
      pitRows += pitcherStatRows(sp, who);
    } else {
      pitRows += `<tr>${whoCell('SP', '', '—', '')}<td rowspan="2"></td><td class="sc-vs">L</td><td></td><td></td><td></td><td></td><td></td></tr><tr><td class="sc-vs">R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
    }
    pitRows += `<tr class="sc-subhead"><td colspan="8">BULLPEN</td></tr>`;
    if (!pen.length) {
      pitRows += `<tr>${whoCell('RP', '', '', '')}<td rowspan="2"></td><td class="sc-vs">L</td><td></td><td></td><td></td><td></td><td></td></tr><tr><td class="sc-vs">R</td><td></td><td></td><td></td><td></td><td></td></tr>`;
    } else {
      pen.forEach((p) => {
        const who = whoCell('RP', '', cardLast(p, pitchers), batsClass(p));
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
      ${hitterTable(hitters, hitterRows)}
      <div class="sc-bar">BENCH</div>
      ${hitterTable(hitters, benchRows)}
      <div class="sc-bar">PITCHERS</div>
      ${pitcherTable(pitRows)}
    </section>`;
  }

  function posOptions(selected) {
    return POS.map(([code, name]) => `<option value="${code}"${code === selected ? ' selected' : ''}>${code === 'DH' ? 'DH' : code + ' ' + name}</option>`).join('');
  }

  function editorSide(abbrev, state) {
    const st = ensureTeam(state, abbrev);
    const people = roster(abbrev);
    const hitters = people.filter((p) => p.role === 'H');
    const pitchers = people.filter((p) => p.role === 'P').slice().sort((a, b) => a.last.localeCompare(b.last));
    const lineupIds = new Set(st.lineup.map((r) => r.id));
    const order = st.lineup.map((r) => {
      const p = byId.get(r.id);
      if (!p) return '';
      return `<li draggable="true" data-id="${r.id}" data-team="${esc(abbrev)}"
        ondragstart="CardApp.dragStart(event)" ondragover="CardApp.dragOver(event)" ondrop="CardApp.dropLineup(event)">
        <input type="checkbox" checked onchange="CardApp.toggleLineup('${esc(abbrev)}', ${r.id}, this.checked)" title="${tr('In today\'s lineup', 'En la alineación de hoy')}">
        <span class="sc-grip" title="${tr('Drag to reorder', 'Arrastra para ordenar')}">↕</span>
        <select onmousedown="event.stopPropagation()" onchange="CardApp.setPos('${esc(abbrev)}', ${r.id}, this.value)">${posOptions(r.pos)}</select>
        <span class="sc-edit-name">${esc(p.name)}</span>
      </li>`;
    }).join('');
    const pool = hitters.filter((p) => !lineupIds.has(p.id)).sort((a, b) => a.last.localeCompare(b.last)).map((p) =>
      `<label class="sc-pool-item"><input type="checkbox" onchange="CardApp.toggleLineup('${esc(abbrev)}', ${p.id}, this.checked)"> ${esc(p.name)}</label>`
    ).join('');
    const arms = pitchers.map((p) => {
      const sp = st.sp === p.id;
      const pen = st.pen.includes(p.id);
      return `<label class="sc-arm">
        <input type="radio" name="sp-${esc(abbrev)}" ${sp ? 'checked' : ''} onchange="CardApp.setSp('${esc(abbrev)}', ${p.id})" title="SP">
        <input type="checkbox" ${pen ? 'checked' : ''} ${sp ? 'disabled' : ''} onchange="CardApp.togglePen('${esc(abbrev)}', ${p.id}, this.checked)" title="${tr('Bullpen', 'Bullpen')}">
        <span>${esc(p.name)}</span>
      </label>`;
    }).join('');
    return `<div class="sc-editor">
      <div class="sc-editor-h">${esc(teamMeta(abbrev).abbrev)} · ${tr('Today\'s card', 'Carta de hoy')}</div>
      <div class="sc-editor-label">${tr('Lineup — check, then drag', 'Alineación — marca y arrastra')}</div>
      <ul class="sc-order">${order || `<li class="sc-empty">${tr('No hitters in the lineup yet.', 'Aún no hay bateadores en la alineación.')}</li>`}</ul>
      <div class="sc-editor-label">${tr('Rest of the hitters → bench', 'Resto de bateadores → banca')}</div>
      <div class="sc-pool">${pool}</div>
      <div class="sc-editor-actions">
        <button type="button" onclick="CardApp.clearLineup('${esc(abbrev)}')">${tr('Clear lineup', 'Vaciar alineación')}</button>
        <button type="button" onclick="CardApp.clearSp('${esc(abbrev)}')">${tr('Clear SP', 'Quitar abridor')}</button>
      </div>
      <div class="sc-editor-label">${tr('Pitchers — radio = SP, check = pen', 'Pitchers — radio = abridor, check = bullpen')}</div>
      <div class="sc-editor-actions">
        <button type="button" onclick="CardApp.checkAllPen('${esc(abbrev)}', true)">${tr('All to pen', 'Todos al bullpen')}</button>
        <button type="button" onclick="CardApp.checkAllPen('${esc(abbrev)}', false)">${tr('Clear pen', 'Vaciar bullpen')}</button>
      </div>
      <div class="sc-arms">${arms}</div>
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
    const opponents = (DATA.teams || []).filter((t) => t.abbrev !== 'SAN');
    if (!opponents.some((t) => t.abbrev === state.opponent)) state.opponent = opponents[0] ? opponents[0].abbrev : 'CAG';
    const left = state.santurceHome ? 'SAN' : state.opponent;
    const right = state.santurceHome ? state.opponent : 'SAN';
    const oppOpts = opponents.map((t) => `<option value="${esc(t.abbrev)}"${t.abbrev === state.opponent ? ' selected' : ''}>${esc(t.name)}</option>`).join('');
    const gaps = DATA.gaps || {};
    root.innerHTML = `
      <div class="sc-toolbar">
        <div class="sc-home-toggle" role="group" aria-label="Santurce home or away">
          <span>${tr('Santurce', 'Santurce')}</span>
          <button type="button" class="${state.santurceHome ? 'on' : ''}" onclick="CardApp.setHome(true)">${tr('Home', 'Home')}</button>
          <button type="button" class="${state.santurceHome ? '' : 'on'}" onclick="CardApp.setHome(false)">${tr('Away', 'Visita')}</button>
        </div>
        <p class="sc-legend">${tr(
          'Green = a strength, red = a soft spot. K/GB repeat the season line on both hands. WH/AVG/OPS use a Synergy platoon split when that side has 15+ PA. TTP is the time-to-home saved on the pop-times tab.',
          'Verde = fortaleza, rojo = punto débil. K/GB repiten la línea de temporada en ambas manos. WH/AVG/OPS usan el split de Synergy cuando ese lado tiene 15+ PA. TTP es el tiempo al home guardado en la pestaña de pop times.'
        )}</p>
        <div class="sc-toolbar-right">
          <label>${tr('Opponent', 'Rival')}
            <select onchange="CardApp.setOpponent(this.value)">${oppOpts}</select>
          </label>
          <button type="button" class="sc-export" onclick="CardApp.exportStrategy()">${tr('Export PDF', 'Exportar PDF')}</button>
        </div>
      </div>
      <p class="note sc-gap">${esc(gaps.pitcher_platoon || '')} ${esc(gaps.hitter_platoon_k_gb || '')}</p>
      <div class="sc-editors">
        ${editorSide(left, state)}
        ${editorSide(right, state)}
      </div>
      <div class="sc-pair" id="strategy-sheet">
        ${sheetSide(left, state)}
        ${sheetSide(right, state)}
      </div>`;
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
    if (!h) return 0;
    if (h.sb_split) return Math.max(h.sb2 || 0, h.sb3 || 0);
    return Math.max(h.sb || 0, h.summer_sb || 0);
  }
  function groupScore(h) {
    if (!h) return 0;
    return Math.max(stealScore(h), sacTotal(h) || 0, h.bunts || 0);
  }
  function skewed(a, b) {
    return a >= 3 && a >= 2 * Math.max(b, 0) && (a - b) >= 2;
  }

  function runNotes(h) {
    if (!h) return { text: '', major: false };
    const bits = [];
    let major = false;
    if (h.sb_split && skewed(h.sb2 || 0, h.sb3 || 0)) {
      major = true;
      bits.push(tr(
        `Steals second much more than third (${h.sb2} of 2B vs ${h.sb3} of 3B).`,
        `Roba segunda mucho más que tercera (${h.sb2} de 2B vs ${h.sb3} de 3B).`
      ));
    } else if (h.sb_split && skewed(h.sb3 || 0, h.sb2 || 0)) {
      major = true;
      bits.push(tr(
        `Steals third much more than second (${h.sb3} of 3B vs ${h.sb2} of 2B).`,
        `Roba tercera mucho más que segunda (${h.sb3} de 3B vs ${h.sb2} de 2B).`
      ));
    }
    if (h.sb != null) {
      const att = (h.sb || 0) + (h.cs || 0);
      const pct = att ? Math.round(100 * h.sb / att) : null;
      bits.push(tr(
        `LBPRC SB ${h.sb}/${att}${pct == null ? '' : ' (' + pct + '%)'}.`,
        `LBPRC SB ${h.sb}/${att}${pct == null ? '' : ' (' + pct + '%)'}.`
      ));
    }
    if (h.summer_sb || h.summer_cs) {
      const att = (h.summer_sb || 0) + (h.summer_cs || 0);
      const src = (h.summer_sources || []).join('+') || '2026';
      bits.push(tr(
        `2026 summer ${h.summer_sb || 0}/${att} (${src}).`,
        `Verano 2026 ${h.summer_sb || 0}/${att} (${src}).`
      ));
    }
    if (!h.sb_split && ((h.sb || 0) + (h.summer_sb || 0) > 0)) {
      bits.push(tr(
        '2B vs 3B split is not in the LBPRC or summer totals.',
        'El split 2B vs 3B no está en los totales de LBPRC ni del verano.'
      ));
    }
    return { text: bits.join(' '), major };
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
    if (h.sac != null || h.summer_sac) {
      const parts = [];
      if (h.sac != null) parts.push(`LBPRC ${h.sac}`);
      if (h.summer_sac) parts.push(tr(`summer ${h.summer_sac}`, `verano ${h.summer_sac}`));
      bits.push(tr(`Sac bunts: ${parts.join(' · ')}.`, `Toques de sacrificio: ${parts.join(' · ')}.`));
    }
    if (h.in_synergy && h.bunts != null && !major) {
      bits.push(tr(`Synergy bunt attempts: ${h.bunts}.`, `Intentos de toque Synergy: ${h.bunts}.`));
    }
    return { text: bits.join(' '), major };
  }

  function aggLabel(h) {
    if (!h || h.sb == null && !h.summer_sb) return '';
    const sb = h.sb || 0;
    const cs = h.cs || 0;
    const att = sb + cs;
    const pct = att ? sb / att : 0;
    if (att >= 8 && pct >= 0.7) return 'AGG';
    if (att >= 2 || (h.summer_sb || 0) >= 2) return tr('Will pick his spot', 'Elige el momento');
    return tr('Not', 'No');
  }

  function countCell(n, extra, title) {
    const b = band(n);
    if (b == null) return `<td title="${esc(title || '')}">—</td>`;
    const extraHtml = extra ? `<small class="sb-extra">${extra}</small>` : '';
    return `<td class="sb-${b}" title="${esc(title || bandWord(b))}"><b>${esc(String(n))}</b><small>${esc(bandWord(b))}</small>${extraHtml}</td>`;
  }

  function sbCells(h) {
    if (h && h.sb_split) {
      const t2 = tr('Stolen bases of second (from 1B)', 'Bases robadas de segunda (desde 1B)');
      const t3 = tr('Stolen bases of third (from 2B)', 'Bases robadas de tercera (desde 2B)');
      return countCell(h.sb2 || 0, '', t2) + countCell(h.sb3 || 0, '', t3);
    }
    if (!h || (h.sb == null && !h.summer_sb && !h.summer_cs)) {
      return '<td colspan="2">—</td>';
    }
    const lbN = h.sb || 0;
    const suN = h.summer_sb || 0;
    const lbTxt = h.sb != null ? `${h.sb}/${lbN + (h.cs || 0)}` : '';
    const suTxt = (h.summer_sb || h.summer_cs) ? `Su ${h.summer_sb || 0}/${(h.summer_sb || 0) + (h.summer_cs || 0)}` : '';
    const b = band(stealScore(h));
    const cls = b ? `sb-${b}` : '';
    const title = tr(
      'Total stolen bases. The cache has no 2B vs 3B split, so this count spans both columns. Summer is listed when 2026 MLB/MiLB/LMB totals exist.',
      'Total de bases robadas. El caché no trae split 2B vs 3B, así que este conteo cubre ambas columnas. El verano aparece cuando hay totales 2026 de MLB/MiLB/LMB.'
    );
    const lines = [];
    if (suTxt && suN > lbN) {
      lines.push(`<b>${esc(suTxt)}</b>`);
      if (lbTxt) lines.push(`<small>${esc(lbTxt)} LBPRC</small>`);
    } else {
      lines.push(`<b>${esc(lbTxt || suTxt || '—')}</b>`);
      if (suTxt && lbTxt) lines.push(`<small>${esc(suTxt)}</small>`);
    }
    if (b) lines.push(`<small>${esc(bandWord(b))}</small>`);
    return `<td class="${cls}" colspan="2" title="${esc(title)}">${lines.join('')}</td>`;
  }

  function sacCell(h) {
    const n = sacTotal(h);
    const extra = h && h.sac != null && h.summer_sac ? `Su ${h.summer_sac}` : '';
    const title = tr('Sacrifice bunts (LBPRC + 2026 summer when present)', 'Toques de sacrificio (LBPRC + verano 2026 si hay)');
    return countCell(n, extra, title);
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
    const teams = DATA.teams || [];
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
          '2B = steals of second, 3B = steals of third. SAC = sacrifice bunts. Frequent >5 green, occasional 2–5 white, rare <2 red. A note turns red when one base, or one bunt situation, is much more common than the other.',
          '2B = robos de segunda, 3B = robos de tercera. SAC = toques de sacrificio. Frecuente >5 verde, ocasional 2–5 blanco, raro <2 rojo. La nota se pone roja cuando una base, o una situación de toque, es mucho más común que la otra.'
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
              <th>Player</th><th>SPD</th><th>2B</th><th>3B</th><th>AGG</th><th>NOTES</th><th>SAC</th><th>HIT</th><th>NOTES</th>
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

  function withStrategy(fn) {
    const state = loadStrategy();
    fn(state);
    saveStrategy(state);
    renderStrategy();
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
    .sc-grid{width:100%;border-collapse:collapse;font-size:9px;line-height:1.15}
    .sc-grid th{background:#e6e8ec;color:#222;font-weight:700;font-size:8px;letter-spacing:.04em;padding:2px 3px;border:1px solid #c5c9d0;text-align:center}
    .sc-grid td{border:1px solid #e1e4ea;padding:1px 3px;text-align:center;background:#fff;white-space:nowrap}
    .sc-who{text-align:left !important;white-space:nowrap;font-weight:700;color:#111}
    .sc-ord,.sc-pos{display:inline-block;min-width:1.1em;margin-right:3px;font-weight:800}
    .sc-last{font-weight:800}
    .sc-lhb{color:#c0392b}
    .sc-shb{color:#1a5276}
    .sc-vs{font-weight:700;color:#333}
    .sc-good{background:#2e9e4f !important;color:#fff !important;font-weight:800}
    .sc-bad{background:#e04b4b !important;color:#fff !important;font-weight:800}
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
    @media print { .sc-good,.sc-bad,.sb-freq,.sb-rare,.sb-major,.sb-band th,.sb-cols th,.sc-head,.sb-teamhead,.sb-title,.sb-teamnotes th{-webkit-print-color-adjust:exact;print-color-adjust:exact} }
  `;

  function openPrint(title, inner, orientation) {
    const w = window.open('', '_blank');
    if (!w) {
      alert(tr('Pop-up blocked — allow pop-ups to export PDF.', 'El navegador bloqueó la ventana. Permite pop-ups para exportar el PDF.'));
      return;
    }
    const portrait = orientation === 'portrait';
    const page = portrait ? 'letter portrait' : 'letter landscape';
    const wide = portrait ? '8.0in' : '10.5in';
    const tallIn = portrait ? 10.15 : 7.7;
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title>
      <style>
        @page { size: ${page}; margin: 0.28in; }
        ${CARD_CSS}
        #fit{width:${wide};transform-origin:top left}
      </style></head><body><div id="fit">${inner}</div></body></html>`);
    w.document.close();
    const fit = () => {
      const el = w.document.getElementById('fit');
      if (!el) return;
      const avail = tallIn * 96;
      const h = el.scrollHeight || el.getBoundingClientRect().height;
      if (h > avail) {
        const z = Math.max(0.42, avail / h);
        el.style.zoom = String(z);
      }
      setTimeout(() => { try { w.focus(); w.print(); } catch (e) {} }, 250);
    };
    setTimeout(fit, 60);
  }

  const CardApp = {
    boot(payload) {
      DATA = payload;
      byId = new Map(((payload && payload.players) || []).map((p) => [p.id, p]));
    },
    renderStrategy,
    renderSmallBall,
    refresh,
    setOpponent(v) { withStrategy((s) => { s.opponent = v; }); },
    setHome(flag) { withStrategy((s) => { s.santurceHome = !!flag; }); },
    toggleLineup(team, id, on) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const pid = Number(id);
        if (on) {
          if (!st.lineup.some((r) => r.id === pid)) {
            const player = byId.get(pid);
            st.lineup.push({ id: pid, pos: player ? defaultPos(player) : 'DH' });
          }
        } else {
          st.lineup = st.lineup.filter((r) => r.id !== pid);
        }
      });
    },
    setPos(team, id, pos) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const row = st.lineup.find((r) => r.id === Number(id));
        if (row) row.pos = pos;
      });
    },
    clearLineup(team) { withStrategy((s) => { ensureTeam(s, team).lineup = []; }); },
    clearSp(team) { withStrategy((s) => { ensureTeam(s, team).sp = null; }); },
    setSp(team, id) {
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        st.sp = Number(id);
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
    dropLineup(e) {
      e.preventDefault();
      const from = Number(e.dataTransfer.getData('text/plain'));
      const to = Number(e.currentTarget.dataset.id);
      const team = e.currentTarget.dataset.team;
      if (!from || !to || from === to) return;
      withStrategy((s) => {
        const st = ensureTeam(s, team);
        const a = st.lineup.findIndex((r) => r.id === from);
        const b = st.lineup.findIndex((r) => r.id === to);
        if (a < 0 || b < 0) return;
        const [row] = st.lineup.splice(a, 1);
        st.lineup.splice(b, 0, row);
      });
    },
    setSbTeam(v) {
      const state = loadNotes();
      state.team = v;
      saveNotesState(state);
      renderSmallBall();
    },
    saveTeamNotes(team, text) {
      const state = loadNotes();
      state.notes[team] = text;
      saveNotesState(state);
    },
    exportStrategy() {
      const el = document.getElementById('strategy-sheet');
      if (!el) return;
      openPrint('Strategy card', el.outerHTML, 'portrait');
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
  };
  window.CardApp = CardApp;
})();

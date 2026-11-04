/* INF + OF defensive positioning — Excel card formats.
   OF codes: STRAIGHT | G/L/O/P + 10/20/30 (gap/line/oppo/pull)
   INF codes: H N L R EL ER (+ optional -3/-6/-9) · BAG for 1B DP
   Storage: localStorage santurce_positioning_v2 (migrates OF v1 cells)
   Seed: data/positioning_v2.json (spray-derived coach-review defaults)
*/
(function () {
  const STORE_KEY = 'santurce_positioning_v2';
  const LEGACY_OF_KEY = 'santurce_of_positioning_v1';
  /** Bump when data/positioning_v2.json is regenerated so devices re-pull seed. */
  const SEED_VERSION = '2026-11-04a';

  const OF_POS = ['LF', 'CF', 'RF'];
  const OF_COUNTS = ['PRE', '2K'];
  const OF_SHADES = ['STRAIGHT', 'G', 'L', 'O', 'P'];
  const OF_STEPS = ['', '10', '20', '30'];

  const INF_POS = ['1B', '2B', 'SS', '3B'];
  const INF_SITS = ['EMPTY', 'RISP', 'DP'];
  /** INF holds always show a selection; blank is not a valid resting state (use N). */
  const INF_HOLDS = ['N', 'H', 'L', 'R', 'EL', 'ER', 'BAG'];
  const INF_STEPS = ['', '3', '6', '9'];

  let mode = 'OF'; // OF | INF
  let sprayHitterName = '';
  let sprayHand = 'R';
  let sprayCount = 'PRE'; // PRE/2K for OF preview; EMPTY for INF preview uses PRE spray
  let POSITION_SEED = null;

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
  function tr(en, sp) {
    return (typeof getLang === 'function' && getLang() === 'es') ? sp : en;
  }
  function teamName(abbr) {
    return ({
      PON: 'Leones de Ponce', SAN: 'Cangrejeros de Santurce', CAG: 'Criollos de Caguas',
      CAR: 'Gigantes de Carolina', MAY: 'Indios de Mayagüez', SJU: 'Senadores de San Juan',
    })[abbr] || abbr;
  }
  function lastUpper(name) {
    if (typeof ofPosLastNameUpper === 'function') return ofPosLastNameUpper(name);
    const parts = String(name || '').trim().split(/\s+/);
    return (parts[parts.length - 1] || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toUpperCase();
  }
  function norm(name) {
    return (typeof normPersonName === 'function')
      ? normPersonName(name)
      : String(name || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  }

  function loadStore() {
    let raw = {};
    try { raw = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}; } catch (e) { raw = {}; }
    if (!raw.of) raw.of = {};
    if (!raw.inf) raw.inf = {};
    if (!raw.hands) raw.hands = {};
    if (!raw.lineups) raw.lineups = {};
    migrateLegacy(raw);
    applySeed(raw);
    return raw;
  }
  function saveStore(store, opts) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(store)); } catch (e) {}
    if (opts && opts.silent) return;
    try {
      if (typeof window.queueGameDayPublish === 'function') window.queueGameDayPublish();
    } catch (e) {}
  }
  function goldenNameNeedles(seed) {
    const names = (seed && seed.meta && seed.meta.golden_names) || ['Nolan Arenado', 'Chris Arroyo'];
    return names.map((n) => norm(n)).filter(Boolean);
  }
  function isGoldenKey(key, needles) {
    const k = String(key || '').toLowerCase();
    return needles.some((n) => n && k.includes(n));
  }
  /** Merge spray seed under localStorage. Coach/local edits win; golden keys always kept. */
  function applySeed(store) {
    const seed = POSITION_SEED;
    if (!seed || typeof seed !== 'object') return;
    const ver = `${SEED_VERSION}`;
    const needles = goldenNameNeedles(seed);
    const keepOf = {};
    const keepInf = {};
    const keepHands = {};
    Object.keys(store.of || {}).forEach((k) => {
      if (isGoldenKey(k, needles)) keepOf[k] = store.of[k];
    });
    Object.keys(store.inf || {}).forEach((k) => {
      if (isGoldenKey(k, needles)) keepInf[k] = store.inf[k];
    });
    Object.keys(store.hands || {}).forEach((k) => {
      if (isGoldenKey(k, needles)) keepHands[k] = store.hands[k];
    });

    if (store._posSeedVer !== ver) {
      // Fresh seed: install all generated cells, then restore golden local overrides.
      store.of = Object.assign({}, seed.of || {}, keepOf);
      store.inf = Object.assign({}, seed.inf || {}, keepInf);
      store.hands = Object.assign({}, seed.hands || {}, store.hands || {}, keepHands);
      if (!store.lineups || !Object.keys(store.lineups).length) {
        store.lineups = Object.assign({}, seed.lineups || {});
      }
      store._posSeedVer = ver;
      store._seed = true;
      saveStore(store, { silent: true });
      return;
    }
    // Same version: fill any missing keys only (do not clobber coach edits).
    Object.keys(seed.of || {}).forEach((k) => {
      if (!Object.prototype.hasOwnProperty.call(store.of, k)) store.of[k] = seed.of[k];
    });
    Object.keys(seed.inf || {}).forEach((k) => {
      if (!Object.prototype.hasOwnProperty.call(store.inf, k)) store.inf[k] = seed.inf[k];
    });
    Object.keys(seed.hands || {}).forEach((k) => {
      if (!Object.prototype.hasOwnProperty.call(store.hands, k)) store.hands[k] = seed.hands[k];
    });
  }
  function setPositionSeed(seed) {
    POSITION_SEED = seed && typeof seed === 'object' ? seed : null;
    if (!POSITION_SEED) return;
    // Force merge now that seed is available (loadStore may have run earlier empty).
    let raw = {};
    try { raw = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}; } catch (e) { raw = {}; }
    if (!raw.of) raw.of = {};
    if (!raw.inf) raw.inf = {};
    if (!raw.hands) raw.hands = {};
    if (!raw.lineups) raw.lineups = {};
    migrateLegacy(raw);
    applySeed(raw);
    // Entry grid may already be on screen when CACHE finishes loading.
    if (document.getElementById('tbl-ofpos')) render();
  }
  function migrateLegacy(store) {
    if (store._migratedOfV1) return;
    let legacy = {};
    try { legacy = JSON.parse(localStorage.getItem(LEGACY_OF_KEY) || '{}') || {}; } catch (e) {}
    if (legacy.lineups) Object.assign(store.lineups, legacy.lineups);
    if (legacy.hands) Object.assign(store.hands, legacy.hands);
    const cells = legacy.cells || {};
    Object.keys(cells).forEach((key) => {
      // team|norm|pos|count|hand
      const parts = key.split('|');
      if (parts.length < 5) return;
      const cell = cells[key] || {};
      const shade = String(cell.shade || 'STRAIGHT').toUpperCase();
      const steps = String(cell.steps || '').trim();
      let code = 'STRAIGHT';
      if (shade && shade !== 'STRAIGHT') code = steps ? `${shade}-${steps}` : shade;
      else if (steps) code = `STRAIGHT`; // keep straight
      if (code === 'STRAIGHT' && !steps && !cell.shade) return;
      const nk = `${parts[0]}|${parts[1]}|${parts[2]}|${parts[3]}|${parts[4]}`;
      if (!store.of[nk]) store.of[nk] = code;
    });
    store._migratedOfV1 = true;
    saveStore(store, { silent: true });
  }

  function team() {
    return document.getElementById('ofpos-team')?.value || 'PON';
  }
  function hitters(t) {
    if (typeof ofPosTeamHitters === 'function') return ofPosTeamHitters(t || team());
    return [];
  }
  /** Resolve strategy-card player id → advance/roster display name. */
  function nameForStrategyId(id, t) {
    const nid = Number(id);
    if (!nid) return '';
    try {
      const cardPlayers = (typeof CACHE !== 'undefined' && CACHE.cardStats && CACHE.cardStats.players) || [];
      const hit = cardPlayers.find((p) => Number(p.id) === nid && (!t || p.team === t));
      if (hit && hit.name) return hit.name;
      const any = cardPlayers.find((p) => Number(p.id) === nid);
      if (any && any.name) return any.name;
    } catch (e) {}
    const pool = hitters(t);
    for (let i = 0; i < pool.length; i++) {
      const h = pool[i];
      if (!h) continue;
      if (typeof playerStrategyId === 'function' && playerStrategyId(h) === nid) return h.name;
      if (Number(h._roster_id) === nid || Number(h.mlb_id) === nid || Number(h.player_id) === nid || Number(h.id) === nid) {
        return h.name;
      }
    }
    return '';
  }
  /**
   * Night's strategy card order: batting 1–9, then checked bench (max 15 slots).
   * Returns null when that team has no strategy lineup yet.
   */
  function strategyCardLineupNames(t) {
    if (!window.CardApp || typeof CardApp.getLineupIds !== 'function') return null;
    const lineupIds = CardApp.getLineupIds(t) || [];
    if (!lineupIds.length) return null;
    const activeIds = (typeof CardApp.getActiveHitterIds === 'function'
      ? (CardApp.getActiveHitterIds(t) || lineupIds)
      : lineupIds);
    const seen = new Set();
    const names = [];
    activeIds.forEach((id) => {
      const nm = nameForStrategyId(id, t);
      if (!nm) return;
      const key = norm(nm);
      if (seen.has(key)) return;
      seen.add(key);
      names.push(nm);
    });
    if (!names.length) return null;
    return Array.from({ length: 15 }, (_, i) => names[i] || '');
  }
  function lineupNames(t) {
    // Strategy card wins over seed / saved A–Z roster order.
    const fromCard = strategyCardLineupNames(t);
    if (fromCard && fromCard.some(Boolean)) return fromCard;
    const store = loadStore();
    const saved = store.lineups[t];
    if (Array.isArray(saved) && saved.length) {
      return Array.from({ length: 15 }, (_, i) => saved[i] || '');
    }
    const pool = hitters(t).slice(0, 9).map((h) => h.name);
    return Array.from({ length: 15 }, (_, i) => pool[i] || '');
  }
  function setLineupSlot(t, idx, name) {
    // Manual slot edits only when strategy card has no lineup for this team.
    if (strategyCardLineupNames(t)) {
      alert(tr(
        'Lineup order comes from the Strategy Card (1–9 + checked bench). Edit it there.',
        'El orden viene de la Carta de Estrategia (1–9 + banca marcada). Edítalo allí.'
      ));
      render();
      return;
    }
    const store = loadStore();
    const row = lineupNames(t).slice();
    row[idx] = name || '';
    store.lineups[t] = row;
    saveStore(store);
    render();
  }
  function handCode(bats) {
    const b = String(bats || '').trim().toUpperCase();
    if (b.startsWith('L')) return 'L';
    if (b.startsWith('S') || b === 'B' || b.startsWith('SWITCH')) return 'S';
    if (b.startsWith('R')) return 'R';
    return '';
  }
  function hitterHand(h, t) {
    const store = loadStore();
    const key = `${t}|${norm(h && h.name)}`;
    if (Object.prototype.hasOwnProperty.call(store.hands, key)) return handCode(store.hands[key]);
    return handCode(h && h.bats);
  }
  function setHand(t, name, bats) {
    const store = loadStore();
    store.hands[`${t}|${norm(name)}`] = handCode(bats);
    saveStore(store);
  }

  function ofKey(t, name, pos, count, pHand) {
    return `${t}|${norm(name)}|${pos}|${count}|${pHand}`;
  }
  function infKey(t, name, pos, sit, pHand) {
    return `${t}|${norm(name)}|${pos}|${sit}|${pHand}`;
  }
  function getOf(t, name, pos, count, pHand) {
    return loadStore().of[ofKey(t, name, pos, count, pHand)] || '';
  }
  function setOf(t, name, pos, count, pHand, code) {
    const store = loadStore();
    const k = ofKey(t, name, pos, count, pHand);
    const v = String(code || '').trim().toUpperCase();
    if (!v) delete store.of[k];
    else store.of[k] = v;
    saveStore(store);
  }
  function getInf(t, name, pos, sit, pHand) {
    // Missing / blank INF cells default to N (neutral) — never leave the UI empty.
    return loadStore().inf[infKey(t, name, pos, sit, pHand)] || 'N';
  }
  function setInf(t, name, pos, sit, pHand, code) {
    const store = loadStore();
    const k = infKey(t, name, pos, sit, pHand);
    const v = String(code || '').trim().toUpperCase() || 'N';
    store.inf[k] = v;
    saveStore(store);
  }

  function parseOfCode(code) {
    const s = String(code || '').trim().toUpperCase();
    if (!s || s === 'STRAIGHT') return { shade: 'STRAIGHT', steps: '' };
    const m = s.match(/^(G|L|O|P)-?(10|20|30)?$/);
    if (m) return { shade: m[1], steps: m[2] || '' };
    if (OF_SHADES.includes(s)) return { shade: s, steps: '' };
    return { shade: 'STRAIGHT', steps: '' };
  }
  function composeOf(shade, steps) {
    const sh = String(shade || 'STRAIGHT').toUpperCase();
    if (!sh || sh === 'STRAIGHT') return 'STRAIGHT';
    const st = String(steps || '').trim();
    return st ? `${sh}-${st}` : sh;
  }
  function parseInfCode(code) {
    const s = String(code || '').trim().toUpperCase();
    if (!s) return { hold: 'N', steps: '' };
    if (s === 'BAG' || s === 'N') return { hold: s, steps: '' };
    const m = s.match(/^(EL|ER|H|L|R|N|M)-?(\d+)?$/);
    if (m) return { hold: m[1] === 'M' ? 'N' : m[1], steps: m[2] || '' };
    if (INF_HOLDS.includes(s)) return { hold: s, steps: '' };
    return { hold: 'N', steps: '' };
  }
  function composeInf(hold, steps) {
    const h = String(hold || 'N').toUpperCase() || 'N';
    if (h === 'BAG' || h === 'N') return h;
    const st = String(steps || '').trim();
    return st ? `${h}-${st}` : h;
  }

  function ofSlotOrder() {
    const out = [];
    OF_COUNTS.forEach((count) => OF_POS.forEach((pos) => out.push({ pos, count, label: `${pos} ${count}` })));
    return out;
  }

  function setMode(next) {
    mode = next === 'INF' ? 'INF' : 'OF';
    document.querySelectorAll('[data-pos-mode]').forEach((btn) => {
      const on = btn.dataset.posMode === mode;
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    const ofBtns = document.getElementById('pos-print-of');
    const infBtns = document.getElementById('pos-print-inf');
    if (ofBtns) ofBtns.hidden = mode !== 'OF';
    if (infBtns) infBtns.hidden = mode !== 'INF';
    renderEntry();
    renderSpray();
  }

  function renderLineup() {
    const root = document.getElementById('ofpos-lineup');
    if (!root) return;
    const t = team();
    const hs = hitters(t);
    const names = lineupNames(t);
    const opts = ['<option value="">—</option>']
      .concat(hs.map((h) => {
        const hand = hitterHand(h, t) || '—';
        const label = (typeof formatLastFirst === 'function' ? formatLastFirst(h.name) : h.name) + ` · ${hand}`;
        return `<option value="${esc(h.name)}">${esc(label)}</option>`;
      })).join('');
    root.innerHTML = names.map((nm, i) => `
      <div class="ofpos-lineup-row">
        <span class="slot">${i + 1}</span>
        <select data-slot="${i}">${opts}</select>
      </div>`).join('');
    root.querySelectorAll('select').forEach((sel, i) => {
      sel.value = names[i] || '';
      sel.onchange = () => setLineupSlot(t, i, sel.value);
    });
  }

  function ofSelect(shade, steps, meta) {
    const sh = shade || 'STRAIGHT';
    const st = steps || '';
    return `<span class="pos-code-pair" ${meta}>
      <select class="pos-of-shade" aria-label="shade">${OF_SHADES.map((s) => `<option value="${s}"${s === sh ? ' selected' : ''}>${s}</option>`).join('')}</select>
      <select class="pos-of-steps" aria-label="steps">${OF_STEPS.map((s) => `<option value="${s}"${s === st ? ' selected' : ''}>${s || '—'}</option>`).join('')}</select>
    </span>`;
  }
  function infSelect(hold, steps, meta) {
    const h = hold || '';
    const st = steps || '';
    return `<span class="pos-code-pair" ${meta}>
      <select class="pos-inf-hold" aria-label="hold">${INF_HOLDS.map((s) => `<option value="${s}"${s === h ? ' selected' : ''}>${s}</option>`).join('')}</select>
      <select class="pos-inf-steps" aria-label="steps">${INF_STEPS.map((s) => `<option value="${s}"${s === st ? ' selected' : ''}>${s || '—'}</option>`).join('')}</select>
    </span>`;
  }

  function isBenchName(t, name) {
    if (!name) return false;
    const names = lineupNames(t);
    for (let i = 9; i < names.length; i++) {
      if (names[i] === name) return true;
    }
    return false;
  }

  function renderEntry() {
    const tbody = document.querySelector('#tbl-ofpos tbody');
    const thead = document.querySelector('#tbl-ofpos thead');
    if (!tbody || !thead) return;
    const t = team();
    const hs = hitters(t);
    if (mode === 'OF') {
      thead.innerHTML = `<tr class="pos-sheet-title"><th colspan="5">OF Positioning</th></tr>
        <tr class="pos-sheet-labels">
          <th>Hand</th><th>Hitter</th><th>Position</th>
          <th>LHP</th><th>RHP</th>
        </tr>`;
      const rows = [];
      hs.forEach((h) => {
        const hand = hitterHand(h, t);
        const nameCls = handNameClass(hand);
        const benchCls = isBenchName(t, h.name) ? ' pos-bench-name' : '';
        let first = true;
        ofSlotOrder().forEach(({ pos, count, label }) => {
          const L = parseOfCode(getOf(t, h.name, pos, count, 'L'));
          const R = parseOfCode(getOf(t, h.name, pos, count, 'R'));
          const metaL = `data-team="${esc(t)}" data-hitter="${esc(h.name)}" data-pos="${pos}" data-count="${count}" data-phand="L"`;
          const metaR = `data-team="${esc(t)}" data-hitter="${esc(h.name)}" data-pos="${pos}" data-count="${count}" data-phand="R"`;
          rows.push(`<tr class="pos-entry-row${first ? ' pos-hitter-start' : ''}" data-hitter="${esc(h.name)}" data-count="${count}">
            <td>${first ? `<select class="pos-hand" data-team="${esc(t)}" data-hitter="${esc(h.name)}">
              ${['', 'R', 'L', 'S'].map((x) => `<option value="${x}"${x === hand ? ' selected' : ''}>${x || '—'}</option>`).join('')}
            </select>` : ''}</td>
            <td class="ofpos-hitter${first && nameCls ? ` ${nameCls}` : ''}${first ? benchCls : ''}">${first ? esc((typeof formatLastFirst === 'function' ? formatLastFirst(h.name) : h.name) || h.name) : ''}</td>
            <td>${esc(label)}</td>
            <td class="pos-input">${ofSelect(L.shade, L.steps, metaL)}<span class="pos-code-out">${esc(composeOf(L.shade, L.steps))}</span></td>
            <td class="pos-input">${ofSelect(R.shade, R.steps, metaR)}<span class="pos-code-out">${esc(composeOf(R.shade, R.steps))}</span></td>
          </tr>`);
          first = false;
        });
      });
      tbody.innerHTML = rows.join('') || `<tr><td colspan="5" class="note">No hitters.</td></tr>`;
    } else {
      thead.innerHTML = `<tr class="pos-sheet-title"><th colspan="9">INF Positioning</th></tr>
        <tr class="pos-sheet-labels">
          <th>Hand</th><th>Hitter</th><th>Pos</th>
          <th colspan="3">LHP · Empty / RISP / DP</th>
          <th colspan="3">RHP · Empty / RISP / DP</th>
        </tr>`;
      const rows = [];
      hs.forEach((h) => {
        const hand = hitterHand(h, t);
        const nameCls = handNameClass(hand);
        const benchCls = isBenchName(t, h.name) ? ' pos-bench-name' : '';
        let first = true;
        INF_POS.forEach((pos) => {
          const cells = (pHand) => INF_SITS.map((sit) => {
            const parsed = parseInfCode(getInf(t, h.name, pos, sit, pHand));
            const meta = `data-team="${esc(t)}" data-hitter="${esc(h.name)}" data-pos="${pos}" data-sit="${sit}" data-phand="${pHand}"`;
            return `<td class="pos-input">${infSelect(parsed.hold, parsed.steps, meta)}<span class="pos-code-out">${esc(composeInf(parsed.hold, parsed.steps))}</span></td>`;
          }).join('');
          rows.push(`<tr class="pos-entry-row${first ? ' pos-hitter-start' : ''}" data-hitter="${esc(h.name)}">
            <td>${first ? `<select class="pos-hand" data-team="${esc(t)}" data-hitter="${esc(h.name)}">
              ${['', 'R', 'L', 'S'].map((x) => `<option value="${x}"${x === hand ? ' selected' : ''}>${x || '—'}</option>`).join('')}
            </select>` : ''}</td>
            <td class="ofpos-hitter${first && nameCls ? ` ${nameCls}` : ''}${first ? benchCls : ''}">${first ? esc((typeof formatLastFirst === 'function' ? formatLastFirst(h.name) : h.name) || h.name) : ''}</td>
            <td>${pos}</td>
            ${cells('L')}${cells('R')}
          </tr>`);
          first = false;
        });
      });
      tbody.innerHTML = rows.join('') || `<tr><td colspan="9" class="note">No hitters.</td></tr>`;
    }
    bindEntry();
    highlightSprayRow();
  }

  function bindEntry() {
    const table = document.getElementById('tbl-ofpos');
    if (!table || table.dataset.posBound === '2') return;
    table.dataset.posBound = '2';
    table.addEventListener('change', (ev) => {
      const el = ev.target;
      if (!el.classList) return;
      if (el.classList.contains('pos-hand')) {
        setHand(el.dataset.team, el.dataset.hitter, el.value);
        render();
        return;
      }
      const wrap = el.closest('.pos-code-pair');
      if (!wrap) return;
      if (el.classList.contains('pos-of-shade') || el.classList.contains('pos-of-steps')) {
        const shade = wrap.querySelector('.pos-of-shade')?.value || 'STRAIGHT';
        const steps = wrap.querySelector('.pos-of-steps')?.value || '';
        const code = composeOf(shade, steps);
        setOf(wrap.dataset.team, wrap.dataset.hitter, wrap.dataset.pos, wrap.dataset.count, wrap.dataset.phand, code);
        const out = wrap.parentElement?.querySelector('.pos-code-out');
        if (out) out.textContent = code;
      }
      if (el.classList.contains('pos-inf-hold') || el.classList.contains('pos-inf-steps')) {
        const hold = wrap.querySelector('.pos-inf-hold')?.value || '';
        const steps = wrap.querySelector('.pos-inf-steps')?.value || '';
        const code = composeInf(hold, steps);
        setInf(wrap.dataset.team, wrap.dataset.hitter, wrap.dataset.pos, wrap.dataset.sit, wrap.dataset.phand, code);
        const out = wrap.parentElement?.querySelector('.pos-code-out');
        if (out) out.textContent = code;
      }
    });
    table.addEventListener('click', (ev) => {
      const tr = ev.target.closest('tr.pos-entry-row');
      if (!tr) return;
      sprayHitterName = tr.dataset.hitter || '';
      if (tr.dataset.count) sprayCount = tr.dataset.count;
      highlightSprayRow();
      renderSpray();
    });
    table.addEventListener('focusin', (ev) => {
      const tr = ev.target.closest('tr.pos-entry-row');
      if (!tr) return;
      sprayHitterName = tr.dataset.hitter || '';
      if (tr.dataset.count) sprayCount = tr.dataset.count;
      highlightSprayRow();
      renderSpray();
    });
  }
  function highlightSprayRow() {
    document.querySelectorAll('#tbl-ofpos tr.pos-entry-row').forEach((tr) => {
      tr.classList.toggle('is-active', tr.dataset.hitter === sprayHitterName);
    });
  }

  /* ── Small spray preview (screen only — never printed) ── */
  function activeHitter() {
    const t = team();
    const hs = hitters(t);
    return hs.find((h) => h.name === sprayHitterName) || hs[0] || null;
  }
  function sprayPoints(h, pHand, twoK) {
    if (!h || typeof sprayPointsForCharts !== 'function') return [];
    const want = pHand === 'L' || pHand === 'R' ? pHand : '';
    return sprayPointsForCharts(h).filter((p) => {
      const hand = typeof sprayPitcherHand === 'function' ? sprayPitcherHand(p) : '';
      if (want && hand !== want) return false;
      if (twoK !== null && twoK !== undefined) {
        if (Boolean(p.two_strikes) !== Boolean(twoK)) return false;
      }
      return true;
    });
  }
  /* Straight-up feet — same plane as drawSpray / OFPOS_ANCHOR (home bottom, +y CF, +x RF). */
  const POS_ANCHORS_FALLBACK = {
    OF: {
      LF: { x: -148, y: 268 },
      CF: { x: 0, y: 320 },
      RF: { x: 148, y: 268 },
    },
    INF: {
      '3B': { x: -78, y: 108 },
      SS: { x: -46, y: 150 },
      '2B': { x: 46, y: 150 },
      '1B': { x: 78, y: 108 },
    },
  };
  function posAnchors() {
    const src = (typeof OFPOS_ANCHOR === 'object' && OFPOS_ANCHOR) ? OFPOS_ANCHOR : null;
    const keys = mode === 'OF' ? ['LF', 'CF', 'RF'] : ['3B', 'SS', '2B', '1B'];
    const out = {};
    keys.forEach((pos) => {
      const a = (src && src[pos]) || (POS_ANCHORS_FALLBACK[mode] || {})[pos];
      if (a && Number.isFinite(a.x) && Number.isFinite(a.y)) out[pos] = { x: a.x, y: a.y };
    });
    return out;
  }

  function renderSpray() {
    const canvas = document.getElementById('pos-spray-canvas');
    const label = document.getElementById('pos-spray-label');
    if (!canvas) return;
    const h = activeHitter();
    if (label) {
      label.textContent = h
        ? `${(typeof formatLastFirst === 'function' ? formatLastFirst(h.name) : h.name)} · vs ${sprayHand}HP · ${sprayCount}`
        : tr('Select a hitter row', 'Elige una fila de bateador');
    }

    const pane = canvas.closest('.pos-spray-pane') || canvas.parentElement;
    const avail = Math.floor((pane && pane.clientWidth) || 360) - 24;
    const aspect = (typeof SPRAY_ASPECT === 'number' && SPRAY_ASPECT > 0) ? SPRAY_ASPECT : Math.SQRT2;
    const dispW = Math.max(280, Math.min(360, avail > 40 ? avail : 360));
    const dispH = Math.round(dispW / aspect);
    // INF zooms to ~220' so basepaths/fielder anchors fill the pane; OF uses full 400' fence.
    const viewFt = mode === 'INF' ? 220 : 400;

    let pts = [];
    if (h) {
      const twoK = sprayCount === '2K';
      pts = sprayPoints(h, sprayHand, twoK);
      if (pts.length < 3) pts = sprayPoints(h, sprayHand, null);
      const depthMin = mode === 'OF' ? 140 : 40;
      const depthMax = mode === 'INF' ? 210 : 9999;
      pts = pts.filter((p) => {
        const y = Number(p.y_ft != null ? p.y_ft : p.y);
        return Number.isFinite(y) && y >= depthMin && y <= depthMax;
      });
      if (typeof limitRecentSprayPoints === 'function') {
        pts = limitRecentSprayPoints(pts, 80);
      } else {
        pts = pts.slice(-80);
      }
    }

    if (typeof drawSpray !== 'function') {
      const ctx = canvas.getContext('2d');
      canvas.width = dispW;
      canvas.height = dispH;
      ctx.fillStyle = '#e8f0e4';
      ctx.fillRect(0, 0, dispW, dispH);
      ctx.fillStyle = '#333';
      ctx.font = '12px sans-serif';
      ctx.fillText('Spray field unavailable', 12, 24);
      return;
    }

    const anchors = posAnchors();
    const geom = drawSpray(canvas, pts, {
      dispW,
      dispH,
      viewFt,
      hideAreaLabels: true,
      afterDraw(ctx, g) {
        const ink = g.ink || 1;
        Object.keys(anchors).forEach((pos) => {
          const a = anchors[pos];
          const p = g.toCanvas(a.x, a.y);
          ctx.beginPath();
          ctx.arc(p.x, p.y, 7 * ink, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(17,17,17,0.88)';
          ctx.fill();
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 1.6 * ink;
          ctx.stroke();
          ctx.fillStyle = '#111';
          ctx.font = `bold ${Math.max(10, 11 * ink)}px "IBM Plex Mono", monospace`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'bottom';
          ctx.fillText(pos, p.x, p.y - 9 * ink);
        });
        ctx.textAlign = 'left';
        ctx.textBaseline = 'alphabetic';
        ctx.fillStyle = '#111';
        ctx.font = `${Math.max(11, 12 * ink)}px "IBM Plex Mono", monospace`;
        ctx.fillText(`n=${pts.length}`, 8 * ink, 16 * ink);
      },
    });
    // CSS box must match drawSpray buffer aspect exactly (no stretch).
    const cssW = (geom && geom.dispW) || dispW;
    const cssH = (geom && geom.dispH) || dispH;
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
  }

  /* ── Print cards (Excel layouts, no sprays) ── */
  /* Card sheet colors (print + Excel): title medium blue, labels light gray, bench name cells light orange. */
  const POS_FILL = {
    title: '5B9BD5',   // medium blue
    label: 'D9D9D9',   // light gray
    bench: 'FCE4D6',   // light orange (name cells only)
  };

  function printOpen(title, bodyHtml) {
    const w = window.open('', '_blank');
    if (!w) {
      alert(tr('Allow pop-ups to print.', 'Permite pop-ups para imprimir.'));
      return;
    }
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title>
      <style>
        @page { size: letter landscape; margin: 0.35in; }
        html,body{margin:0;background:#fff;color:#111;font-family:Arial,Helvetica,sans-serif}
        h1{font-size:16px;margin:0 0 8px;letter-spacing:.04em}
        h2{font-size:13px;margin:12px 0 6px;background:#111;color:#fff;padding:4px 8px;letter-spacing:.08em}
        .pos-print-grid{display:grid;gap:14px}
        .pos-print-3{grid-template-columns:1fr 1fr 1fr}
        .pos-print-2{grid-template-columns:1fr 1fr}
        table.pos-card{
          width:100%;border-collapse:collapse;font-size:11px;
          border:2.5px solid #000;background:#fff;
        }
        table.pos-card th,table.pos-card td{
          border:1px solid #999;padding:3px 5px;text-align:center;
        }
        table.pos-card th{font-size:10px;letter-spacing:.04em}
        table.pos-card td.name{text-align:left;font-weight:700;width:34%}
        table.pos-card .pos-title{
          font-weight:800;background:#5B9BD5;color:#111;text-align:left;
        }
        table.pos-card .pos-labels th{background:#D9D9D9;font-weight:700;color:#111}
        table.pos-card .pos-bench{background:#FCE4D6}
        .sc-lhb{color:#C0392B!important}
        .sc-shb{color:#1A5276!important}
        @media print {
          h2,table.pos-card .pos-title,table.pos-card .pos-labels th,
          table.pos-card .pos-bench,.sc-lhb,.sc-shb{
            -webkit-print-color-adjust:exact;print-color-adjust:exact
          }
        }
      </style></head><body>${bodyHtml}</body></html>`);
    w.document.close();
    setTimeout(() => { try { w.focus(); w.print(); } catch (e) {} }, 200);
  }

  /** Lineup card slots 1–N (0-based). Slots 10+ (index ≥ 9) are bench. */
  function lineupSlots(t, n) {
    const by = new Map(hitters(t).map((h) => [h.name, h]));
    const names = lineupNames(t);
    const out = [];
    for (let i = 0; i < n; i++) {
      const nm = names[i] || '';
      const h = nm ? by.get(nm) : null;
      out.push({
        name: h ? h.name : nm,
        bats: h ? (h.bats || '') : '',
        hand: h ? (hitterHand(h, t) || '') : '',
        last: nm ? lastUpper(h ? h.name : nm) : '',
        slot: i,
        isBench: i >= 9,
      });
    }
    return out;
  }

  function printNameCell(slot) {
    if (!slot || !slot.last) return '<td class="name"></td>';
    const nc = handNameClass(slot.hand);
    const bench = slot.isBench ? ' pos-bench' : '';
    return `<td class="name${bench}${nc ? ` ${nc}` : ''}">${esc(slot.last)}</td>`;
  }

  function printOf(scope) {
    // scope: 'LF'|'CF'|'RF'|'ALL' — Entire OF = 6 tables (LF/CF/RF × LHP/RHP)
    const t = team();
    const list = lineupSlots(t, 15);
    const positions = scope === 'ALL' ? OF_POS : [scope];
    function ofTable(pos, pHand) {
      const rows = list.map((slot) => {
        const nm = slot.name;
        const pre = nm ? (getOf(t, nm, pos, 'PRE', pHand) || '') : '';
        const two = nm ? (getOf(t, nm, pos, '2K', pHand) || '') : '';
        return `<tr>${printNameCell(slot)}<td></td><td></td><td>${esc(pre)}</td><td>${esc(two)}</td></tr>`;
      }).join('');
      return `<table class="pos-card">
        <thead>
          <tr><th class="pos-title" colspan="5">${esc(pHand + 'HP')}</th></tr>
          <tr class="pos-labels"><th>${esc(pos)}</th><th></th><th></th><th>PRE</th><th>2K</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
    }
    function block(pHand) {
      if (positions.length === 1) {
        return `<div>${ofTable(positions[0], pHand)}</div>`;
      }
      const cols = positions.map((pos) => `<div>${ofTable(pos, pHand)}</div>`).join('');
      return `<h2>${pHand}HP</h2><div class="pos-print-grid pos-print-3">${cols}</div>`;
    }
    const title = scope === 'ALL'
      ? `${teamName(t)} Entire OF Positioning`
      : `${teamName(t)} ${scope} Positioning`;
    const gridClass = positions.length === 1 ? 'pos-print-grid pos-print-2' : '';
    const body = positions.length === 1
      ? `<h1>${esc(title)}</h1><div class="${gridClass}">${block('L')}${block('R')}</div>`
      : `<h1>${esc(title)}</h1>${block('L')}${block('R')}`;
    printOpen(title, body);
  }

  function printInf(scope) {
    // scope: '1B'|'2B'|'SS'|'3B'|'ALL'
    const t = team();
    const list = lineupSlots(t, 15);
    const positions = scope === 'ALL' ? INF_POS : [scope];
    function tableFor(pos, pHand) {
      const rows = list.map((slot) => {
        const nm = slot.name;
        const cells = INF_SITS.map((sit) => `<td>${esc(nm ? (getInf(t, nm, pos, sit, pHand) || '') : '')}</td>`).join('');
        return `<tr>${printNameCell(slot)}<td></td><td></td>${cells}</tr>`;
      }).join('');
      return `<div>
        <table class="pos-card">
          <thead>
            <tr><th class="pos-title" colspan="6">${esc(pHand + 'HP')}</th></tr>
            <tr class="pos-labels"><th>${esc(pos)}</th><th></th><th></th><th>Empty</th><th>RISP</th><th>DP</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
    }
    function block(pHand) {
      if (positions.length === 1) {
        return tableFor(positions[0], pHand);
      }
      // Excel: 1B|2B on top, SS|3B below
      return `<h2>${pHand}HP</h2>
        <div class="pos-print-grid pos-print-2">${tableFor('1B', pHand)}${tableFor('2B', pHand)}</div>
        <div class="pos-print-grid pos-print-2" style="margin-top:10px">${tableFor('SS', pHand)}${tableFor('3B', pHand)}</div>`;
    }
    const title = scope === 'ALL'
      ? `${teamName(t)} Entire Infield Positioning`
      : `${teamName(t)} ${scope} Positioning`;
    const body = positions.length === 1
      ? `<h1>${esc(title)}</h1><div class="pos-print-grid pos-print-2">${block('L')}${block('R')}</div>`
      : `<h1>${esc(title)}</h1>${block('L')}${block('R')}`;
    printOpen(title, body);
  }

  /* ── Excel export matching templates ── */
  /** Batter-name font by hand: L = red, S = blue, R/blank = black. */
  function handNameClass(hand) {
    const h = String(hand || '').trim().toUpperCase();
    if (h === 'L') return 'sc-lhb';
    if (h === 'S') return 'sc-shb';
    return '';
  }
  function handNameFont(hand) {
    const h = String(hand || '').trim().toUpperCase();
    if (h === 'L') return { color: { rgb: 'C0392B' }, bold: true };
    if (h === 'S') return { color: { rgb: '1A5276' }, bold: true };
    return { color: { rgb: '000000' }, bold: true };
  }
  function solidFill(rgb) {
    return { patternType: 'solid', fgColor: { rgb } };
  }
  function borderEdge(style) {
    return { style: style || 'thin', color: { rgb: '000000' } };
  }
  function ensureCell(ws, addr) {
    return ws[addr] || (ws[addr] = { t: 's', v: '' });
  }
  function paintFill(ws, addr, rgb, extra) {
    if (!ws || !addr || !rgb) return;
    const cell = ensureCell(ws, addr);
    cell.s = Object.assign({}, cell.s || {}, { fill: solidFill(rgb) }, extra || {});
  }
  function paintNameCell(ws, addr, hand, opts) {
    if (!ws || !addr) return;
    const cell = ensureCell(ws, addr);
    const style = { font: handNameFont(hand) };
    if (opts && opts.bench) style.fill = solidFill(POS_FILL.bench);
    cell.s = Object.assign({}, cell.s || {}, style);
  }
  function paintRowRange(ws, r0, c0, c1, rgb, extra) {
    for (let c = c0; c <= c1; c++) {
      paintFill(ws, XLSX.utils.encode_cell({ r: r0, c }), rgb, extra);
    }
  }
  /** Thick black outer box around a card table; thin internal grid on every cell. */
  function paintTableBox(ws, r0, r1, c0, c1) {
    if (!ws || r1 < r0 || c1 < c0) return;
    const thick = borderEdge('medium');
    const thin = borderEdge('thin');
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        const cell = ensureCell(ws, XLSX.utils.encode_cell({ r, c }));
        const border = {
          top: r === r0 ? thick : thin,
          bottom: r === r1 ? thick : thin,
          left: c === c0 ? thick : thin,
          right: c === c1 ? thick : thin,
        };
        cell.s = Object.assign({}, cell.s || {}, { border });
      }
    }
  }
  function titleFont() {
    return { bold: true, color: { rgb: '000000' } };
  }
  function labelFont() {
    return { bold: true, color: { rgb: '000000' } };
  }

  function exportOfExcel() {
    if (typeof XLSX === 'undefined') {
      alert('Excel library failed to load.');
      return;
    }
    const t = team();
    const byName = new Map(hitters(t).map((h) => [h.name, h]));
    const lineup = lineupNames(t);
    const manualHitters = hitters(t);
    const lineupHands = [];
    const lineupAoa = [['#', 'Hitters', 'Hand']];
    for (let i = 0; i < 15; i++) {
      const h = byName.get(lineup[i]);
      const hand = h ? (hitterHand(h, t) || '') : '';
      lineupHands.push(hand);
      lineupAoa.push([i + 1, h ? lastUpper(h.name) : (lineup[i] ? lastUpper(lineup[i]) : ''), hand]);
    }
    const manualAoa = [['Hand', '', 'Hitter', 'Position', 'HELPER COL', '', 'LHP', '', 'RHP']];
    const manualNameHands = []; // row index in sheet (1-based data rows start at 2)
    manualHitters.forEach((h) => {
      const hand = hitterHand(h, t) || '';
      const last = lastUpper(h.name);
      ofSlotOrder().forEach(({ pos, count, label }) => {
        manualAoa.push([hand, '', last, label, '', '', getOf(t, h.name, pos, count, 'L') || '', '', getOf(t, h.name, pos, count, 'R') || '']);
        manualNameHands.push(hand);
      });
    });
    const manualLast = Math.max(manualAoa.length, 2);
    const cardAoa = [[]];
    const cardNameStyles = []; // {r,c,hand,bench} 0-based
    const cardHeaderStyles = []; // {r,c0,c1,kind:'title'|'label'} 0-based
    const cardBoxes = []; // {r0,r1,c0,c1} 0-based — every LF/CF/RF table
    const OF_GROUPS = [[0, 4, 'LF'], [6, 10, 'CF'], [12, 16, 'RF']];
    function pushHandBlock(label, pHand) {
      const head = Array(17).fill('');
      head[0] = label; head[6] = label; head[12] = label;
      cardAoa.push(head);
      const titleR = cardAoa.length - 1;
      OF_GROUPS.forEach(([c0, c1]) => {
        cardHeaderStyles.push({ r: titleR, c0, c1, kind: 'title' });
      });
      const sub = Array(17).fill('');
      sub[0] = 'LF'; sub[3] = 'PRE'; sub[4] = '2K';
      sub[6] = 'CF'; sub[9] = 'PRE'; sub[10] = '2K';
      sub[12] = 'RF'; sub[15] = 'PRE'; sub[16] = '2K';
      cardAoa.push(sub);
      const labelR = cardAoa.length - 1;
      OF_GROUPS.forEach(([c0, c1]) => {
        cardHeaderStyles.push({ r: labelR, c0, c1, kind: 'label' });
      });
      const dataStart = cardAoa.length;
      for (let i = 0; i < 15; i++) {
        const nm = lineup[i] || '';
        const h = byName.get(nm);
        const last = h ? lastUpper(h.name) : (nm ? lastUpper(nm) : '');
        const nameKey = h ? h.name : nm;
        const hand = h ? (hitterHand(h, t) || '') : (lineupHands[i] || '');
        const bench = i >= 9;
        const row = Array(17).fill('');
        OF_GROUPS.forEach(([c0, , pos]) => {
          row[c0] = last;
          row[c0 + 3] = nameKey ? (getOf(t, nameKey, pos, 'PRE', pHand) || '') : '';
          row[c0 + 4] = nameKey ? (getOf(t, nameKey, pos, '2K', pHand) || '') : '';
          if (last) cardNameStyles.push({ r: dataStart + i, c: c0, hand, bench });
        });
        cardAoa.push(row);
      }
      const dataEnd = cardAoa.length - 1;
      OF_GROUPS.forEach(([c0, c1]) => {
        cardBoxes.push({ r0: titleR, r1: dataEnd, c0, c1 });
      });
    }
    pushHandBlock('LHP', 'L');
    cardAoa.push([]); cardAoa.push([]); cardAoa.push([]);
    pushHandBlock('RHP', 'R');

    const wb = XLSX.utils.book_new();
    const wsLineup = XLSX.utils.aoa_to_sheet(lineupAoa);
    lineupHands.forEach((hand, i) => paintNameCell(wsLineup, `B${i + 2}`, hand, { bench: i >= 9 }));
    const wsManual = XLSX.utils.aoa_to_sheet(manualAoa);
    for (let r = 2; r <= manualLast; r++) wsManual[`E${r}`] = { f: `C${r}&"|"&D${r}` };
    manualNameHands.forEach((hand, i) => paintNameCell(wsManual, `C${i + 2}`, hand));
    const wsCard = XLSX.utils.aoa_to_sheet(cardAoa);
    // Boxes first so fills/fonts layer on top of borders.
    cardBoxes.forEach(({ r0, r1, c0, c1 }) => paintTableBox(wsCard, r0, r1, c0, c1));
    cardHeaderStyles.forEach(({ r, c0, c1, kind }) => {
      const rgb = kind === 'title' ? POS_FILL.title : POS_FILL.label;
      const extra = { font: kind === 'title' ? titleFont() : labelFont() };
      paintRowRange(wsCard, r, c0, c1, rgb, extra);
    });
    cardNameStyles.forEach(({ r, c, hand, bench }) => {
      paintNameCell(wsCard, XLSX.utils.encode_cell({ r, c }), hand, { bench });
    });
    XLSX.utils.book_append_sheet(wb, wsLineup, 'LINEUP');
    XLSX.utils.book_append_sheet(wb, wsManual, 'Manual Data');
    XLSX.utils.book_append_sheet(wb, wsCard, 'LHP - RHP ');
    XLSX.writeFile(wb, `${teamName(t)} OF Defensive Positioning.xlsx`);
  }

  function exportInfExcel() {
    if (typeof XLSX === 'undefined') {
      alert('Excel library failed to load.');
      return;
    }
    const t = team();
    const byName = new Map(hitters(t).map((h) => [h.name, h]));
    const lineup = lineupNames(t);
    const lineupHands = [];
    const lineupAoa = [['#', 'Hitters', 'Hand']];
    for (let i = 0; i < 15; i++) {
      const h = byName.get(lineup[i]);
      const hand = h ? (hitterHand(h, t) || '') : '';
      lineupHands.push(hand);
      lineupAoa.push([i + 1, h ? lastUpper(h.name) : (lineup[i] ? lastUpper(lineup[i]) : ''), hand]);
    }
    const manualAoa = [
      ['', '', '', '', '', '', 'LHP', '', '', 'RHP', '', ''],
      ['Hand', '', 'Hitters', 'Position', 'HELPER COL', '', 'EMPTY', 'RISP', 'DP', 'EMPTY', 'RISP', 'DP'],
    ];
    const manualNameHands = [];
    hitters(t).forEach((h) => {
      const hand = hitterHand(h, t) || '';
      const last = lastUpper(h.name);
      INF_POS.forEach((pos) => {
        manualAoa.push([
          hand, '', last, pos, '',
          '',
          getInf(t, h.name, pos, 'EMPTY', 'L') || '',
          getInf(t, h.name, pos, 'RISP', 'L') || '',
          getInf(t, h.name, pos, 'DP', 'L') || '',
          getInf(t, h.name, pos, 'EMPTY', 'R') || '',
          getInf(t, h.name, pos, 'RISP', 'R') || '',
          getInf(t, h.name, pos, 'DP', 'R') || '',
        ]);
        manualNameHands.push(hand);
      });
    });
    const manualLast = Math.max(manualAoa.length, 3);

    function buildHandSheet(pHand) {
      // Values filled directly (no formulas) so print/export is self-contained
      const aoa = [[]];
      const nameStyles = []; // {r,c,hand,bench} 0-based
      const headerStyles = []; // {r,c0,c1,kind}
      const boxes = []; // {r0,r1,c0,c1}
      const INF_GROUPS = [[0, 5], [9, 14]];
      function pair(posA, posB) {
        const head = Array(15).fill('');
        head[0] = pHand + 'HP'; head[9] = pHand + 'HP';
        aoa.push(head);
        const titleR = aoa.length - 1;
        INF_GROUPS.forEach(([c0, c1]) => {
          headerStyles.push({ r: titleR, c0, c1, kind: 'title' });
        });
        const sub = Array(15).fill('');
        sub[0] = posA; sub[3] = 'Empty'; sub[4] = 'RISP'; sub[5] = 'DP';
        sub[9] = posB; sub[12] = 'Empty'; sub[13] = 'RISP'; sub[14] = 'DP';
        aoa.push(sub);
        const labelR = aoa.length - 1;
        INF_GROUPS.forEach(([c0, c1]) => {
          headerStyles.push({ r: labelR, c0, c1, kind: 'label' });
        });
        for (let i = 0; i < 15; i++) {
          const nm = lineup[i] || '';
          const h = byName.get(nm);
          const last = h ? lastUpper(h.name) : (nm ? lastUpper(nm) : '');
          const nameKey = h ? h.name : nm;
          const hand = h ? (hitterHand(h, t) || '') : (lineupHands[i] || '');
          const bench = i >= 9;
          const row = Array(15).fill('');
          row[0] = last;
          row[3] = nameKey ? (getInf(t, nameKey, posA, 'EMPTY', pHand) || '') : '';
          row[4] = nameKey ? (getInf(t, nameKey, posA, 'RISP', pHand) || '') : '';
          row[5] = nameKey ? (getInf(t, nameKey, posA, 'DP', pHand) || '') : '';
          row[9] = last;
          row[12] = nameKey ? (getInf(t, nameKey, posB, 'EMPTY', pHand) || '') : '';
          row[13] = nameKey ? (getInf(t, nameKey, posB, 'RISP', pHand) || '') : '';
          row[14] = nameKey ? (getInf(t, nameKey, posB, 'DP', pHand) || '') : '';
          const r = aoa.length;
          aoa.push(row);
          if (last) {
            nameStyles.push({ r, c: 0, hand, bench });
            nameStyles.push({ r, c: 9, hand, bench });
          }
        }
        const dataEnd = aoa.length - 1;
        INF_GROUPS.forEach(([c0, c1]) => {
          boxes.push({ r0: titleR, r1: dataEnd, c0, c1 });
        });
      }
      pair('1B', '2B');
      aoa.push([]); aoa.push([]); aoa.push([]);
      pair('SS', '3B');
      const ws = XLSX.utils.aoa_to_sheet(aoa);
      boxes.forEach(({ r0, r1, c0, c1 }) => paintTableBox(ws, r0, r1, c0, c1));
      headerStyles.forEach(({ r, c0, c1, kind }) => {
        const rgb = kind === 'title' ? POS_FILL.title : POS_FILL.label;
        paintRowRange(ws, r, c0, c1, rgb, { font: kind === 'title' ? titleFont() : labelFont() });
      });
      nameStyles.forEach(({ r, c, hand, bench }) => {
        paintNameCell(ws, XLSX.utils.encode_cell({ r, c }), hand, { bench });
      });
      return ws;
    }

    const wb = XLSX.utils.book_new();
    const wsLineup = XLSX.utils.aoa_to_sheet(lineupAoa);
    lineupHands.forEach((hand, i) => paintNameCell(wsLineup, `B${i + 2}`, hand, { bench: i >= 9 }));
    const wsManual = XLSX.utils.aoa_to_sheet(manualAoa);
    for (let r = 3; r <= manualLast; r++) wsManual[`E${r}`] = { f: `C${r}&"|"&D${r}` };
    manualNameHands.forEach((hand, i) => paintNameCell(wsManual, `C${i + 3}`, hand));
    XLSX.utils.book_append_sheet(wb, wsLineup, 'LINEUP');
    XLSX.utils.book_append_sheet(wb, wsManual, 'Manual Data');
    XLSX.utils.book_append_sheet(wb, buildHandSheet('L'), 'LHP');
    XLSX.utils.book_append_sheet(wb, buildHandSheet('R'), 'RHP');
    XLSX.writeFile(wb, `${teamName(t)} Infield Positioning.xlsx`);
  }

  function clearAll() {
    if (!confirm(tr('Clear all INF/OF positioning inputs and lineups on this device?', '¿Borrar todo el posicionamiento INF/OF y alineaciones en este dispositivo?'))) return;
    try { localStorage.removeItem(STORE_KEY); } catch (e) {}
    // Re-install spray seed defaults after clear.
    if (POSITION_SEED) setPositionSeed(POSITION_SEED);
    render();
  }

  function render() {
    const meta = document.getElementById('ofpos-meta');
    const t = team();
    const hs = hitters(t);
    if (meta) {
      meta.textContent = mode === 'OF'
        ? tr(`${hs.length} hitters · OF entry: one row per player × LF/CF/RF × PRE/2K · codes G/L/O/P + 10/20/30 or STRAIGHT`,
          `${hs.length} bateadores · OF: una fila por jugador × LF/CF/RF × PRE/2K · códigos G/L/O/P + 10/20/30 o STRAIGHT`)
        : tr(`${hs.length} hitters · INF entry: one row per player × 1B/2B/SS/3B · Empty/RISP/DP · H N L R EL ER (BAG on 1B DP)`,
          `${hs.length} bateadores · INF: una fila por jugador × 1B/2B/SS/3B · Empty/RISP/DP · H N L R EL ER (BAG en 1B DP)`);
    }
    renderLineup();
    renderEntry();
    if (!sprayHitterName && hs[0]) sprayHitterName = hs[0].name;
    renderSpray();
    setMode(mode);
  }

  function onTeamChange() {
    sprayHitterName = '';
    render();
  }

  window.PosApp = {
    render,
    setMode,
    onTeamChange,
    printOf,
    printInf,
    exportOfExcel,
    exportInfExcel,
    clearAll,
    setPositionSeed,
    seedVersion: SEED_VERSION,
    setSprayHand(h) {
      sprayHand = h === 'L' ? 'L' : 'R';
      document.querySelectorAll('#pos-spray-hand .ofpos-pill').forEach((b) => {
        const on = b.dataset.hand === sprayHand;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      renderSpray();
    },
    setSprayCount(c) {
      sprayCount = c === '2K' ? '2K' : 'PRE';
      document.querySelectorAll('#pos-spray-count .ofpos-pill').forEach((b) => {
        const on = b.dataset.count === sprayCount;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      renderSpray();
    },
  };

  // Back-compat hooks used by pregame.html
  window.renderOfPos = function () { PosApp.render(); };
  window.onOfPosTeamChange = function () { PosApp.onTeamChange(); };
  window.exportOfPosExcel = function () { PosApp.exportOfExcel(); };
  window.clearOfPosOverrides = function () { PosApp.clearAll(); };
  window.setOfPosSubtab = function (name) {
    if (name === 'inf') PosApp.setMode('INF');
    else if (name === 'of' || name === 'grid') PosApp.setMode('OF');
  };

  /** One-shot dump for agents: ?dump_pos=1 downloads JSON + stamps tab title (no storage writes). */
  function dumpPosIfRequested() {
    try {
      if (!/[?&]dump_pos=1(?:&|$)/.test(location.search || '')) return;
      let raw = {};
      try { raw = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}; } catch (e) { raw = {}; }
      const needles = ['nolan arenado', 'chris arroyo'];
      const pick = (obj) => {
        const out = {};
        Object.keys(obj || {}).forEach((k) => {
          const kl = k.toLowerCase();
          if (needles.some((n) => kl.includes(n))) out[k] = obj[k];
        });
        return out;
      };
      const golden = {
        source: 'safari_or_browser_localStorage',
        key: STORE_KEY,
        origin: location.origin + location.pathname,
        seedVer: raw._posSeedVer || null,
        of: pick(raw.of),
        inf: pick(raw.inf),
        hands: pick(raw.hands),
        full: {
          of: raw.of || {},
          inf: raw.inf || {},
          hands: raw.hands || {},
          lineups: raw.lineups || {},
          _posSeedVer: raw._posSeedVer || null,
          _migratedOfV1: !!raw._migratedOfV1,
        },
      };
      const text = JSON.stringify(golden, null, 2);
      const blob = new Blob([text], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'santurce_positioning_golden_dump.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Compact title stamp so AppleScript can read without do JavaScript
      const compact = {
        seedVer: golden.seedVer,
        of: golden.of,
        inf: golden.inf,
        hands: golden.hands,
      };
      document.title = 'POSDUMP:' + JSON.stringify(compact);
    } catch (e) {
      try { document.title = 'POSDUMP_ERR:' + String(e); } catch (e2) {}
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', dumpPosIfRequested);
  } else {
    dumpPosIfRequested();
  }
})();

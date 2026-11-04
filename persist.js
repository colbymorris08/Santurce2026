/* Game-day persistence: publish browser state to GitHub + daily version snapshots.
   Live file: data/game_day_state.json
   Versions: data/versions/index.json + data/versions/YYYY-MM-DD/
   Uses the same PAT as OF/TTH (santurce_gh_pat_v1 → scoutGhPat / githubPutJson).
*/
(function () {
  const STATE_PATH = 'data/game_day_state.json';
  const VERSIONS_INDEX = 'data/versions/index.json';
  const STRATEGY_KEY = 'santurce_strategy_lineup_v1';
  const NOTES_KEY = 'santurce_smallball_notes_v1';
  const JERSEY_KEY = 'santurce_jersey_overrides_v1';
  const ONEPAGE_KEY = 'santurce_pitcher_one_page_notes_v1';
  const TTH_KEY = 'santurce_tth_pop_overrides_v1';
  const OF_KEY = 'santurce_of_arms_overrides_v1';
  const POS_KEY = 'santurce_positioning_v2';
  const MEDIA_PREFIX = 'santurce_pm_v1:';

  const SNAPSHOT_FILES = [
    'game_day_state.json',
    'pregame_tth_pop.json',
    'pregame_of_arms.json',
    'positioning_v2.json',
    'pitcher_one_page_notes.json',
    'pregame_card_stats.json',
  ];

  let publishTimer = null;
  let LIVE_STATE = null;
  let VERSIONS = null;

  function lsGet(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return fallback;
      const v = JSON.parse(raw);
      return v == null ? fallback : v;
    } catch (e) {
      return fallback;
    }
  }
  function lsSet(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      console.warn('game-day localStorage save failed', key, e);
    }
  }
  function assetV() {
    return (typeof ASSET_V !== 'undefined' && ASSET_V) ? ASSET_V : String(Date.now());
  }
  function statusEl() {
    return document.getElementById('gameday-persist-note');
  }
  function revertStatusEl() {
    return document.getElementById('revert-status');
  }
  function setStatus(msg) {
    const el = statusEl();
    if (el) el.textContent = msg;
  }
  function setRevertStatus(msg) {
    const el = revertStatusEl();
    if (el) el.textContent = msg;
  }
  function hasPat() {
    return typeof scoutGhPat === 'function' && !!scoutGhPat();
  }
  function mediaRefsFromLocal() {
    const refs = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith(MEDIA_PREFIX)) continue;
        const rest = k.slice(MEDIA_PREFIX.length);
        const colon = rest.indexOf(':');
        if (colon < 0) continue;
        refs.push({ id: rest.slice(0, colon), slot: rest.slice(colon + 1) });
      }
    } catch (e) {}
    return refs;
  }
  function positioningBlob() {
    const raw = lsGet(POS_KEY, {}) || {};
    return {
      of: raw.of || {},
      inf: raw.inf || {},
      hands: raw.hands || {},
      lineups: raw.lineups || {},
      _posSeedVer: raw._posSeedVer || null,
    };
  }

  function collectGameDayState() {
    return {
      updated: new Date().toISOString(),
      label: todayLabel(),
      date_key: todayKey(),
      published_from: 'pregame game-day publish',
      note: 'Shared across browsers after publish. Snapshot again ~weekly at roster update.',
      strategy: lsGet(STRATEGY_KEY, {}) || {},
      notes: lsGet(NOTES_KEY, { team: 'CAG', notes: {} }) || { team: 'CAG', notes: {} },
      jersey_overrides: lsGet(JERSEY_KEY, {}) || {},
      one_page_notes: lsGet(ONEPAGE_KEY, {}) || {},
      tth_overrides: lsGet(TTH_KEY, {}) || {},
      of_overrides: lsGet(OF_KEY, {}) || {},
      positioning: positioningBlob(),
      media_refs: mediaRefsFromLocal(),
    };
  }

  function applyGameDayState(doc, { quiet } = {}) {
    if (!doc || typeof doc !== 'object') return false;
    LIVE_STATE = doc;
    if (doc.strategy && typeof doc.strategy === 'object') lsSet(STRATEGY_KEY, doc.strategy);
    if (doc.notes && typeof doc.notes === 'object') lsSet(NOTES_KEY, doc.notes);
    if (doc.jersey_overrides && typeof doc.jersey_overrides === 'object') lsSet(JERSEY_KEY, doc.jersey_overrides);
    if (doc.one_page_notes && typeof doc.one_page_notes === 'object') {
      // Merge (server wins on conflict) so a stale/partial game-day map cannot drop
      // keys the browser already has from the published pitcher_one_page_notes.json hydrate.
      const cur = lsGet(ONEPAGE_KEY, {}) || {};
      const curFlat = (cur && cur.notes && typeof cur.notes === 'object') ? cur.notes : cur;
      const incoming = (doc.one_page_notes.notes && typeof doc.one_page_notes.notes === 'object')
        ? doc.one_page_notes.notes
        : doc.one_page_notes;
      lsSet(ONEPAGE_KEY, Object.assign({}, (curFlat && typeof curFlat === 'object' ? curFlat : {}), incoming || {}));
    }
    if (doc.tth_overrides && typeof doc.tth_overrides === 'object') {
      lsSet(TTH_KEY, doc.tth_overrides);
    }
    if (doc.of_overrides && typeof doc.of_overrides === 'object') {
      lsSet(OF_KEY, doc.of_overrides);
    }
    if (doc.positioning && typeof doc.positioning === 'object') {
      const cur = lsGet(POS_KEY, {}) || {};
      const next = {
        of: Object.assign({}, cur.of || {}, doc.positioning.of || {}),
        inf: Object.assign({}, cur.inf || {}, doc.positioning.inf || {}),
        hands: Object.assign({}, cur.hands || {}, doc.positioning.hands || {}),
        lineups: Object.assign({}, cur.lineups || {}, doc.positioning.lineups || {}),
      };
      if (doc.positioning._posSeedVer) next._posSeedVer = doc.positioning._posSeedVer;
      else if (cur._posSeedVer) next._posSeedVer = cur._posSeedVer;
      if (cur._migratedOfV1) next._migratedOfV1 = cur._migratedOfV1;
      lsSet(POS_KEY, next);
    }
    try {
      if (typeof loadTthOverrides === 'function') loadTthOverrides();
      if (typeof loadOfOverrides === 'function') loadOfOverrides();
    } catch (e) {}
    if (!quiet) {
      try {
        if (window.CardApp && typeof CardApp.refresh === 'function') CardApp.refresh();
        if (typeof renderPitcherOnePage === 'function') renderPitcherOnePage();
        if (typeof renderTth === 'function') renderTth();
        if (typeof renderOfDef === 'function') renderOfDef();
        if (window.PosApp && typeof PosApp.render === 'function') PosApp.render();
        else if (typeof renderOfPos === 'function') renderOfPos();
      } catch (e) {
        console.warn('game-day UI refresh after hydrate', e);
      }
    }
    return true;
  }

  function todayKey() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }
  function todayLabel() {
    const d = new Date();
    return `${d.getMonth() + 1}-${d.getDate()}`;
  }

  async function publishGameDayState(opts) {
    const options = opts || {};
    if (!hasPat()) {
      setStatus('Game-day edits stay in this browser until a GitHub PAT is set (OF Jump / Arm tab).');
      return false;
    }
    if (typeof githubPutJson !== 'function') {
      setStatus('Publish failed: GitHub helper missing');
      return false;
    }
    setStatus('Publishing game-day state to GitHub…');
    try {
      const doc = collectGameDayState();
      await githubPutJson(STATE_PATH, doc, `Game-day state: lineup / notes / overrides (${doc.label})`);
      LIVE_STATE = doc;
      // Keep scout sheets in sync when those override maps changed.
      // skipScout: when a scout sheet publish already triggered this (avoids recursion).
      if (!options.skipScout) {
        try {
          if (typeof publishScoutSheet === 'function') {
            if (Object.keys(doc.tth_overrides || {}).length) await publishScoutSheet('tth');
            if (Object.keys(doc.of_overrides || {}).length) await publishScoutSheet('of');
            if (Object.keys(doc.one_page_notes || {}).length) await publishScoutSheet('onepage');
          }
        } catch (e) {
          console.warn('scout sheet side-publish', e);
        }
      }
      // Positioning seed file — only when coach store has cells (additive merge on disk via full put of current seed+overrides is heavy;
      // store coach blob inside game_day_state; optional full positioning publish via PosApp dump).
      setStatus(`Published game-day state · ${new Date().toLocaleTimeString()} → ${STATE_PATH}`);
      updateGameDayPersistNote();
      return true;
    } catch (err) {
      setStatus(`Publish failed: ${err.message || err}`);
      return false;
    }
  }

  function queueGameDayPublish() {
    clearTimeout(publishTimer);
    publishTimer = setTimeout(() => { publishGameDayState(); }, 1800);
    updateGameDayPersistNote();
  }

  function updateGameDayPersistNote() {
    const el = statusEl();
    if (!el) return;
    const doc = collectGameDayState();
    const teams = Object.keys((doc.strategy && doc.strategy.teams) || {});
    const nJersey = Object.keys(doc.jersey_overrides || {}).length;
    const nTth = Object.keys(doc.tth_overrides || {}).length;
    const nMedia = (doc.media_refs || []).length;
    const bits = [
      teams.length ? `lineup ${teams.join('/')}` : 'no lineup yet',
      nJersey ? `${nJersey} jersey` : null,
      nTth ? `${nTth} TTH` : null,
      nMedia ? `${nMedia} media refs` : null,
    ].filter(Boolean).join(' · ');
    if (hasPat()) {
      el.textContent = `${bits} · auto-publish armed → ${STATE_PATH}`;
    } else {
      el.textContent = `${bits} · browser-only until PAT set on OF Jump / Arm (shared).`;
    }
  }

  async function fetchJson(path) {
    const res = await fetch(`${path}?v=${encodeURIComponent(assetV())}`);
    if (!res.ok) throw new Error(`GET ${path} failed (${res.status})`);
    return res.json();
  }

  async function loadVersionsIndex() {
    try {
      VERSIONS = await fetchJson(VERSIONS_INDEX);
    } catch (e) {
      VERSIONS = { versions: [] };
    }
    return VERSIONS;
  }

  function fillRevertDropdown() {
    const sel = document.getElementById('revert-date');
    if (!sel) return;
    const list = (VERSIONS && VERSIONS.versions) || [];
    const cur = sel.value;
    sel.innerHTML = list.length
      ? list.map((v) => {
          const label = v.label ? `${v.label} (${v.date_key})` : v.date_key;
          return `<option value="${esc(v.date_key)}">${esc(label)}</option>`;
        }).join('')
      : '<option value="">No snapshots yet</option>';
    if (cur && [...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function revertConfirmReady() {
    const a = document.getElementById('revert-yes-1');
    const b = document.getElementById('revert-yes-2');
    const c = document.getElementById('revert-yes-3');
    return !!(a && a.checked && b && b.checked && c && c.checked);
  }
  function updateRevertButton() {
    const btn = document.getElementById('revert-run');
    if (!btn) return;
    const dateOk = !!(document.getElementById('revert-date') || {}).value;
    btn.disabled = !(dateOk && revertConfirmReady() && hasPat());
  }

  async function githubGetJson(path) {
    const token = scoutGhPat();
    if (!token) throw new Error('No GitHub PAT set');
    const repo = (typeof SCOUT_GH_REPO !== 'undefined' && SCOUT_GH_REPO) || 'colbymorris08/Santurce2026';
    const branch = (typeof SCOUT_GH_BRANCH !== 'undefined' && SCOUT_GH_BRANCH) || 'main';
    const api = `https://api.github.com/repos/${repo}/contents/${path}?ref=${encodeURIComponent(branch)}`;
    const headers = {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'X-GitHub-Api-Version': '2022-11-28',
    };
    const res = await fetch(api, { headers });
    if (!res.ok) throw new Error(`GET ${path} failed (${res.status})`);
    const meta = await res.json();
    const raw = decodeURIComponent(escape(atob(meta.content.replace(/\n/g, ''))));
    return JSON.parse(raw);
  }

  async function createTodaySnapshot() {
    if (!hasPat() || typeof githubPutJson !== 'function') {
      setRevertStatus('Set a GitHub PAT on the OF Jump / Arm tab first.');
      return false;
    }
    const dateKey = todayKey();
    const label = todayLabel();
    setRevertStatus(`Creating snapshot ${label} (${dateKey})…`);
    try {
      // Ensure live game-day file is current first.
      const doc = collectGameDayState();
      doc.date_key = dateKey;
      doc.label = label;
      await githubPutJson(STATE_PATH, doc, `Game-day state before snapshot ${label}`);

      const files = [];
      for (const name of SNAPSHOT_FILES) {
        const livePath = `data/${name}`;
        let body;
        try {
          body = await fetchJson(livePath);
        } catch (e) {
          if (name === 'game_day_state.json') body = doc;
          else continue;
        }
        if (name === 'game_day_state.json') body = doc;
        const snapPath = `data/versions/${dateKey}/${name}`;
        await githubPutJson(snapPath, body, `Snapshot ${label}: ${name}`);
        files.push(name);
      }
      const manifest = {
        date_key: dateKey,
        label,
        created: new Date().toISOString(),
        note: 'Created from Revert tab. Snapshot again ~weekly at roster update.',
        files,
        restore_targets: Object.fromEntries(files.map((f) => [f, `data/${f}`])),
      };
      await githubPutJson(`data/versions/${dateKey}/manifest.json`, manifest, `Snapshot ${label}: manifest`);

      let index;
      try { index = await githubGetJson(VERSIONS_INDEX); } catch (e) { index = { versions: [] }; }
      if (!index.versions) index.versions = [];
      index.versions = index.versions.filter((v) => v.date_key !== dateKey);
      index.versions.unshift({
        date_key: dateKey,
        label,
        created: manifest.created,
        path: `data/versions/${dateKey}`,
      });
      index.updated = manifest.created;
      index.note = 'Daily version snapshots for Revert tab. Additive — prior dates are kept.';
      await githubPutJson(VERSIONS_INDEX, index, `Snapshot index: add ${label}`);
      VERSIONS = index;
      fillRevertDropdown();
      setRevertStatus(`Snapshot ${label} saved under data/versions/${dateKey}/`);
      return true;
    } catch (err) {
      setRevertStatus(`Snapshot failed: ${err.message || err}`);
      return false;
    }
  }

  async function revertToSelected() {
    if (!revertConfirmReady()) {
      setRevertStatus('Check all three Yes boxes before reverting.');
      return;
    }
    if (!hasPat()) {
      setRevertStatus('Set a GitHub PAT on the OF Jump / Arm tab first.');
      return;
    }
    const dateKey = (document.getElementById('revert-date') || {}).value;
    if (!dateKey) {
      setRevertStatus('Pick a saved date.');
      return;
    }
    const btn = document.getElementById('revert-run');
    if (btn) btn.disabled = true;
    setRevertStatus(`Reverting live site to ${dateKey}…`);
    try {
      let manifest;
      try {
        manifest = await githubGetJson(`data/versions/${dateKey}/manifest.json`);
      } catch (e) {
        manifest = {
          files: SNAPSHOT_FILES.slice(),
          restore_targets: Object.fromEntries(SNAPSHOT_FILES.map((f) => [f, `data/${f}`])),
        };
      }
      const files = manifest.files || SNAPSHOT_FILES;
      const targets = manifest.restore_targets || {};
      for (const name of files) {
        const snapPath = `data/versions/${dateKey}/${name}`;
        const livePath = targets[name] || `data/${name}`;
        const body = await githubGetJson(snapPath);
        await githubPutJson(livePath, body, `Revert live ${name} ← ${dateKey}`);
        if (name === 'game_day_state.json') applyGameDayState(body);
      }
      // Clear confirmations so a second click is intentional.
      ['revert-yes-1', 'revert-yes-2', 'revert-yes-3'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.checked = false;
      });
      updateRevertButton();
      setRevertStatus(`Reverted to ${dateKey}. Hard-refresh other browsers after GitHub Pages updates (~30s).`);
      try {
        if (typeof loadTthOverrides === 'function') loadTthOverrides();
        if (typeof loadOfOverrides === 'function') loadOfOverrides();
        if (typeof updateTthPersistNote === 'function') updateTthPersistNote();
        if (typeof updateOfPersistNote === 'function') updateOfPersistNote();
        if (window.CardApp && typeof CardApp.refresh === 'function') CardApp.refresh();
        if (typeof renderTth === 'function') renderTth();
        if (typeof renderOfDef === 'function') renderOfDef();
        if (typeof renderPitcherOnePage === 'function') renderPitcherOnePage();
      } catch (e) {}
      return true;
    } catch (err) {
      setRevertStatus(`Revert failed: ${err.message || err}`);
      updateRevertButton();
      return false;
    }
  }

  async function bootPersist(seedDoc) {
    if (seedDoc) applyGameDayState(seedDoc, { quiet: true });
    else {
      try {
        const doc = await fetchJson(STATE_PATH);
        applyGameDayState(doc, { quiet: true });
      } catch (e) {
        /* first boot without published state is fine */
      }
    }
    await loadVersionsIndex();
    fillRevertDropdown();
    updateGameDayPersistNote();
    updateRevertButton();
    const patInput = document.getElementById('scout-gh-pat');
    if (patInput) {
      patInput.addEventListener('change', () => { updateGameDayPersistNote(); updateRevertButton(); });
      patInput.addEventListener('blur', () => { updateGameDayPersistNote(); updateRevertButton(); });
    }
    ['revert-yes-1', 'revert-yes-2', 'revert-yes-3'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', updateRevertButton);
    });
    const sel = document.getElementById('revert-date');
    if (sel) sel.addEventListener('change', updateRevertButton);
  }

  window.GameDayPersist = {
    collect: collectGameDayState,
    apply: applyGameDayState,
    publish: publishGameDayState,
    queuePublish: queueGameDayPublish,
    createTodaySnapshot,
    revertToSelected,
    boot: bootPersist,
    updateNote: updateGameDayPersistNote,
    updateRevertButton,
    loadVersionsIndex,
    fillRevertDropdown,
    live: () => LIVE_STATE,
  };
  window.queueGameDayPublish = queueGameDayPublish;
  window.publishGameDayNow = (opts) => publishGameDayState(opts);
  window.createGameDaySnapshotNow = () => createTodaySnapshot();
  window.revertGameDayNow = () => revertToSelected();
  window.onRevertConfirmChange = updateRevertButton;
})();

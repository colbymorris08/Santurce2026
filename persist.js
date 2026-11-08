/* Game-day persistence: publish browser state to GitHub + daily version snapshots.
   Live file: data/game_day_state.json — source of truth for published daily lineups.
   Versions: data/versions/index.json + data/versions/YYYY-MM-DD/
   Uses the same PAT as OF/TTH (santurce_gh_pat_v1 → scoutGhPat / githubPutJson).
   Hydrate: committed JSON is source of truth for SAN and today’s opponent.
   Empty localStorage cannot wipe a published 1–9, and cannot reseed stale
   non-daily opponents (e.g. MAY) from JSON. Other filled browser sides
   (an old Ponce card, etc.) stay local only.
   Publish: PAT required; must PUT/commit data/game_day_state.json (no silent
   local-only success). Empty local teams never replace a filled GitHub card.
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
  let LAST_PUBLISH = null;

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

  function isMap(v) {
    return !!(v && typeof v === 'object' && !Array.isArray(v));
  }
  function isoTime(s) {
    const t = Date.parse(s || '');
    return Number.isFinite(t) ? t : 0;
  }
  function filledRows(list) {
    return (list || []).filter((r) => r && r.id != null && r.id !== '');
  }
  function lineupCount(st) {
    return filledRows((st && st.lineup) || []).length;
  }
  function hasFullLineup(st) {
    return lineupCount(st) >= 9;
  }
  function cloneTeam(st) {
    if (!isMap(st)) return { sp: null, pen: [], lineup: [], bench: [] };
    return {
      sp: st.sp != null && st.sp !== '' ? st.sp : null,
      pen: Array.isArray(st.pen) ? st.pen.slice() : [],
      lineup: Array.isArray(st.lineup) ? st.lineup.slice() : [],
      bench: Array.isArray(st.bench) ? st.bench.slice() : [],
    };
  }
  function teamFilled(st) {
    const t = cloneTeam(st);
    return (t.sp != null ? 2 : 0) + t.pen.filter(Boolean).length
      + filledRows(t.lineup).length * 3 + filledRows(t.bench).length;
  }
  function fillEmptyTeamFields(out, fallback) {
    if (!isMap(out) || !isMap(fallback)) return out;
    if (out.sp == null && fallback.sp != null) out.sp = fallback.sp;
    if (!out.pen.length && fallback.pen.length) out.pen = fallback.pen.slice();
    if (!filledRows(out.lineup).length && filledRows(fallback.lineup).length) {
      out.lineup = fallback.lineup.slice();
    }
    if (!filledRows(out.bench).length && filledRows(fallback.bench).length) {
      out.bench = fallback.bench.slice();
    }
    return out;
  }
  function stripPitchersFromBench(st) {
    const t = cloneTeam(st);
    const blocked = new Set((t.pen || []).map(Number).filter(Boolean));
    if (t.sp != null && t.sp !== '') blocked.add(Number(t.sp));
    t.bench = (t.bench || []).filter((r) => {
      if (!r || r.id == null || r.id === '') return false;
      if (blocked.has(Number(r.id))) return false;
      // Import bug: unused arms were saved on the bench with pos 1 / P.
      if (String(r.pos) === '1' || String(r.pos).toUpperCase() === 'P') return false;
      return true;
    });
    return t;
  }
  function pickTeamPreferPrimary(primary, secondary) {
    const a = cloneTeam(primary);
    const b = cloneTeam(secondary);
    if (hasFullLineup(primary)) return stripPitchersFromBench(fillEmptyTeamFields(a, b));
    if (hasFullLineup(secondary)) return stripPitchersFromBench(fillEmptyTeamFields(b, a));
    if (teamFilled(primary) > 0) return stripPitchersFromBench(fillEmptyTeamFields(a, b));
    if (teamFilled(secondary) > 0) return stripPitchersFromBench(fillEmptyTeamFields(b, a));
    return stripPitchersFromBench(fillEmptyTeamFields(a, b));
  }
  function mergeTeamBlock(local, incoming, { preferIncoming, localExists, incomingExists }) {
    const loc = cloneTeam(local);
    const inc = cloneTeam(incoming);
    const locHasTeam = !!localExists && teamFilled(local) > 0;
    const incHasTeam = !!incomingExists && teamFilled(incoming) > 0;
    let out;
    if (locHasTeam && !incHasTeam) out = loc;
    else if (!locHasTeam && incHasTeam) out = inc;
    else if (!locHasTeam && !incHasTeam) out = cloneTeam(null);
    else {
      out = cloneTeam(preferIncoming ? inc : loc);
      fillEmptyTeamFields(out, preferIncoming ? loc : inc);
    }
    // A committed full 1–9 always beats an empty lineup, even when the empty
    // side has a newer localStorage timestamp (hard-refresh after a blank SAN).
    if (hasFullLineup(inc) && !filledRows(out.lineup).length) out.lineup = inc.lineup.slice();
    if (hasFullLineup(loc) && !filledRows(out.lineup).length) out.lineup = loc.lineup.slice();
    fillEmptyTeamFields(out, inc);
    fillEmptyTeamFields(out, loc);
    return stripPitchersFromBench(out);
  }
  function teamMap(strategy) {
    const teams = strategy && strategy.teams;
    return isMap(teams) ? teams : {};
  }
  function mergeStrategy(local, incoming, { forceIncoming, hydrate } = {}) {
    const a = isMap(local) ? local : {};
    const b = isMap(incoming) ? incoming : {};
    const aTeams = teamMap(a);
    const bTeams = teamMap(b);
    const aT = isoTime(a.updated);
    const bT = isoTime(b.updated);
    const preferIncoming = !!forceIncoming || (bT > aT);
    const outTeams = {};
    const keys = new Set([...Object.keys(aTeams), ...Object.keys(bTeams)]);
    const jsonOpp = String((hydrate ? b.opponent : '') || '').toUpperCase();
    keys.forEach((abbr) => {
      if (hydrate) {
        // incoming = published JSON. JSON wins for SAN + today's opponent so
        // an empty/stale localStorage cannot hide the daily card.
        const daily = abbr === 'SAN' || (jsonOpp && abbr === jsonOpp);
        if (!daily) {
          // Keep a filled local side (old Ponce card, etc.) but never seed
          // yesterday's opponent from committed JSON into an empty browser —
          // that resurrected MAY over CAR after a cleared localStorage.
          if (teamFilled(aTeams[abbr]) > 0) {
            outTeams[abbr] = pickTeamPreferPrimary(aTeams[abbr], bTeams[abbr]);
          }
          return;
        }
        outTeams[abbr] = pickTeamPreferPrimary(bTeams[abbr], aTeams[abbr]);
        return;
      }
      outTeams[abbr] = mergeTeamBlock(aTeams[abbr], bTeams[abbr], {
        preferIncoming,
        localExists: Object.prototype.hasOwnProperty.call(aTeams, abbr),
        incomingExists: Object.prototype.hasOwnProperty.call(bTeams, abbr),
      });
    });
    const incomingHasGame = hasFullLineup(bTeams.SAN) && !!b.opponent;
    const localSanEmpty = !filledRows((aTeams.SAN && aTeams.SAN.lineup) || []).length;
    const takeIncomingMeta = hydrate
      ? incomingHasGame
      : (incomingHasGame && localSanEmpty) || preferIncoming;
    const newer = takeIncomingMeta ? b : (preferIncoming ? b : a);
    const older = takeIncomingMeta ? a : (preferIncoming ? a : b);
    return {
      teams: outTeams,
      opponent: newer.opponent || older.opponent || 'CAG',
      santurceHome: typeof newer.santurceHome === 'boolean'
        ? newer.santurceHome
        : (typeof older.santurceHome === 'boolean' ? older.santurceHome : true),
      updated: (bT >= aT ? b.updated : a.updated) || b.updated || a.updated || null,
    };
  }
  function mergeNotes(local, incoming) {
    const a = isMap(local) ? local : {};
    const b = isMap(incoming) ? incoming : {};
    const aNotes = isMap(a.notes) ? a.notes : (isMap(a) && !a.notes ? {} : {});
    const bNotes = isMap(b.notes) ? b.notes : {};
    const notes = Object.assign({}, aNotes);
    Object.entries(bNotes).forEach(([k, v]) => {
      if (v != null && String(v).trim() !== '') notes[k] = v;
    });
    return { team: b.team || a.team || 'CAG', notes };
  }
  function mergeOverrideMaps(base, overlay) {
    const merged = Object.assign({}, isMap(base) ? base : {});
    if (!isMap(overlay)) return merged;
    Object.entries(overlay).forEach(([id, fields]) => {
      if (!isMap(fields)) return;
      const row = Object.assign({}, merged[id] || {});
      Object.entries(fields).forEach(([field, raw]) => {
        if (raw == null || String(raw).trim() === '') return;
        row[field] = raw;
      });
      if (Object.keys(row).length) merged[id] = row;
    });
    return merged;
  }
  function flattenNotesMap(raw) {
    if (!isMap(raw)) return {};
    return isMap(raw.notes) ? raw.notes : raw;
  }
  function mergePositioningMaps(cur, incoming) {
    const next = {
      of: Object.assign({}, (cur && cur.of) || {}, (incoming && incoming.of) || {}),
      inf: Object.assign({}, (cur && cur.inf) || {}, (incoming && incoming.inf) || {}),
      hands: Object.assign({}, (cur && cur.hands) || {}, (incoming && incoming.hands) || {}),
      lineups: Object.assign({}, (cur && cur.lineups) || {}, (incoming && incoming.lineups) || {}),
    };
    if (incoming && incoming._posSeedVer) next._posSeedVer = incoming._posSeedVer;
    else if (cur && cur._posSeedVer) next._posSeedVer = cur._posSeedVer;
    if (cur && cur._migratedOfV1) next._migratedOfV1 = cur._migratedOfV1;
    return next;
  }
  function unionMediaRefs(a, b) {
    const map = new Map();
    [...(Array.isArray(a) ? a : []), ...(Array.isArray(b) ? b : [])].forEach((r) => {
      if (!r || r.id == null || r.slot == null || r.slot === '') return;
      map.set(`${r.id}:${r.slot}`, { id: r.id, slot: r.slot });
    });
    return [...map.values()];
  }
  function mergeGameDayDocs(remote, local, { strategyForceLocal } = {}) {
    const base = isMap(remote) ? remote : {};
    const over = isMap(local) ? local : {};
    const strategy = mergeStrategy(base.strategy, over.strategy, {
      forceIncoming: strategyForceLocal !== false,
    });
    // Never PUT an empty 1–9 over a committed full card (SAN, MAY, or any side).
    const remoteTeams = teamMap(base.strategy);
    Object.keys(remoteTeams).forEach((abbr) => {
      const remote = remoteTeams[abbr];
      const next = strategy.teams[abbr];
      if (hasFullLineup(remote) && !hasFullLineup(next)) {
        strategy.teams[abbr] = mergeTeamBlock(remote, next, {
          preferIncoming: false,
          localExists: true,
          incomingExists: !!next && teamFilled(next) > 0,
        });
      }
    });
    strategy.updated = over.updated || strategy.updated || new Date().toISOString();
    return {
      updated: over.updated || new Date().toISOString(),
      label: over.label || base.label || todayLabel(),
      date_key: over.date_key || base.date_key || todayKey(),
      published_from: over.published_from || 'pregame game-day publish',
      note: base.note || over.note || 'Shared across browsers after publish. Snapshot again ~weekly at roster update.',
      strategy,
      notes: mergeNotes(base.notes, over.notes),
      jersey_overrides: Object.assign({}, isMap(base.jersey_overrides) ? base.jersey_overrides : {}, isMap(over.jersey_overrides) ? over.jersey_overrides : {}),
      one_page_notes: Object.assign({}, flattenNotesMap(base.one_page_notes), flattenNotesMap(over.one_page_notes)),
      tth_overrides: mergeOverrideMaps(base.tth_overrides, over.tth_overrides),
      of_overrides: mergeOverrideMaps(base.of_overrides, over.of_overrides),
      positioning: mergePositioningMaps(base.positioning, over.positioning),
      media_refs: unionMediaRefs(base.media_refs, over.media_refs),
    };
  }
  function strategyHasLineup(strategy) {
    const teams = teamMap(strategy);
    return Object.keys(teams).some((abbr) => teamFilled(teams[abbr]) > 0);
  }
  function downloadGameDayBackup(reason) {
    try {
      const payload = collectGameDayState();
      payload.meta = { exported_at: new Date().toISOString(), reason: reason || 'backup' };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'santurce_game_day_state.json';
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      console.warn('game-day backup download failed', e);
    }
  }

  function collectGameDayState() {
    const strategy = lsGet(STRATEGY_KEY, {}) || {};
    if (isMap(strategy) && !strategy.updated) strategy.updated = new Date().toISOString();
    return {
      updated: new Date().toISOString(),
      label: todayLabel(),
      date_key: todayKey(),
      published_from: 'pregame game-day publish',
      note: 'Shared across browsers after publish. Snapshot again ~weekly at roster update.',
      strategy,
      notes: lsGet(NOTES_KEY, { team: 'CAG', notes: {} }) || { team: 'CAG', notes: {} },
      jersey_overrides: lsGet(JERSEY_KEY, {}) || {},
      one_page_notes: lsGet(ONEPAGE_KEY, {}) || {},
      tth_overrides: lsGet(TTH_KEY, {}) || {},
      of_overrides: lsGet(OF_KEY, {}) || {},
      positioning: positioningBlob(),
      media_refs: mediaRefsFromLocal(),
    };
  }

  function applyGameDayState(doc, { quiet, replace } = {}) {
    if (!doc || typeof doc !== 'object') return false;
    LIVE_STATE = doc;
    if (replace) {
      if (doc.strategy && typeof doc.strategy === 'object') lsSet(STRATEGY_KEY, doc.strategy);
      if (doc.notes && typeof doc.notes === 'object') lsSet(NOTES_KEY, doc.notes);
      if (doc.jersey_overrides && typeof doc.jersey_overrides === 'object') lsSet(JERSEY_KEY, doc.jersey_overrides);
      if (doc.one_page_notes && typeof doc.one_page_notes === 'object') lsSet(ONEPAGE_KEY, flattenNotesMap(doc.one_page_notes));
      if (doc.tth_overrides && typeof doc.tth_overrides === 'object') lsSet(TTH_KEY, doc.tth_overrides);
      if (doc.of_overrides && typeof doc.of_overrides === 'object') lsSet(OF_KEY, doc.of_overrides);
      if (doc.positioning && typeof doc.positioning === 'object') lsSet(POS_KEY, mergePositioningMaps({}, doc.positioning));
    } else {
      if (doc.strategy && typeof doc.strategy === 'object') {
        // Merge (do not replace) so a stale Pages copy of game_day_state.json cannot
        // wipe a lineup the coach just saved in this browser (same class as TTH).
        // Stamp the file clock onto strategy when the blob omitted it — otherwise a
        // newer empty localStorage always beats the committed SAN vs PON 1–9.
        const cur = lsGet(STRATEGY_KEY, {}) || {};
        const incoming = Object.assign({}, doc.strategy);
        if (!incoming.updated && doc.updated) incoming.updated = doc.updated;
        lsSet(STRATEGY_KEY, mergeStrategy(cur, incoming, { hydrate: true }));
      }
      if (doc.notes && typeof doc.notes === 'object') {
        lsSet(NOTES_KEY, mergeNotes(lsGet(NOTES_KEY, { team: 'CAG', notes: {} }), doc.notes));
      }
      if (doc.jersey_overrides && typeof doc.jersey_overrides === 'object') {
        lsSet(JERSEY_KEY, Object.assign({}, lsGet(JERSEY_KEY, {}) || {}, doc.jersey_overrides));
      }
      if (doc.one_page_notes && typeof doc.one_page_notes === 'object') {
        // Merge (server wins on conflict) so a stale/partial game-day map cannot drop
        // keys the browser already has from the published pitcher_one_page_notes.json hydrate.
        const cur = lsGet(ONEPAGE_KEY, {}) || {};
        const curFlat = flattenNotesMap(cur);
        const incoming = flattenNotesMap(doc.one_page_notes);
        lsSet(ONEPAGE_KEY, Object.assign({}, curFlat, incoming || {}));
      }
      // Merge (do not replace) so a published/partial game-day map cannot wipe
      // browser-only TTH/OF keys the coach typed before publish (e.g. new pitchers).
      if (doc.tth_overrides && typeof doc.tth_overrides === 'object') {
        lsSet(TTH_KEY, mergeOverrideMaps(lsGet(TTH_KEY, {}) || {}, doc.tth_overrides));
      }
      if (doc.of_overrides && typeof doc.of_overrides === 'object') {
        lsSet(OF_KEY, mergeOverrideMaps(lsGet(OF_KEY, {}) || {}, doc.of_overrides));
      }
      if (doc.positioning && typeof doc.positioning === 'object') {
        lsSet(POS_KEY, mergePositioningMaps(lsGet(POS_KEY, {}) || {}, doc.positioning));
      }
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

  async function loadRemoteGameDayState() {
    if (hasPat()) {
      try {
        return await githubGetJson(STATE_PATH);
      } catch (e) {
        /* 404 on first publish is fine; fall back to in-memory/pages copy */
      }
    }
    return LIVE_STATE;
  }

  function putCommitSha(put) {
    if (!put || typeof put !== 'object') return '';
    return (put && put._commitSha)
      || (put.commit && put.commit.sha)
      || (put.content && put.content.sha)
      || '';
  }

  async function publishGameDayState(opts) {
    const options = opts || {};
    const quiet = !!options.quiet || !!options.skipScout;
    const local = collectGameDayState();
    if (!hasPat()) {
      LAST_PUBLISH = { ok: false, reason: 'no-pat', at: new Date().toISOString() };
      const nTeams = Object.keys(teamMap(local.strategy)).filter((k) => teamFilled(teamMap(local.strategy)[k]) > 0);
      const msg = nTeams.length
        ? `Publish blocked: no GitHub PAT. Lineup for ${nTeams.join('/')} is only in this browser — it was NOT written to ${STATE_PATH}. Paste a PAT with repo Contents:write on the OF Jump / Arm tab, then click Publish lineup again.`
        : `Publish blocked: no GitHub PAT. Nothing was written to ${STATE_PATH}. Paste a PAT with repo Contents:write on the OF Jump / Arm tab, then click Publish lineup again.`;
      setStatus(msg);
      if (!quiet) {
        downloadGameDayBackup('publish-blocked-no-pat');
        if (typeof focusScoutPatInput === 'function') focusScoutPatInput();
        alert(`${msg}\n\nA backup JSON of this browser’s game-day state was downloaded so the lineup is not lost.`);
      }
      return false;
    }
    if (typeof githubPutJson !== 'function') {
      LAST_PUBLISH = { ok: false, reason: 'no-helper', at: new Date().toISOString() };
      const msg = `Publish failed: GitHub helper missing. ${STATE_PATH} was not updated.`;
      setStatus(msg);
      if (!quiet) {
        downloadGameDayBackup('publish-failed-no-helper');
        alert(msg);
      }
      return false;
    }
    setStatus(`Publishing ${STATE_PATH} to GitHub (PUT)…`);
    try {
      const remote = await loadRemoteGameDayState();
      // Overlay this browser’s lineup onto the live GitHub file. Empty local
      // TTH/notes/pics/positioning maps never replace filled remote keys.
      const doc = mergeGameDayDocs(remote, local, { strategyForceLocal: true });
      if (!strategyHasLineup(doc.strategy) && strategyHasLineup(local.strategy)) {
        doc.strategy = local.strategy;
      }
      const put = await githubPutJson(STATE_PATH, doc, `Game-day state: lineup / notes / overrides (${doc.label})`);
      const sha = putCommitSha(put);
      if (!sha) {
        throw new Error(`GitHub PUT did not commit ${STATE_PATH}. Lineup is still only in this browser.`);
      }
      LIVE_STATE = doc;
      applyGameDayState(doc, { quiet: true });
      // Keep scout sheets in sync when those override maps changed.
      // skipScout: when a scout sheet publish already triggered this (avoids recursion).
      if (!options.skipScout) {
        try {
          if (typeof publishScoutSheet === 'function') {
            if (Object.keys(doc.tth_overrides || {}).length) await publishScoutSheet('tth', { quiet: true });
            if (Object.keys(doc.of_overrides || {}).length) await publishScoutSheet('of', { quiet: true });
            if (Object.keys(doc.one_page_notes || {}).length) await publishScoutSheet('onepage', { quiet: true });
          }
        } catch (e) {
          console.warn('scout sheet side-publish', e);
        }
      }
      const teams = Object.keys(teamMap(doc.strategy)).filter((k) => teamFilled(teamMap(doc.strategy)[k]) > 0);
      LAST_PUBLISH = {
        ok: true,
        sha,
        teams,
        path: STATE_PATH,
        at: new Date().toISOString(),
      };
      setStatus(`Published ${STATE_PATH} to GitHub · ${sha.slice(0, 7)} · ${teams.join('/') || 'no teams'} · ${new Date().toLocaleTimeString()}`);
      return true;
    } catch (err) {
      LAST_PUBLISH = { ok: false, reason: String(err.message || err), at: new Date().toISOString() };
      const fail = `Publish failed — ${STATE_PATH} was NOT updated: ${err.message || err}`;
      setStatus(fail);
      if (!quiet) {
        downloadGameDayBackup('publish-failed');
        alert(`${fail}\n\nA backup JSON of this browser’s game-day state was downloaded. Keep that file — fix the PAT if needed and retry Publish lineup.`);
      }
      return false;
    }
  }

  function queueGameDayPublish() {
    clearTimeout(publishTimer);
    publishTimer = setTimeout(() => { publishGameDayState({ quiet: true }); }, 1800);
    updateGameDayPersistNote();
  }

  function updateGameDayPersistNote() {
    const el = statusEl();
    if (!el) return;
    if (LAST_PUBLISH && LAST_PUBLISH.ok && LAST_PUBLISH.sha) {
      el.textContent = `On GitHub: ${STATE_PATH} · ${LAST_PUBLISH.sha.slice(0, 7)} · ${(LAST_PUBLISH.teams || []).join('/') || 'lineup'} · ${new Date(LAST_PUBLISH.at).toLocaleTimeString()}`;
      return;
    }
    const doc = collectGameDayState();
    const teams = Object.keys(teamMap(doc.strategy)).filter((k) => teamFilled(teamMap(doc.strategy)[k]) > 0);
    const bits = teams.length ? `browser lineup ${teams.join('/')}` : 'no lineup in this browser';
    if (LAST_PUBLISH && LAST_PUBLISH.ok === false) {
      el.textContent = `${bits} · last publish did NOT write ${STATE_PATH} (${LAST_PUBLISH.reason || 'failed'})`;
      return;
    }
    if (hasPat()) {
      el.textContent = `${bits} · PAT set — click Publish lineup to PUT ${STATE_PATH} on GitHub`;
    } else {
      el.textContent = `${bits} · browser-only until a PAT is set on OF Jump / Arm, then Publish lineup writes ${STATE_PATH}`;
    }
  }

  async function fetchJson(path) {
    const live = path === STATE_PATH;
    const url = `${path}?v=${encodeURIComponent(assetV())}${live ? `&t=${Date.now()}` : ''}`;
    const res = await fetch(url, live ? { cache: 'no-store' } : undefined);
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
      // Ensure live game-day file is current first (merge so snapshot cannot drop TTH/notes/pics).
      const local = collectGameDayState();
      const remote = await loadRemoteGameDayState();
      const doc = mergeGameDayDocs(remote, local, { strategyForceLocal: true });
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
        if (name === 'game_day_state.json') applyGameDayState(body, { replace: true });
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
    let doc = seedDoc || null;
    if (!doc) {
      try {
        doc = await fetchJson(STATE_PATH);
      } catch (e) {
        /* first boot without published state is fine */
      }
    }
    // PAT copy is the live GitHub file (not the cached Pages asset). Prefer it
    // when newer so a just-published lineup hydrates even before Pages rebuilds.
    if (hasPat()) {
      try {
        const remote = await githubGetJson(STATE_PATH);
        if (remote && (!doc || isoTime(remote.updated) >= isoTime(doc.updated))) doc = remote;
      } catch (e) {}
    }
    if (doc) applyGameDayState(doc, { quiet: true });
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
    mergeStrategy,
    mergeGameDayDocs,
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

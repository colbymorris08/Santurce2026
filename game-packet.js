/* Game Packet — one top-bar download for today's opposing sheets.
   Excels land in the ZIP immediately. PDFs need two Save-as-PDF steps
   (portrait + landscape) because browser print can't feed a ZIP silently.
   Pitcher media / arm-slot ZIP stays separate on purpose. */
(function (root) {
  'use strict';

  const POS_PRINT_CSS = `
    h1{font-size:16px;margin:0 0 8px;letter-spacing:.04em}
    h2{font-size:13px;margin:12px 0 6px;background:#111;color:#fff;padding:4px 8px;letter-spacing:.08em}
    .pos-print-grid{display:grid;gap:14px}
    .pos-print-3{grid-template-columns:1fr 1fr 1fr}
    .pos-print-2{grid-template-columns:1fr 1fr}
    table.pos-card{width:100%;border-collapse:collapse;font-size:11px;border:2.5px solid #000;background:#fff}
    table.pos-card th,table.pos-card td{border:1px solid #999;padding:3px 5px;text-align:center}
    table.pos-card th{font-size:10px;letter-spacing:.04em}
    table.pos-card td.name{text-align:left;font-weight:700;width:34%}
    table.pos-card .pos-title{font-weight:800;background:#5B9BD5;color:#111;text-align:left}
    table.pos-card .pos-labels th{background:#D9D9D9;font-weight:700;color:#111}
    table.pos-card .pos-bench{background:#FCE4D6}
    .sc-lhb{color:#C0392B!important}
    .sc-shb{color:#1A5276!important}
    .gp-section{page-break-before:always;break-before:page}
    .gp-section:first-child{page-break-before:auto;break-before:auto}
    @media print {
      h2,table.pos-card .pos-title,table.pos-card .pos-labels th,
      table.pos-card .pos-bench,.sc-lhb,.sc-shb{
        -webkit-print-color-adjust:exact;print-color-adjust:exact
      }
    }
  `;

  function strategy() {
    return (root.CACHE && CACHE.gameDayState && CACHE.gameDayState.strategy)
      || (root.GameDayPersist && GameDayPersist.live && GameDayPersist.live() && GameDayPersist.live().strategy)
      || null;
  }

  function opponentTeam() {
    const s = strategy();
    const opp = s && s.opponent;
    if (opp) return String(opp).toUpperCase();
    const fromUi = document.getElementById('adv-team')?.value
      || document.getElementById('bullpen-team')?.value
      || document.getElementById('onepage-team')?.value;
    if (fromUi && fromUi !== 'all' && fromUi !== 'SAN') return String(fromUi).toUpperCase();
    return 'CAG';
  }

  function dateKey() {
    const g = (root.CACHE && CACHE.gameDayState) || (root.GameDayPersist && GameDayPersist.live && GameDayPersist.live()) || {};
    return g.date_key || new Date().toISOString().slice(0, 10);
  }

  function setSelect(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = value;
    el.dispatchEvent(new Event('change'));
  }

  function checkAll(selector, predicate) {
    document.querySelectorAll(selector).forEach((cb) => {
      cb.checked = predicate ? !!predicate(cb) : true;
    });
  }

  function prepareFilters(team) {
    setSelect('adv-p-team', team);
    if (typeof fillAdvancePitchers === 'function') fillAdvancePitchers();
    // Prefer today's card SP/pen when set, but mass export still gates to official actives.
    const cardIds = new Set((onePageCardPitcherIndex(team).ids || []).map(String));
    const officialIds = new Set(
      (typeof officialActivePitcherIndex === 'function'
        ? (officialActivePitcherIndex(team).ids || [])
        : []
      ).map(String)
    );
    checkAll('#adv-p-export-list input[type=checkbox]', (cb) => {
      const id = String(cb.value);
      if (officialIds.size && !officialIds.has(id)) return false;
      if (cardIds.size) return cardIds.has(id);
      return !officialIds.size || officialIds.has(id);
    });

    setSelect('adv-team', team);
    if (typeof fillAdvanceHitters === 'function') fillAdvanceHitters();
    checkAll('#adv-h-export-list input[type=checkbox]', () => true);

    setSelect('spray-team', team);
    if (typeof onSprayTeamChange === 'function') onSprayTeamChange();
    // Check game-day hitters (lineup + bench) when strategy ids resolve; else all filtered.
    const want = new Set(typeof strategyActiveHitterIdOrder === 'function' ? strategyActiveHitterIdOrder(team) : []);
    const pool = typeof filteredSprayPlayers === 'function' ? filteredSprayPlayers() : [];
    const keys = new Set(
      pool
        .filter((p) => !want.size || (typeof playerStrategyId === 'function' && want.has(playerStrategyId(p))))
        .map((p) => sprayPlayerKey(p))
    );
    checkAll('#spray-export-list input[type=checkbox]', (cb) => !keys.size || keys.has(cb.value));

    setSelect('bullpen-team', team);
    if (typeof renderBullpen === 'function') renderBullpen();

    setSelect('onepage-team', team);
    if (typeof renderPitcherOnePage === 'function') renderPitcherOnePage();

    setSelect('ofpos-team', team);
    if (root.PosApp && typeof PosApp.onTeamChange === 'function') PosApp.onTeamChange();
    else if (typeof onOfPosTeamChange === 'function') onOfPosTeamChange();

    if (root.CardApp && typeof CardApp.setSbTeam === 'function') CardApp.setSbTeam(team);
  }

  function sortPitchersCardOrder(sheets, team) {
    const index = onePageCardPitcherIndex(team);
    if (!index.ids.length) return sheets;
    return sheets.slice().sort((a, b) => {
      const ra = onePageCardRank(a, index);
      const rb = onePageCardRank(b, index);
      const aa = ra == null ? 9999 : ra;
      const bb = rb == null ? 9999 : rb;
      return aa - bb || String(a.name || '').localeCompare(String(b.name || ''));
    });
  }

  function buildPortraitBody(team) {
    const pitchers = sortPitchersCardOrder(selectedPitcherAdvanceSheets(), team);
    const pitcherHtml = pitchers.map((p) => buildPitcherAdvanceSheetHtml(p, { printPage: true })).join('\n');

    const hitterIds = selectedHitterAdvanceIds();
    const hitterPacks = hitterIds.map((id, i) => {
      const h = resolveAdvanceHitter(id);
      if (!h) return null;
      let sprayUrls = null;
      try { sprayUrls = bakeHitterSprayPairDataUrls([h]); } catch (e) { sprayUrls = null; }
      if (!(sprayUrls?.risp && sprayUrls?.non)) sprayUrls = null;
      return { h, sprayUrls, i };
    }).filter(Boolean);
    const hitterHtml = hitterPacks.map(({ h, sprayUrls, i }) => buildHitterAdvanceSheetHtml(h, [h], {
      printPage: true, prefix: `gp-h${i}-`, sprayUrls,
    })).join('\n');

    let sprayPlayers = selectedSprayPlayers();
    if (typeof sortByStrategyLineup === 'function') {
      sprayPlayers = sortByStrategyLineup(sprayPlayers, (p) => p.name, team);
    }
    // Prefer game-day only when strategy exists
    if (typeof filterToStrategyGameDay === 'function') {
      const gd = filterToStrategyGameDay(sprayPlayers, team);
      if (gd.length) sprayPlayers = gd;
    }
    const sprayHtml = sprayPlayers.map((p, i) => {
      const urls = bakeSprayHandUrls(p);
      return buildSprayHandSheetHtml(p, { printPage: true, prefix: `gp-s${i}-`, sprayUrls: urls });
    }).join('\n');

    const sections = [];
    if (pitcherHtml) sections.push(`<div class="gp-block" data-gp="pitcher-advance">${pitcherHtml}</div>`);
    if (hitterHtml) sections.push(`<div class="gp-block" data-gp="hitter-advance">${hitterHtml}</div>`);
    if (sprayHtml) sections.push(`<div class="gp-block" data-gp="spray">${sprayHtml}</div>`);
    return {
      body: sections.join('\n'),
      counts: {
        pitchers: pitchers.length,
        hitters: hitterPacks.length,
        spray: sprayPlayers.length,
      },
      hitterPacks,
    };
  }

  function buildOnePageBody(team) {
    const root = document.getElementById('onepage-view');
    const packRoot = root?.querySelector('.onepage-print');
    if (!packRoot) return '';
    const label = CACHE.pitcherOnePage?.teams?.[team]?.team_name || team;
    const clone = packRoot.cloneNode(true);
    clone.querySelectorAll('textarea').forEach((ta) => {
      const td = ta.closest('td') || ta.parentElement;
      if (td) {
        td.textContent = ta.value || '';
        td.classList.add('op-notes');
      }
    });
    clone.querySelectorAll('input.op-jersey').forEach((inp) => {
      const td = inp.closest('td') || inp.parentElement;
      if (td) {
        td.textContent = inp.value || '';
        td.classList.add('op-num');
      }
    });
    const titleText = clone.querySelector('.onepage-title')?.textContent || `${label} Pitchers`;
    const table = clone.querySelector('.onepage-grid');
    const theadHtml = table?.querySelector('thead')?.outerHTML || '';
    let rowEls = Array.from(table?.querySelectorAll('tbody tr') || []);
    // Official Active-this-week only (same gate as pitcher one-page mass PDF).
    const official = typeof officialActivePitcherIndex === 'function'
      ? officialActivePitcherIndex(team)
      : { ids: [], byId: new Map(), byName: new Map() };
    if (official.ids.length) {
      rowEls = rowEls.filter((tr) => {
        const id = Number(tr.getAttribute('data-op-id'));
        if (Number.isFinite(id) && official.byId.has(id)) return true;
        const key = tr.getAttribute('data-op-key') || '';
        return !!(key && official.byName && official.byName.has(key));
      });
    }
    const n = rowEls.length;
    let density = '';
    let perPage = 20;
    if (n > 20) { density = 'op-density-compact'; perPage = 26; }
    if (n > 26) { density = 'op-density-dense'; perPage = 32; }
    if (n > 32) { density = 'op-density-ultra'; perPage = 38; }
    const pageCount = Math.max(1, Math.ceil(Math.max(n, 1) / perPage));
    const pages = [];
    for (let i = 0; i < pageCount; i++) {
      const chunk = rowEls.slice(i * perPage, (i + 1) * perPage);
      const suffix = pageCount > 1 ? ` (${i + 1}/${pageCount})` : '';
      pages.push(
        `<div class="onepage-print-page${density ? ` ${density}` : ''}">` +
        `<div class="onepage-print">` +
        `<div class="onepage-title">${escHtml(titleText)}${suffix}</div>` +
        `<table class="onepage-grid">${theadHtml}<tbody>${chunk.map((r) => r.outerHTML).join('')}</tbody></table>` +
        `</div></div>`
      );
    }
    return pages.join('');
  }

  function buildBullpenBody(team) {
    const data = typeof activeBullpenData === 'function' ? activeBullpenData() : CACHE.bullpenUsage;
    const pack = data?.teams?.[team];
    if (!pack || !(pack.games || []).length) return '';
    const label = pack.team_name || team;
    const tag = (typeof BULLPEN_TAB !== 'undefined' && BULLPEN_TAB === 'test') ? 'Test Dec 2025' : '2026-27';
    const inner = buildBullpenSheetHtml(pack, { teamLabel: `${label} (${tag})`, forPrint: true });
    return `<div class="bullpen-print-page">${inner}</div>`;
  }

  function buildSmallBallBody() {
    const el = document.getElementById('smallball-sheet');
    if (!el) return '';
    const clone = el.cloneNode(true);
    clone.querySelectorAll('textarea').forEach((ta) => {
      const div = document.createElement('div');
      div.textContent = ta.value;
      div.style.whiteSpace = 'pre-wrap';
      div.style.minHeight = '36px';
      ta.replaceWith(div);
    });
    return `<div class="gp-section gp-smallball">${clone.outerHTML}</div>`;
  }

  function buildLandscapeBody(team) {
    const parts = [];
    const one = buildOnePageBody(team);
    if (one) parts.push(`<div class="gp-section" data-gp="onepage">${one}</div>`);
    const bp = buildBullpenBody(team);
    if (bp) parts.push(`<div class="gp-section" data-gp="bullpen">${bp}</div>`);
    if (root.PosApp) {
      const ofb = PosApp.buildOfPrint('ALL');
      if (ofb?.body) parts.push(`<div class="gp-section" data-gp="of">${ofb.body}</div>`);
      const inf = PosApp.buildInfPrint('ALL');
      if (inf?.body) parts.push(`<div class="gp-section" data-gp="inf">${inf.body}</div>`);
    }
    const sb = buildSmallBallBody();
    if (sb) parts.push(sb);
    return { body: parts.join('\n'), count: parts.length };
  }

  function openLandscapePacketWindow(title, bodyHtml) {
    const w = window.open('', '_blank');
    if (!w) {
      alert('Pop-up blocked — allow pop-ups to export the game packet PDF.');
      return null;
    }
    const sheetCss = typeof sheetPrintCss === 'function'
      ? `${sheetPrintCss('bullpen')}\n${sheetPrintCss('onepage')}`
      : '';
    w.document.write(`<!doctype html><html style="color-scheme:only light"><head>
      <meta charset="utf-8"><meta name="color-scheme" content="only light">
      <title>${escHtml(title)}</title>
      <style>
        ${sheetCss}
        ${POS_PRINT_CSS}
        @page { size: letter landscape; margin: 0.28in; }
        html,body{margin:0;padding:0;background:#fff;color:#111}
        .gp-section{page-break-before:always;break-before:page}
        .gp-section:first-child{page-break-before:auto;break-before:auto}
      </style>
    </head><body>${bodyHtml}
    <script>window.onload=()=>setTimeout(()=>window.print(),450);<\/script>
    </body></html>`);
    w.document.close();
    return w;
  }

  function openPortraitPacketWindow(title, bodyHtml, hitterPacks) {
    return openAdvancePrintWindow(title, bodyHtml, {
      orientation: 'portrait',
      pageMargin: '0.55in 0.32in 0.28in 0.32in',
      autoPrint: false,
      afterWrite(w) {
        const paintAndPrint = () => {
          (hitterPacks || []).forEach(({ h, sprayUrls, i }) => {
            if (sprayUrls?.risp && sprayUrls?.non) return;
            const prefix = `gp-h${i}-`;
            try { paintHitterSprayPair(prefix, [h], w.document); } catch (e) {}
          });
          try { bakeHitterSprayCanvasesForPrint(w.document); } catch (e) {}
          const imgs = [...w.document.querySelectorAll('img.spray-print-img')];
          let printed = false;
          const go = () => {
            if (printed) return;
            printed = true;
            setTimeout(() => { try { w.focus(); w.print(); } catch (e) {} }, 180);
          };
          if (!imgs.length) { go(); return; }
          let left = imgs.length;
          const tick = () => { left -= 1; if (left <= 0) go(); };
          imgs.forEach((img) => {
            if (img.complete && img.naturalWidth > 0) tick();
            else { img.onload = tick; img.onerror = tick; }
          });
          setTimeout(go, 2500);
        };
        setTimeout(() => {
          if (w.requestAnimationFrame) w.requestAnimationFrame(() => w.requestAnimationFrame(paintAndPrint));
          else paintAndPrint();
        }, 280);
      },
    });
  }

  function dismissBar() {
    const bar = document.getElementById('game-packet-bar');
    if (bar?._focusHandler) {
      window.removeEventListener('focus', bar._focusHandler);
      document.removeEventListener('visibilitychange', bar._focusHandler);
    }
    bar?.remove();
  }

  function presentBar(ctx) {
    dismissBar();
    const bar = document.createElement('div');
    bar.id = 'game-packet-bar';
    bar.setAttribute('role', 'status');
    bar.style.cssText = 'position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:99999;background:#1a2332;color:#fff;padding:14px 16px;border-radius:10px;display:flex;flex-direction:column;gap:10px;box-shadow:0 8px 28px rgba(0,0,0,.45);font-size:13px;max-width:min(820px,94vw);border:1px solid #3a4a63';
    bar._ctx = ctx;
    document.body.appendChild(bar);
    renderBarStep(bar);
  }

  function renderBarStep(bar) {
    const ctx = bar._ctx;
    const step = ctx.steps[ctx.stepIndex];
    const excelN = ctx.files.filter((f) => /\.xlsx$/i.test(f.name)).length;
    const pdfN = ctx.files.filter((f) => /\.pdf$/i.test(f.name)).length;
    const status = document.createElement('div');
    status.id = 'game-packet-status';
    status.style.cssText = 'opacity:.9;font-size:12px';

    if (!step) {
      bar.innerHTML =
        `<div style="line-height:1.45">` +
          `<strong style="display:block;margin-bottom:4px">Game packet ready — ${ctx.team} · ${ctx.date}</strong>` +
          `<div>${excelN} Excel · ${pdfN} PDF in ZIP. Pitcher media ZIP is separate (do that after).</div>` +
        `</div>` +
        `<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">` +
          `<button type="button" class="btn" id="gp-dl" style="font-weight:700">Download ZIP</button>` +
          `<button type="button" class="btn btn-sm" id="gp-dismiss">Close</button>` +
        `</div>`;
      bar.querySelector('#gp-dismiss').onclick = () => dismissBar();
      bar.querySelector('#gp-dl').onclick = () => {
        downloadBlob(zipStore(ctx.files), ctx.zipName);
        status.textContent = 'Downloaded.';
      };
      downloadBlob(zipStore(ctx.files), ctx.zipName);
      return;
    }

    bar.innerHTML =
      `<div style="line-height:1.45">` +
        `<strong style="display:block;margin-bottom:4px">Game Packet · ${ctx.team} · step ${ctx.stepIndex + 1}/${ctx.steps.length}</strong>` +
        `<div><b>1.</b> Print dialog → Destination → <b>Save as PDF</b> → Save.</div>` +
        `<div><b>2.</b> Click <b>Choose saved PDF</b> to add <code>${step.fileName}</code> to the packet ZIP.</div>` +
        `<div style="margin-top:4px;opacity:.9">${step.blurb}</div>` +
        `<div style="margin-top:4px;opacity:.75">Excels already queued (${excelN}). Pitcher delivery ZIP stays separate.</div>` +
      `</div>` +
      `<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">` +
        `<button type="button" class="btn" id="gp-print" style="font-weight:700">Print / Save PDF</button>` +
        `<button type="button" class="btn" id="gp-pick" style="font-weight:700">Choose saved PDF</button>` +
        `<button type="button" class="btn btn-sm" id="gp-skip">Skip this PDF</button>` +
        `<button type="button" class="btn btn-sm" id="gp-dismiss">Cancel</button>` +
      `</div>`;
    bar.appendChild(status);

    bar.querySelector('#gp-dismiss').onclick = () => dismissBar();
    bar.querySelector('#gp-skip').onclick = () => {
      ctx.stepIndex += 1;
      renderBarStep(bar);
    };
    bar.querySelector('#gp-print').onclick = () => {
      try { step.openPrint(); }
      catch (e) {
        console.error(e);
        status.textContent = 'Could not open print window.';
      }
    };
    bar.querySelector('#gp-pick').onclick = async () => {
      const pickBtn = bar.querySelector('#gp-pick');
      try {
        pickBtn.disabled = true;
        status.textContent = 'Waiting for file…';
        const file = await pickSavedPrintPdfFile();
        const bytes = new Uint8Array(await file.arrayBuffer());
        ctx.files.push({ name: step.fileName, data: bytes });
        status.textContent = `Added ${step.fileName}.`;
        ctx.stepIndex += 1;
        setTimeout(() => renderBarStep(bar), 200);
      } catch (e) {
        pickBtn.disabled = false;
        if (String(e?.message || e) === 'cancelled') {
          status.textContent = '';
          return;
        }
        console.error(e);
        status.textContent = 'Could not read that PDF.';
      }
    };

    // Auto-open print once when the step appears
    setTimeout(() => {
      try { step.openPrint(); } catch (e) {}
    }, 350);

    let armed = false;
    setTimeout(() => { armed = true; }, 900);
    const onReturn = () => {
      if (!armed || !document.getElementById('game-packet-bar')) return;
      const btn = document.getElementById('gp-pick');
      if (!btn || btn.disabled) return;
      btn.style.outline = '3px solid #f5c542';
      btn.style.boxShadow = '0 0 0 4px rgba(245,197,66,.35)';
      try { btn.focus({ preventScroll: true }); } catch (e) {}
      status.textContent = 'Saved? Click Choose saved PDF.';
      setTimeout(() => {
        if (!btn.isConnected) return;
        btn.style.outline = '';
        btn.style.boxShadow = '';
      }, 2200);
    };
    if (bar._focusHandler) {
      window.removeEventListener('focus', bar._focusHandler);
      document.removeEventListener('visibilitychange', bar._focusHandler);
    }
    bar._focusHandler = onReturn;
    window.addEventListener('focus', onReturn);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') onReturn();
    });
  }

  function collectExcels(team) {
    const files = [];
    if (root.PosApp) {
      setSelect('ofpos-team', team);
      try {
        const ofx = PosApp.ofExcelBytes && PosApp.ofExcelBytes();
        if (ofx) files.push({ name: `positioning/${ofx.name}`, data: ofx.data });
      } catch (e) { console.error('OF excel', e); }
      try {
        const ifx = PosApp.infExcelBytes && PosApp.infExcelBytes();
        if (ifx) files.push({ name: `positioning/${ifx.name}`, data: ifx.data });
      } catch (e) { console.error('INF excel', e); }
    }
    const readme = [
      `Santurce game packet — ${team} — ${dateKey()}`,
      '',
      'Contents:',
      '  positioning/*.xlsx     INF + OF positioning workbooks',
      '  01_portrait_sheets.pdf Pitcher advance + hitter advance (lineup order) + spray charts',
      '  02_landscape_sheets.pdf Pitcher one-page (official actives) + bullpen + OF/INF one-pagers + small ball',
      '',
      'Do the pitcher delivery/movement ZIP separately from Pitcher Advance → Export uploads.',
      '',
    ].join('\n');
    files.push({ name: 'README.txt', data: new TextEncoder().encode(readme) });
    return files;
  }

  function exportGamePacket() {
    const team = opponentTeam();
    const date = dateKey();
    prepareFilters(team);

    // Give renders a tick so one-page / bullpen / small-ball DOM is ready
    setTimeout(() => {
      const files = collectExcels(team);
      const portrait = buildPortraitBody(team);
      const landscape = buildLandscapeBody(team);

      if (!portrait.body && !landscape.body && files.length <= 1) {
        alert('Nothing to pack — set the Strategy Card lineup / pen for the opponent first.');
        return;
      }

      const steps = [];
      if (portrait.body) {
        steps.push({
          fileName: '01_portrait_sheets.pdf',
          blurb: `Portrait: pitcher advance (${portrait.counts.pitchers}) · hitter advance (${portrait.counts.hitters}, lineup order) · spray (${portrait.counts.spray}).`,
          openPrint: () => openPortraitPacketWindow(
            `${team} Game Packet — Portrait`,
            portrait.body,
            portrait.hitterPacks
          ),
        });
      }
      if (landscape.body) {
        steps.push({
          fileName: '02_landscape_sheets.pdf',
          blurb: `Landscape: pitcher one-page (official actives) · bullpen · OF/INF one-pagers · small ball (${landscape.count} sections).`,
          openPrint: () => openLandscapePacketWindow(`${team} Game Packet — Landscape`, landscape.body),
        });
      }

      presentBar({
        team,
        date,
        files,
        steps,
        stepIndex: 0,
        zipName: `${team}_${date}_game_packet.zip`,
      });
    }, 120);
  }

  root.exportGamePacket = exportGamePacket;
  root.gamePacketOpponent = opponentTeam;
})(typeof window !== 'undefined' ? window : globalThis);

import puppeteer from 'puppeteer-core';
import { createServer } from 'http';
import { readFileSync, existsSync, statSync, writeFileSync } from 'fs';
import { extname, join, normalize } from 'path';

const root = '/Users/colbymorris/Santurce2026';
const mime = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff2': 'font/woff2',
};

const server = createServer((req, res) => {
  const urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
  const filePath = normalize(join(root, urlPath === '/' ? 'pregame.html' : urlPath.replace(/^\//, '')));
  if (!filePath.startsWith(root) || !existsSync(filePath) || !statSync(filePath).isFile()) {
    res.writeHead(404); res.end('missing'); return;
  }
  res.writeHead(200, { 'Content-Type': mime[extname(filePath)] || 'application/octet-stream' });
  res.end(readFileSync(filePath));
});

await new Promise((r) => server.listen(0, '127.0.0.1', r));
const { port } = server.address();
const base = `http://127.0.0.1:${port}`;

const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
});
const page = await browser.newPage();
const printDocs = [];
await page.exposeFunction('__capturePrintDoc', (html) => { printDocs.push(html); });
await page.evaluateOnNewDocument(() => {
  window.open = () => {
    const doc = {
      open() {},
      close() {},
      write(html) {
        this._html = (this._html || '') + String(html || '');
        window.__lastPrintHtml = this._html;
        if (typeof window.__capturePrintDoc === 'function') window.__capturePrintDoc(this._html);
      },
    };
    return { document: doc, print() {}, close() {}, focus() {}, onload: null, __scFitPrintPages: null };
  };
});

await page.goto(`${base}/pregame.html#strategy`, { waitUntil: 'networkidle0', timeout: 90000 });
await page.waitForFunction(() => document.getElementById('loading')?.style?.display === 'none' || !document.getElementById('loading')?.offsetParent, { timeout: 60000 });
await page.waitForFunction(() => typeof window.CardApp === 'object' && typeof CardApp.exportStrategy === 'function', { timeout: 20000 });
await page.waitForSelector('#strategy-sheet .sc-side', { timeout: 30000 });
await new Promise((r) => setTimeout(r, 400));

const cardStats = JSON.parse(readFileSync(join(root, 'data/pregame_card_stats.json'), 'utf8'));
await page.evaluate((stats) => {
  const KEY = 'santurce_strategy_lineup_v1';
  const POS = ['8', '9', '3', '4', '5', '6', '7', '2', 'DH'];
  const byTeam = {};
  (stats.players || []).forEach((p) => {
    if (!byTeam[p.team]) byTeam[p.team] = { H: [], P: [] };
    if (p.role === 'H') byTeam[p.team].H.push(p);
    if (p.role === 'P') byTeam[p.team].P.push(p);
  });
  const teamState = (abbr) => {
    const hitters = (byTeam[abbr]?.H || []).slice().sort((a, b) => String(a.last || a.name || '').localeCompare(String(b.last || b.name || '')));
    const pitchers = (byTeam[abbr]?.P || []).slice().sort((a, b) => String(a.last || a.name || '').localeCompare(String(b.last || b.name || '')));
    const lineup = hitters.slice(0, 9).map((p, i) => ({ id: p.id, pos: POS[i] }));
    const inLine = new Set(lineup.map((r) => r.id));
    const bench = hitters.filter((p) => !inLine.has(p.id)).map((p) => ({ id: p.id, pos: 'DH' }));
    const sp = pitchers[0] ? pitchers[0].id : null;
    const pen = pitchers.filter((p) => p.id !== sp).map((p) => p.id);
    return { sp, pen, lineup, bench };
  };
  const state = { opponent: 'PON', santurceHome: true, teams: { SAN: teamState('SAN'), PON: teamState('PON') } };
  localStorage.setItem(KEY, JSON.stringify(state));
  CardApp.setOpponent('PON');
  CardApp.setHome(true);
  CardApp.renderStrategy();
}, cardStats);

await new Promise((r) => setTimeout(r, 500));
printDocs.length = 0;
await page.evaluate(() => { window.__lastPrintHtml = ''; CardApp.exportStrategy(); });
await new Promise((r) => setTimeout(r, 300));
const html = printDocs[printDocs.length - 1] || await page.evaluate(() => window.__lastPrintHtml || '');
if (!html) {
  console.error(JSON.stringify({ error: 'no print html captured' }));
  await browser.close(); server.close(); process.exit(1);
}

const printPage = await browser.newPage();
await printPage.setContent(html, { waitUntil: 'load' });
await new Promise((r) => setTimeout(r, 150));
await printPage.evaluate(() => { if (typeof window.__scFitPrintPages === 'function') window.__scFitPrintPages(); });
await new Promise((r) => setTimeout(r, 80));

const layout = await printPage.evaluate(() => {
  const tallIn = 10.5, notesIn = 1.5;
  const avail = tallIn * 96;
  const bodyAvail = (tallIn - notesIn - 0.08) * 96;
  const pages = [...document.querySelectorAll('.sc-print-page')];
  const front = document.querySelector('.sc-print-front');
  const aux = document.querySelector('.sc-print-aux');
  const back = document.querySelector('.sc-print-back');
  const notes = document.querySelector('.sc-front-notes');
  const body = front?.querySelector('.sc-front-body');
  const scale = front?.querySelector('.sc-front-scale');
  const names = [...document.querySelectorAll('.sc-print-front .sc-last')].map((el) => el.textContent.trim()).filter(Boolean);
  const frontBox = front?.getBoundingClientRect();
  const notesBox = notes?.getBoundingClientRect();
  const bodyBox = body?.getBoundingClientRect();
  const scaleBox = scale?.getBoundingClientRect();
  const nameBoxes = [...document.querySelectorAll('.sc-print-front .sc-last')].map((el) => {
    const r = el.getBoundingClientRect();
    return { name: el.textContent.trim(), bottom: r.bottom, top: r.top };
  }).filter((x) => x.name);
  const ceiling = notesBox && bodyBox ? Math.min(notesBox.top, bodyBox.bottom) : (notesBox ? notesBox.top : Infinity);
  const namesPastNotes = nameBoxes.filter((n) => n.bottom > ceiling + 1).map((n) => n.name);
  const namesClipped = bodyBox
    ? nameBoxes.filter((n) => n.bottom > bodyBox.bottom + 1 || n.top < bodyBox.top - 1).map((n) => n.name)
    : [];
  const gapToNotesPx = notesBox && scaleBox ? notesBox.top - scaleBox.bottom : 999;
  const lineupEl = document.querySelector('.sc-print-front .sc-grid-lineup');
  const frontBench = document.querySelector('.sc-print-front .sc-grid-bench');
  const auxBench = document.querySelector('.sc-print-aux .sc-grid-bench');
  const auxPen = document.querySelector('.sc-print-aux .sc-grid-pen');
  const firstSide = document.querySelector('.sc-print-front .sc-side');
  const ordLabels = firstSide
    ? [...(firstSide.querySelectorAll('.sc-grid').item(0)?.querySelectorAll('.sc-ord, .sc-pos') || [])].map((el) => el.textContent.trim())
    : [];
  return {
    printPageDivs: pages.length,
    hasFront: !!front,
    hasAux: !!aux,
    hasBack: !!back,
    hasNotes: !!notes,
    frontHasNoBenchPen: !frontBench && !document.querySelector('.sc-print-front .sc-grid-pen'),
    auxHasBenchPen: !!auxBench && !!auxPen,
    nameCount: names.length,
    namesAboveNotes: namesPastNotes.length === 0,
    notesAtBottom: !!(frontBox && notesBox && Math.abs(notesBox.bottom - frontBox.bottom) <= 2 && notesBox.height >= (notesIn * 96) - 4),
    notesHeightIn: notesBox ? notesBox.height / 96 : 0,
    frontHeightIn: frontBox ? frontBox.height / 96 : 0,
    bodyLayoutHeightIn: bodyBox ? bodyBox.height / 96 : 0,
    scaleLayoutHeightIn: scaleBox ? scaleBox.height / 96 : 0,
    bodyZoom: parseFloat(scale?.style?.zoom || '1') || 1,
    scaleTransform: scale?.style?.transform || '',
    frontFitsAvail: frontBox ? frontBox.height <= avail + 1 : false,
    scaleUsesMostOfBody: scaleBox ? scaleBox.height >= bodyAvail * 0.85 : false,
    fillsToNotes: gapToNotesPx >= -4 && gapToNotesPx <= 32,
    gapToNotesPx,
    lineupFontPx: lineupEl ? parseFloat(getComputedStyle(lineupEl).fontSize) : 0,
    lineupHeightIn: lineupEl ? lineupEl.getBoundingClientRect().height / 96 : 0,
    namesPastNotes: namesPastNotes.slice(0, 8),
    namesClipped: namesClipped.slice(0, 8),
    clippedCount: namesClipped.length,
    ordLabels: ordLabels.slice(0, 12),
    fills: front ? front.classList.contains('sc-fill') : false,
    backHasTtp: !!back?.querySelector('.sc-ttp-table'),
    backHasPop: (back?.querySelectorAll('.sc-bar') || []).length >= 2,
    backBars: [...(back?.querySelectorAll('.sc-bar') || [])].map((el) => el.textContent.trim()),
    backInnerH: (back?.querySelector('.sc-back')?.getBoundingClientRect().height || 0) / 96,
  };
});

const pdf = await printPage.pdf({
  format: 'Letter',
  printBackground: true,
  margin: { top: '0.18in', right: '0.18in', bottom: '0.18in', left: '0.18in' },
});
writeFileSync(join(root, 'runs', 'strategy_card_measure.pdf'), pdf);
await printPage.screenshot({ path: join(root, 'runs', 'strategy_card_measure.png'), fullPage: false });
const pdfText = Buffer.from(pdf).toString('latin1');
const pdfPages = (pdfText.match(/\/Type\s*\/Page[^s]/g) || []).length;

const result = {
  ...layout,
  pdfPages,
  ok:
    pdfPages === 3
    && layout.printPageDivs === 3
    && layout.hasFront
    && layout.hasAux
    && layout.hasBack
    && layout.hasNotes
    && layout.frontHasNoBenchPen
    && layout.auxHasBenchPen
    && layout.namesAboveNotes
    && layout.notesAtBottom
    && layout.notesHeightIn >= 1.45
    && layout.frontFitsAvail
    && layout.clippedCount === 0
    && layout.backHasTtp
    && layout.backHasPop
    && layout.fillsToNotes
    && layout.scaleUsesMostOfBody,
};
console.log(JSON.stringify(result, null, 2));

// Also measure the live SAN vs MAY published card.
const gds = JSON.parse(readFileSync(join(root, 'data/game_day_state.json'), 'utf8'));
await page.evaluate((doc) => {
  const KEY = 'santurce_strategy_lineup_v1';
  const strategy = doc.strategy || {};
  localStorage.setItem(KEY, JSON.stringify(strategy));
  CardApp.setOpponent(strategy.opponent || 'MAY');
  CardApp.setHome(!!strategy.santurceHome);
  CardApp.renderStrategy();
}, gds);
await new Promise((r) => setTimeout(r, 400));
printDocs.length = 0;
await page.evaluate(() => { window.__lastPrintHtml = ''; CardApp.exportStrategy(); });
await new Promise((r) => setTimeout(r, 300));
const mayHtml = printDocs[printDocs.length - 1] || '';
const mayPage = await browser.newPage();
await mayPage.setContent(mayHtml, { waitUntil: 'load' });
await new Promise((r) => setTimeout(r, 150));
await mayPage.evaluate(() => { if (typeof window.__scFitPrintPages === 'function') window.__scFitPrintPages(); });
await new Promise((r) => setTimeout(r, 80));
const mayLayout = await mayPage.evaluate(() => {
  const notes = document.querySelector('.sc-front-notes');
  const scale = document.querySelector('.sc-front-scale');
  const lineup = document.querySelector('.sc-grid-lineup');
  const bench = document.querySelector('.sc-grid-bench');
  const who = document.querySelector('.sc-grid-lineup td.sc-who');
  const lastWho = [...document.querySelectorAll('.sc-grid-lineup td.sc-who')].pop();
  const notesBox = notes?.getBoundingClientRect();
  const scaleBox = scale?.getBoundingClientRect();
  const lineupBox = lineup?.getBoundingClientRect();
  const whoBox = who?.getBoundingClientRect();
  const lastBox = lastWho?.getBoundingClientRect();
  const front = document.querySelector('.sc-print-front');
  return {
    notesH: notesBox ? notesBox.height / 96 : 0,
    gapToNotesPx: notesBox && scaleBox ? notesBox.top - scaleBox.bottom : 999,
    lastWhoToNotesPx: notesBox && lastBox ? notesBox.top - lastBox.bottom : 999,
    lineupH: lineupBox ? lineupBox.height / 96 : 0,
    whoH: whoBox ? whoBox.height : 0,
    namePct: whoBox && lineupBox && lineupBox.width ? +(100 * whoBox.width / lineupBox.width).toFixed(1) : 0,
    whoWhiteSpace: who ? getComputedStyle(who).whiteSpace : '',
    bodyZoom: parseFloat(scale?.style?.zoom || '1') || 1,
    benchZoom: parseFloat(bench?.style?.zoom || '1') || 1,
    benchTransform: bench?.style?.transform || '',
    fills: front ? front.classList.contains('sc-fill') : false,
    auxXdense: front ? front.classList.contains('sc-aux-xdense') : false,
    names: [...document.querySelectorAll('.sc-grid-lineup .sc-last')].slice(0, 6).map((el) => el.textContent.trim()),
  };
});
writeFileSync(join(root, 'runs', 'strategy_card_may_measure.png'), await mayPage.screenshot({ type: 'png' }));
const mayPdf = await mayPage.pdf({
  format: 'Letter',
  printBackground: true,
  margin: { top: '0.18in', right: '0.18in', bottom: '0.18in', left: '0.18in' },
});
writeFileSync(join(root, 'runs', 'strategy_card_may_measure.pdf'), mayPdf);
console.log('MAY', JSON.stringify(mayLayout, null, 2));
await mayPage.close();
await browser.close();
server.close();
process.exit(result.ok ? 0 : 1);

/**
 * Measure strategy-card PDF: exactly 2 pages.
 * Page 1 = full lineup body + 1.5in notes below all names (no mid-lineup notes).
 * Page 2 = TTP + catcher pop only.
 * Stress: Ponce (long bench/pen) vs Santurce, both fully filled.
 */
import { chromium } from 'playwright';
import { createServer } from 'http';
import { readFileSync, existsSync, statSync, writeFileSync } from 'fs';
import { extname, join, normalize } from 'path';
import { fileURLToPath } from 'url';

const root = join(fileURLToPath(import.meta.url), '..', '..');
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

process.env.PLAYWRIGHT_BROWSERS_PATH = join(root, '.pw-browsers');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
const page = await context.newPage();

const printDocs = [];
await page.exposeFunction('__capturePrintDoc', (html) => {
  printDocs.push(html);
});

await page.addInitScript(() => {
  window.open = () => {
    const doc = {
      open() {},
      close() {},
      write(html) {
        this._html = (this._html || '') + String(html || '');
        window.__lastPrintHtml = this._html;
        if (typeof window.__capturePrintDoc === 'function') {
          window.__capturePrintDoc(this._html);
        }
      },
    };
    return {
      document: doc,
      print() {},
      close() {},
      focus() {},
      onload: null,
      __scFitPrintPages: null,
    };
  };
});

await page.goto(`${base}/pregame.html#strategy`, { waitUntil: 'networkidle' });
await page.waitForSelector('#loading', { state: 'hidden', timeout: 60000 });
await page.waitForFunction(() => typeof window.CardApp === 'object' && typeof CardApp.exportStrategy === 'function', { timeout: 20000 });
await page.waitForSelector('#strategy-sheet .sc-side', { timeout: 30000 });
await page.waitForTimeout(400);

// Fill long Ponce + Santurce cards (lineup 9, rest bench, all pen).
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
  const state = {
    opponent: 'PON',
    santurceHome: true,
    teams: {
      SAN: teamState('SAN'),
      PON: teamState('PON'),
    },
  };
  localStorage.setItem(KEY, JSON.stringify(state));
  CardApp.setOpponent('PON');
  CardApp.setHome(true);
  CardApp.renderStrategy();
}, cardStats);

await page.waitForTimeout(500);
await page.waitForSelector('#strategy-sheet .sc-side', { timeout: 10000 });

printDocs.length = 0;
await page.evaluate(() => { window.__lastPrintHtml = ''; });
await page.evaluate(() => CardApp.exportStrategy());
await page.waitForTimeout(200);

const html = printDocs[printDocs.length - 1] || await page.evaluate(() => window.__lastPrintHtml || '');
if (!html) {
  console.error(JSON.stringify({ error: 'no print html captured' }, null, 2));
  await browser.close();
  server.close();
  process.exit(1);
}

const printPage = await context.newPage();
await printPage.setContent(html, { waitUntil: 'load' });
await printPage.waitForTimeout(150);
// Run embedded fit (or fallback).
await printPage.evaluate(() => {
  if (typeof window.__scFitPrintPages === 'function') window.__scFitPrintPages();
});
await printPage.waitForTimeout(50);

  const layout = await printPage.evaluate(() => {
  const tallIn = 10.5;
  const notesIn = 1.5;
  const avail = tallIn * 96;
  const bodyAvail = (tallIn - notesIn - 0.08) * 96;
  const pages = [...document.querySelectorAll('.sc-print-page')];
  const front = document.querySelector('.sc-print-front');
  const back = document.querySelector('.sc-print-back');
  const notes = document.querySelector('.sc-front-notes');
  const body = document.querySelector('.sc-front-body');
  const names = [...document.querySelectorAll('.sc-print-front .sc-last')].map((el) => el.textContent.trim()).filter(Boolean);

  const frontBox = front?.getBoundingClientRect();
  const notesBox = notes?.getBoundingClientRect();
  const bodyBox = body?.getBoundingClientRect();
  const nameBoxes = [...document.querySelectorAll('.sc-print-front .sc-last')].map((el) => {
    const r = el.getBoundingClientRect();
    return { name: el.textContent.trim(), bottom: r.bottom, top: r.top };
  }).filter((x) => x.name);

  const notesAtBottom = frontBox && notesBox
    ? Math.abs(notesBox.bottom - frontBox.bottom) <= 2 && notesBox.height >= (notesIn * 96) - 4
    : false;

  const scale = document.querySelector('.sc-front-scale');
  const scaleBox = scale?.getBoundingClientRect();
  const bodyZoom = parseFloat(scale?.style?.zoom || body?.style?.zoom || '1') || 1;
  const backZoom = parseFloat(back?.style?.zoom || '1') || 1;
  const frontOverflowY = front ? getComputedStyle(front).overflowY : '';
  const frontH = frontBox ? frontBox.height : 0;
  const bodyLayoutH = bodyBox ? bodyBox.height : 0;
  const scaleLayoutH = scaleBox ? scaleBox.height : 0;
  const notesH = notesBox ? notesBox.height : 0;
  const gapToNotesPx = notesBox && scaleBox ? notesBox.top - scaleBox.bottom : 999;
  const lineupEl = document.querySelector('.sc-print-front .sc-grid-lineup');
  const benchEl = document.querySelector('.sc-print-front .sc-grid-bench');
  const lineupFontPx = lineupEl ? parseFloat(getComputedStyle(lineupEl).fontSize) : 0;
  const benchFontPx = benchEl ? parseFloat(getComputedStyle(benchEl).fontSize) : 0;
  const ceiling = notesBox && bodyBox
    ? Math.min(notesBox.top, bodyBox.bottom)
    : (notesBox ? notesBox.top : Infinity);

  // Names must sit inside the body shell and above the notes band (not clipped).
  const namesPastNotes = nameBoxes
    .filter((n) => n.bottom > ceiling + 1 || (bodyBox && n.bottom > bodyBox.bottom + 1))
    .map((n) => n.name);
  const namesClipped = bodyBox
    ? nameBoxes.filter((n) => n.bottom > bodyBox.bottom + 1 || n.top < bodyBox.top - 1).map((n) => n.name)
    : [];

  // Spot-check pitcher after #9 on first side: order labels before first P.
  const firstSide = document.querySelector('.sc-print-front .sc-side');
  const ordLabels = firstSide
    ? [...firstSide.querySelectorAll('.sc-grid').item(0)?.querySelectorAll('.sc-ord, .sc-pos') || []]
        .map((el) => el.textContent.trim())
    : [];

  return {
    printPageDivs: pages.length,
    hasFront: !!front,
    hasBack: !!back,
    hasNotes: !!notes,
    nameCount: names.length,
    sampleNames: names.slice(0, 8),
    lastNames: names.slice(-6),
    namesAboveNotes: namesPastNotes.length === 0,
    notesAtBottom,
    notesHeightIn: notesH / 96,
    frontHeightIn: frontH / 96,
    bodyLayoutHeightIn: bodyLayoutH / 96,
    scaleLayoutHeightIn: scaleLayoutH / 96,
    bodyZoom,
    backZoom,
    frontOverflowY,
    frontFitsAvail: frontH <= avail + 1,
    bodyPlusNotesFits: (bodyLayoutH + notesH) <= avail + 2,
    scaleUsesMostOfBody: scaleLayoutH >= bodyAvail * 0.85 || bodyZoom >= 0.95,
    fillsToNotes: gapToNotesPx >= -4 && gapToNotesPx <= 32,
    gapToNotesPx,
    lineupFontPx,
    benchFontPx,
    lineupBiggerThanBench: !benchFontPx || lineupFontPx + 0.4 >= benchFontPx,
    namesPastNotes,
    namesClipped,
    ordLabels: ordLabels.slice(0, 12),
    dense: front ? front.classList.contains('sc-dense') : false,
    xdense: front ? front.classList.contains('sc-xdense') : false,
    auxDense: front ? front.classList.contains('sc-aux-dense') : false,
    auxXdense: front ? front.classList.contains('sc-aux-xdense') : false,
    fills: front ? front.classList.contains('sc-fill') : false,
    backHasTtp: !!back?.querySelector('.sc-ttp-table'),
    backHasPop: (back?.querySelectorAll('.sc-bar') || []).length >= 2,
    backBars: [...(back?.querySelectorAll('.sc-bar') || [])].map((el) => el.textContent.trim()),
  };
});

const pdf = await printPage.pdf({
  format: 'Letter',
  printBackground: true,
  margin: { top: '0.18in', right: '0.18in', bottom: '0.18in', left: '0.18in' },
});
const pdfText = Buffer.from(pdf).toString('latin1');
const pdfPages = (pdfText.match(/\/Type\s*\/Page[^s]/g) || []).length;
writeFileSync(join(root, 'runs', 'strategy_card_measure.pdf'), pdf);

const result = {
  ...layout,
  pdfPages,
  exactlyTwoPages: pdfPages === 2 && layout.printPageDivs === 2,
  ok:
    pdfPages === 2
    && layout.printPageDivs === 2
    && layout.hasFront
    && layout.hasBack
    && layout.hasNotes
    && layout.namesAboveNotes
    && layout.notesAtBottom
    && layout.notesHeightIn >= 1.45
    && layout.frontFitsAvail
    && layout.namesPastNotes.length === 0
    && layout.namesClipped.length === 0
    && layout.backHasTtp
    && layout.backHasPop
    && layout.fillsToNotes
    && layout.scaleUsesMostOfBody
    && layout.lineupBiggerThanBench,
};

console.log(JSON.stringify(result, null, 2));
await browser.close();
server.close();
process.exit(result.ok ? 0 : 1);

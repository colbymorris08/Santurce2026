/* Split a mass/print PDF into one file per page (pdf-lib copyPages, same boxes).
   Used by pitcher/hitter/spray ZIP + Download each. No re-layout, no letter resize. */
(function (root) {
  function asU8(data) {
    if (data instanceof Uint8Array) return data;
    if (data instanceof ArrayBuffer) return new Uint8Array(data);
    if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    return new Uint8Array(data || 0);
  }

  function crc32Bytes(bytes) {
    const u8 = asU8(bytes);
    let c = ~0;
    for (let i = 0; i < u8.length; i++) {
      c ^= u8[i];
      for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xEDB88320 & -(c & 1));
    }
    return ~c >>> 0;
  }

  function zipStoreBytes(files) {
    const enc = new TextEncoder();
    const parts = [];
    const central = [];
    let offset = 0;
    (files || []).forEach((f) => {
      const name = enc.encode(String(f.name || 'file'));
      const data = asU8(f.data);
      const crc = crc32Bytes(data);
      const local = new Uint8Array(30 + name.length);
      const view = new DataView(local.buffer);
      view.setUint32(0, 0x04034b50, true);
      view.setUint16(4, 20, true);
      view.setUint32(14, crc, true);
      view.setUint32(18, data.length, true);
      view.setUint32(22, data.length, true);
      view.setUint16(26, name.length, true);
      local.set(name, 30);
      parts.push(local, data);
      const cen = new Uint8Array(46 + name.length);
      const cv = new DataView(cen.buffer);
      cv.setUint32(0, 0x02014b50, true);
      cv.setUint16(4, 20, true);
      cv.setUint16(6, 20, true);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, data.length, true);
      cv.setUint32(24, data.length, true);
      cv.setUint16(28, name.length, true);
      cv.setUint32(42, offset, true);
      cen.set(name, 46);
      central.push(cen);
      offset += local.length + data.length;
    });
    const cenStart = offset;
    let cenSize = 0;
    central.forEach((c) => { parts.push(c); cenSize += c.length; });
    const end = new Uint8Array(22);
    const ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(8, files.length, true);
    ev.setUint16(10, files.length, true);
    ev.setUint32(12, cenSize, true);
    ev.setUint32(16, cenStart, true);
    parts.push(end);
    let total = 0;
    parts.forEach((p) => { total += p.length; });
    const out = new Uint8Array(total);
    let o = 0;
    parts.forEach((p) => { out.set(p, o); o += p.length; });
    return out;
  }

  function zipStore(files) {
    const bytes = zipStoreBytes(files);
    if (typeof Blob === 'function') return new Blob([bytes], { type: 'application/zip' });
    return bytes;
  }

  function playerFileStem(name) {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return 'player';
    const last = parts[parts.length - 1];
    const first = parts.slice(0, -1).join('_') || 'player';
    return `${last}_${first}`.replace(/[^\w.-]+/g, '_');
  }

  function uniquePdfRelPaths(files) {
    const used = new Set();
    return (files || []).map((f) => {
      let name = String(f.name || 'player.pdf');
      if (!/\.pdf$/i.test(name)) name += '.pdf';
      const m = name.match(/^(.*)(\.pdf)$/i);
      const base = m ? m[1] : name;
      const ext = m ? m[2] : '';
      let out = name;
      let n = 2;
      while (used.has(out.toLowerCase())) {
        out = `${base}_${n}${ext}`;
        n++;
      }
      used.add(out.toLowerCase());
      return Object.assign({}, f, { name: out });
    });
  }

  function copyBoxIfPresent(srcPage, destPage, nodeName, getter, setter) {
    const nodeFn = srcPage.node && srcPage.node[nodeName];
    const node = typeof nodeFn === 'function' ? nodeFn.call(srcPage.node) : null;
    if (!node) return;
    const r = getter.call(srcPage);
    setter.call(destPage, r.x, r.y, r.width, r.height);
  }

  /** Flatten inherited MediaBox/CropBox/Rotate onto the copied leaf so viewers
      don't fall back to letter (612×792) and warp landscape print pages. */
  function copyExactPageBoxes(srcPage, destPage) {
    const media = srcPage.getMediaBox();
    destPage.setMediaBox(media.x, media.y, media.width, media.height);
    copyBoxIfPresent(srcPage, destPage, 'CropBox', srcPage.getCropBox, destPage.setCropBox);
    copyBoxIfPresent(srcPage, destPage, 'BleedBox', srcPage.getBleedBox, destPage.setBleedBox);
    copyBoxIfPresent(srcPage, destPage, 'TrimBox', srcPage.getTrimBox, destPage.setTrimBox);
    copyBoxIfPresent(srcPage, destPage, 'ArtBox', srcPage.getArtBox, destPage.setArtBox);
    try {
      destPage.setRotation(srcPage.getRotation());
    } catch (e) {}
  }

  function pageBoxSnapshot(page) {
    const media = page.getMediaBox();
    const crop = page.getCropBox();
    let rotate = 0;
    try { rotate = page.getRotation().angle || 0; } catch (e) { rotate = 0; }
    return {
      media: { x: media.x, y: media.y, width: media.width, height: media.height },
      crop: { x: crop.x, y: crop.y, width: crop.width, height: crop.height },
      rotate,
    };
  }

  async function splitPrintPdfToPageBytes(pdfBytes, PDFDocument) {
    if (!PDFDocument) throw new Error('pdf-lib PDFDocument required');
    const src = await PDFDocument.load(pdfBytes);
    const srcPages = src.getPages();
    const pages = [];
    const boxes = [];
    const n = src.getPageCount();
    for (let i = 0; i < n; i++) {
      const doc = await PDFDocument.create();
      const [page] = await doc.copyPages(src, [i]);
      doc.addPage(page);
      copyExactPageBoxes(srcPages[i], page);
      pages.push(new Uint8Array(await doc.save()));
      boxes.push(pageBoxSnapshot(page));
    }
    return { pages, boxes, pageCount: n };
  }

  /** Every source page becomes a file. Extra pages → unmapped/page_NNN.pdf. Never drop with Math.min. */
  function namedPdfFilesFromPrintPages(entries, pageBytes) {
    const list = entries || [];
    const files = [];
    for (let i = 0; i < pageBytes.length; i++) {
      const e = list[i];
      const name = (e && e.name)
        ? `${String(e.team || 'TEAM').replace(/[^\w.-]+/g, '_')}/${playerFileStem(e.name)}.pdf`
        : `unmapped/page_${String(i + 1).padStart(3, '0')}.pdf`;
      files.push({ name, data: pageBytes[i] });
    }
    return uniquePdfRelPaths(files);
  }

  function splitCountNote(split) {
    const pagesIn = split.pages;
    const filesOut = (split.files || []).length;
    const selected = split.selected || 0;
    const named = Math.min(pagesIn, selected);
    const unmapped = Math.max(0, pagesIn - selected);
    const missing = Math.max(0, selected - pagesIn);
    let msg = `${pagesIn} page${pagesIn === 1 ? '' : 's'} in → ${filesOut} PDF${filesOut === 1 ? '' : 's'} out`;
    if (unmapped) msg += ` (${named} named, ${unmapped} unmapped)`;
    else if (missing) msg += ` (${named} named; ${missing} selected had no page)`;
    else msg += ` (${named} named)`;
    return msg;
  }

  async function splitPrintPdfToNamedFiles(entries, pdfBytes, PDFDocument) {
    const split = await splitPrintPdfToPageBytes(pdfBytes, PDFDocument);
    if (!split.pages.length) throw new Error('empty pdf');
    const files = namedPdfFilesFromPrintPages(entries, split.pages);
    if (!files.length) throw new Error('no pages');
    return {
      files,
      pages: split.pageCount,
      selected: (entries || []).length,
      boxes: split.boxes,
      named: Math.min(split.pageCount, (entries || []).length),
      unmapped: Math.max(0, split.pageCount - (entries || []).length),
    };
  }

  const api = {
    asU8,
    crc32Bytes,
    zipStoreBytes,
    zipStore,
    playerFileStem,
    uniquePdfRelPaths,
    copyExactPageBoxes,
    pageBoxSnapshot,
    splitPrintPdfToPageBytes,
    namedPdfFilesFromPrintPages,
    splitCountNote,
    splitPrintPdfToNamedFiles,
  };

  root.AdvancePdfSplit = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
  ['zipStore', 'zipStoreBytes', 'playerFileStem', 'uniquePdfRelPaths',
    'namedPdfFilesFromPrintPages', 'splitPrintPdfToPageBytes',
    'splitPrintPdfToNamedFiles', 'splitCountNote'].forEach((k) => {
    root[k] = api[k];
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);

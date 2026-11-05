#!/usr/bin/env node
/** Verify mass-PDF split: N pages → N files in ZIP, identical MediaBox/CropBox/Rotate. */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const { PDFDocument, degrees } = require('pdf-lib');
const split = require('../advance-pdf-split.js');

function approx(a, b, eps) {
  return Math.abs(a - b) < (eps || 0.01);
}
function boxEq(a, b, label) {
  const keys = ['x', 'y', 'width', 'height'];
  for (const k of keys) {
    if (!approx(a[k], b[k])) {
      throw new Error(`${label} ${k}: src ${a[k]} vs out ${b[k]}`);
    }
  }
}

async function main() {
  const src = await PDFDocument.create();
  const specs = [
    { size: [792, 612], crop: [10, 20, 760, 560], rotate: 90, text: 'landscape-crop' },
    { size: [500, 900], crop: [0, 0, 500, 900], rotate: 0, text: 'tall-custom' },
    { size: [612, 792], crop: [18, 18, 576, 756], rotate: 0, text: 'letter-inset' },
    { size: [842, 595], crop: [5, 5, 832, 585], rotate: 270, text: 'a4-land' },
  ];
  for (const spec of specs) {
    const page = src.addPage(spec.size);
    page.setCropBox(spec.crop[0], spec.crop[1], spec.crop[2], spec.crop[3]);
    page.setRotation(degrees(spec.rotate));
    page.drawText(spec.text, { x: 36, y: 36, size: 18 });
  }
  const srcBytes = await src.save();
  const srcReload = await PDFDocument.load(srcBytes);
  const srcBoxes = srcReload.getPages().map(split.pageBoxSnapshot);

  const entries = [
    { team: 'CAG', name: 'Juan Soto' },
    { team: 'PON', name: 'Jose Ortiz' },
  ];
  const named = await split.splitPrintPdfToNamedFiles(entries, srcBytes, PDFDocument);
  if (named.pages !== specs.length) throw new Error(`pages in ${named.pages} != ${specs.length}`);
  if (named.files.length !== specs.length) throw new Error(`files out ${named.files.length} != ${specs.length}`);
  if (named.named !== 2 || named.unmapped !== 2) {
    throw new Error(`count named=${named.named} unmapped=${named.unmapped}`);
  }
  if (!named.files[2].name.startsWith('unmapped/page_')) {
    throw new Error(`expected unmapped extra, got ${named.files[2].name}`);
  }

  for (let i = 0; i < specs.length; i++) {
    const outDoc = await PDFDocument.load(named.files[i].data);
    if (outDoc.getPageCount() !== 1) throw new Error(`file ${i} has ${outDoc.getPageCount()} pages`);
    const outBox = split.pageBoxSnapshot(outDoc.getPages()[0]);
    boxEq(srcBoxes[i].media, outBox.media, `page ${i} media`);
    boxEq(srcBoxes[i].crop, outBox.crop, `page ${i} crop`);
    if (srcBoxes[i].rotate !== outBox.rotate) {
      throw new Error(`page ${i} rotate ${srcBoxes[i].rotate} vs ${outBox.rotate}`);
    }
    if (approx(outBox.media.width, 612) && approx(outBox.media.height, 792) && specs[i].size[0] !== 612) {
      throw new Error(`page ${i} was resized to letter`);
    }
  }

  const zipBytes = split.zipStoreBytes(named.files);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'santurce-pdf-split-'));
  const zipPath = path.join(dir, 'sheets.zip');
  fs.writeFileSync(zipPath, zipBytes);
  const run = spawnSync('python3', ['-c', `
import zipfile, sys
z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
print(len(names))
for n in names:
    info = z.getinfo(n)
    print(n, info.file_size, info.compress_type)
    data = z.read(n)
    if not data.startswith(b'%PDF'):
        raise SystemExit('not a pdf: ' + n)
`, zipPath], { encoding: 'utf8' });
  if (run.status !== 0) {
    throw new Error(`unzip failed: ${run.stderr || run.stdout}`);
  }
  const lines = run.stdout.trim().split('\n');
  const zipCount = Number(lines[0]);
  if (zipCount !== specs.length) throw new Error(`zip count ${zipCount} != ${specs.length}\n${run.stdout}`);
  for (const line of lines.slice(1)) {
    if (!line.includes(' 0')) throw new Error(`expected STORE (compress_type 0): ${line}`);
  }

  const note = split.splitCountNote(named);
  console.log('ok', note);
  console.log(run.stdout.trim());
  fs.rmSync(dir, { recursive: true, force: true });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

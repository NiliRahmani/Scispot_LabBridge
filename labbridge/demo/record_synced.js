// Schedule-driven recorder: walks the app with highlights only (NO burned
// captions) and times each scene to the voiceover's sentence start times, so
// the right screen/highlight is on when each sentence is spoken. Subtitles and
// audio are added afterwards in ffmpeg. Prints HEAD_MS = ms from recording
// start to the schedule anchor, so the head can be trimmed to align with audio.
//
// Usage: URL=... OUTDIR=./vsync SCHED=/path/sentence_times.json node record_synced.js

const fs = require('fs');
const { chromium } = require('playwright');

const URL = process.env.URL || 'http://localhost:8536';
const OUTDIR = process.env.OUTDIR || './vsync';
const SCHED = process.env.SCHED;
const W = 1600, H = 900;
const LEAD = 1.4;   // seconds before a sentence to perform its screen transition

const sched = JSON.parse(fs.readFileSync(SCHED, 'utf8'));
const starts = sched.sentences.map(s => s.start);
const audioEnd = sched.audio_end;

(async () => {
  const recStart = Date.now();
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: W, height: H }, deviceScaleFactor: 1,
    recordVideo: { dir: OUTDIR, size: { width: W, height: H } },
  });
  const page = await context.newPage();

  async function ensureOverlay() {
    await page.evaluate(() => {
      if (window.__demoReady) return;
      const style = document.createElement('style');
      style.textContent = `
        header[data-testid="stHeader"], [data-testid="stToolbar"],
        [data-testid="stDecoration"], footer { display: none !important; }
        #demo-hl { position: fixed; z-index: 2147483646; pointer-events: none;
          border: 3px solid #f59e0b; border-radius: 11px; opacity: 0;
          box-shadow: 0 0 0 4px rgba(245,158,11,.22), 0 0 16px 4px rgba(245,158,11,.55);
          transition: all .30s cubic-bezier(.4,0,.2,1); }`;
      document.head.appendChild(style);
      const hl = document.createElement('div'); hl.id = 'demo-hl';
      document.body.appendChild(hl);
      window.__setHighlight = (r) => {
        if (!r) { hl.style.opacity = '0'; return; }
        hl.style.opacity = '1';
        hl.style.left = (r.x - 8) + 'px'; hl.style.top = (r.y - 8) + 'px';
        hl.style.width = (r.w + 16) + 'px'; hl.style.height = (r.h + 16) + 'px';
      };
      window.__demoReady = true;
    });
  }
  async function highlight(locator) {
    await ensureOverlay();
    if (!locator) { await page.evaluate(() => window.__setHighlight(null)); return; }
    try {
      await locator.scrollIntoViewIfNeeded({ timeout: 4000 });
      const r = await locator.evaluate((e) => { const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; });
      await page.evaluate((r) => window.__setHighlight(r), r);
    } catch (e) { console.log('hl skip:', e.message.split('\n')[0]); await page.evaluate(() => window.__setHighlight(null)); }
  }
  const clearHl = () => page.evaluate(() => window.__setHighlight && window.__setHighlight(null));

  const btn = (re) => page.getByRole('button', { name: re });
  const tab = (re) => page.getByRole('tab', { name: re });
  const txt = (re) => page.getByText(re).first();

  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.getByText('Upload a messy instrument export').waitFor({ timeout: 45000 });
  await ensureOverlay();
  await page.waitForTimeout(300);
  const t0 = Date.now();
  const headMs = t0 - recStart;
  console.log('HEAD_MS', headMs);

  const sleepUntil = async (tSec) => { const d = t0 + tSec * 1000 - Date.now(); if (d > 0) await page.waitForTimeout(d); };

  // navigation helpers (clear highlight, click, wait for the next screen) ----
  const nav = (clickLoc, waitLoc) => async () => {
    await clearHl(); await clickLoc.click();
    if (waitLoc) await waitLoc.waitFor({ timeout: 20000 });
  };
  const navTab = (re) => async () => { await clearHl(); await tab(re).click(); await page.waitForTimeout(400); };

  // scenes 0..12 == sentences 1..13
  const scenes = [
    { nav: null, target: null },                                                                   // S1 upload
    { nav: null, target: () => btn(/Load sample plate export/) },                                  // S2 load btn
    { nav: nav(btn(/Load sample plate export/), txt(/detected format/)), target: () => txt(/detected format/) }, // S3
    { nav: nav(btn(/Next/), page.getByText('Review the proposed schema mapping')), target: () => txt(/needs your judgement/) }, // S4
    { nav: nav(btn(/Confirm mapping/), page.getByText('Quality control & anomaly review')), target: () => page.locator('[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])').first() }, // S5
    { nav: nav(btn(/Next/), page.getByText('QC Intelligence — scientific')), target: () => page.getByText('QC Intelligence — scientific') }, // S6
    { nav: null, target: () => page.locator('[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])').first() }, // S7 std curve metrics
    { nav: navTab(/Replicate reliability/), target: () => page.locator('[data-testid="stVegaLiteChart"]:visible').first() }, // S8
    { nav: navTab(/Control monitoring/), target: () => page.locator('[data-testid="stVegaLiteChart"]:visible').first() }, // S9
    { nav: navTab(/Fitness verdict/), target: () => txt(/Not fit for analysis/) }, // S10
    { nav: nav(btn(/Next/), page.getByText('Clean, standardized records')), target: () => page.locator('[data-testid="stDataFrame"]').nth(1) }, // S11
    { nav: nav(btn(/Next/), page.getByText('Export & data-quality report')), target: () => page.locator('[data-testid="stHorizontalBlock"]:has([data-testid="stDownloadButton"])').first() }, // S12
    { nav: null, target: () => txt(/Data-Quality Summary/) }, // S13
  ];

  for (let i = 0; i < scenes.length; i++) {
    const s = scenes[i];
    if (s.nav) { await sleepUntil(starts[i] - LEAD); await s.nav(); }
    await sleepUntil(starts[i]);
    await highlight(s.target ? s.target() : null);
    console.log(`scene ${i + 1} @ ${((Date.now() - t0) / 1000).toFixed(2)}s (target ${starts[i]}s)`);
  }
  await sleepUntil(audioEnd);
  await clearHl();
  await page.waitForTimeout(400);

  await context.close();
  await browser.close();
  console.log('done; HEAD_MS', headMs);
})().catch((e) => { console.error('SYNC_REC_FAILED', e); process.exit(1); });

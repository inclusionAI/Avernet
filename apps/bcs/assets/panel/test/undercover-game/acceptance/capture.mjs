#!/usr/bin/env node
/**
 * Phase-4 acceptance capture for the undercover-game panel (#2608).
 *
 * Drives the REAL Vite preview page
 *   /test/undercover-game/visual-preview.html
 * in headless Google Chrome through raw CDP (no puppeteer / playwright):
 *
 *   - five wide review screenshots at a 760x1000 device viewport
 *   - four narrow screenshots at exact 320x600 / 360x640 viewports (incl. expanded workflow board)
 *   - layout measurements + assertions in acceptance-results.json
 *   - a real-browser keyboard walkthrough (CDP Input.dispatchKeyEvent)
 *   - a synthetic screen-recording GIF assembled with Pillow
 *
 * Everything is synthetic preview data from visual-preview.tsx. It is NOT
 * live BCS game evidence. See README.md for the exact bounds.
 *
 * Run from apps/bcs/assets/panel (the Vite server must already serve it):
 *   node test/undercover-game/acceptance/capture.mjs
 * Optional env:
 *   PREVIEW_BASE=http://127.0.0.1:4178
 *   CHROME_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
 *   PYTHON=/path/to/python3-with-pillow
 *   SKIP_GIF=1
 */
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import {
  OUT_DIR, PREVIEW_BASE, PREVIEW_PATH, PYTHON, SKIP_GIF,
  WIDE, NARROW_320, NARROW_360, PRIVACY_NEEDLES, sleep, gitInfo,
} from './lib/runtime.mjs';
import { CDP, launchChrome, createTarget, closeTarget } from './lib/cdp.mjs';
import { runKeyboardScenario } from './lib/keyboard.mjs';
import { captureLayoutSweep, captureWideScreenshots, captureNarrowScreenshots } from './lib/layout.mjs';
import { recordWalkthroughGif } from './lib/recording.mjs';

/* ------------------------------------------------------------- main ------ */

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  const results = {
    generatedAt: new Date().toISOString(),
    title: 'Phase-4 acceptance — undercover-game panel (#2608)',
    synthetic: true,
    liveBcsEvidence: false,
    environment: {
      previewBase: PREVIEW_BASE,
      previewUrl: `${PREVIEW_BASE}${PREVIEW_PATH}`,
      wideViewport: WIDE,
      narrowViewports: [NARROW_320, NARROW_360],
      chromeProduct: null,
      headless: true,
      viewportEmulation: 'CDP Emulation.setDeviceMetricsOverride (deviceScaleFactor=1)',
      node: process.version,
      pillow: PYTHON ? 'python3 + Pillow' : 'unavailable (GIF/optimization skipped)',
      git: gitInfo(),
      syntheticData: 'visual-preview.tsx fetch mocks (six synthetic players, preview-run, word 苹果 masked by the panel)',
      liveBcsStatus: 'Not exercised. The Lead reported local BCS health responds but has 0 bots/groups at capture time; this run does not connect to BCS at all.',
    },
    commands: {
      run: 'cd apps/bcs/assets/panel && node test/undercover-game/acceptance/capture.mjs',
      prerequisites: `Vite dev server serving apps/bcs/assets/panel at ${PREVIEW_BASE} (e.g. npx vite --host 127.0.0.1 --port 4178)`,
      env: 'PREVIEW_BASE, CHROME_PATH, PYTHON, SKIP_GIF',
    },
    screenshots: [],
    layoutSweep: [],
    wideLayoutChecks: [],
    toggleEvidence: [],
    runSwitchReset: null,
    keyboardWalkthrough: [],
    privacyScan: { needles: PRIVACY_NEEDLES, pages: [], hits: [], pass: true },
    recording: null,
    errors: [],
    summary: { pass: false, failedChecks: [], notes: [] },
  };

  console.log(`[acceptance] checking preview server ${PREVIEW_BASE}${PREVIEW_PATH}`);
  const probe = await fetch(`${PREVIEW_BASE}${PREVIEW_PATH}`).catch((error) => ({ ok: false, error }));
  if (!probe.ok) {
    throw new Error(`preview page not reachable at ${PREVIEW_BASE}${PREVIEW_PATH}; start the Vite dev server first (${probe.status ?? probe.error?.message ?? 'network error'})`);
  }

  const { proc, port, userDataDir } = await launchChrome();
  console.log(`[acceptance] chrome launched, CDP port ${port}`);
  let browserProduct = null;
  try {
    const versionTarget = await createTarget(port);
    const versionCdp = new CDP(versionTarget.webSocketDebuggerUrl);
    await versionCdp.ready;
    browserProduct = (await versionCdp.send('Browser.getVersion')).product;
    versionCdp.close();
    await closeTarget(port, versionTarget.id);
  } catch { /* optional */ }
  results.environment.chromeProduct = browserProduct;

  try {
    console.log('[acceptance] layout sweep (320x600 / 360x640 x speech/vote/pk-vote/finished)');
    await captureLayoutSweep(port, results);

    console.log('[acceptance] wide screenshots (760x1000)');
    await captureWideScreenshots(port, results);

    console.log('[acceptance] narrow screenshots (320x600 / 360x640)');
    await captureNarrowScreenshots(port, results);

    console.log('[acceptance] keyboard walkthrough (real CDP key events)');
    const keyboardSpecs = [
      { scenario: 'speech', mode: 'speech', ...WIDE },
      { scenario: 'vote', mode: 'vote', ...WIDE },
      { scenario: 'abstain', mode: 'vote', ...WIDE },
      { scenario: 'pk-vote', mode: 'pk-vote', ...WIDE },
      { scenario: 'finished', mode: 'finished', ...WIDE },
      { scenario: 'narrow-speech', mode: 'speech', ...NARROW_320 },
    ];
    for (const spec of keyboardSpecs) {
      const row = await runKeyboardScenario(port, spec);
      results.keyboardWalkthrough.push(row);
      console.log(`[acceptance] keyboard ${row.scenario}: ${row.pass ? 'PASS' : 'FAIL'} (${row.steps.length} steps)`);
    }

    if (!SKIP_GIF) {
      console.log('[acceptance] recording synthetic walkthrough GIF via CDP screencast');
      try {
        results.recording = await recordWalkthroughGif(port);
      } catch (error) {
        results.recording = { status: 'failed', error: error.message, synthetic: true, liveGame: false };
      }
    } else {
      results.recording = { status: 'skipped', reason: 'SKIP_GIF=1' };
    }
  } finally {
    try { proc.kill('SIGTERM'); } catch { /* ignore */ }
    await sleep(200);
    rmSync(userDataDir, { recursive: true, force: true });
  }

  // Aggregate results.
  const privacyHits = results.privacyScan.pages.flatMap((page) => (page.hits ?? []).map((hit) => ({ file: page.file, ...hit })));
  results.privacyScan.hits = privacyHits;
  results.privacyScan.pass = privacyHits.length === 0;

  const failed = [];
  for (const row of results.layoutSweep) {
    if (!row.pass) failed.push(`layout ${row.viewport.width}x${row.viewport.height} ${row.mode}: ${row.failedChecks.join(', ')}`);
  }
  for (const row of results.wideLayoutChecks) {
    if (!row.pass) failed.push(`wide layout ${row.file} (${row.mode}): ${row.failedChecks.join(', ')}`);
  }
  for (const row of results.keyboardWalkthrough) {
    if (!row.pass) failed.push(`keyboard ${row.scenario}: ${row.steps.filter((step) => !step.pass).map((step) => step.action).join(' | ')}`);
  }
  if (!results.privacyScan.pass) failed.push(`privacy scan hits: ${privacyHits.length}`);
  for (const error of results.errors) failed.push(`capture ${error.mode} ${error.fileName}: ${error.error}`);
  if (results.recording && results.recording.status === 'failed') failed.push(`recording: ${results.recording.error}`);
  if (results.screenshots.length !== 9) failed.push(`expected 9 screenshots, got ${results.screenshots.length}`);
  for (const row of results.toggleEvidence) {
    if (!row.pass) failed.push(`workflow toggle ${row.viewport.width}x${row.viewport.height} ${row.mode}: ${Object.entries(row.checks).filter(([, value]) => !value).map(([name]) => name).join(', ')}`);
  }
  if (results.runSwitchReset && !results.runSwitchReset.pass) failed.push(`run-switch toggle reset: ${results.runSwitchReset.error ?? 'jsdom test failed'}`);

  results.summary.failedChecks = failed;
  results.summary.pass = failed.length === 0;
  results.summary.notes = [
    'Synthetic preview only: all data is served by visual-preview.tsx fetch mocks, not a live BCS game.',
    'The preview harness pads #root by 12px, so the 320x600 screenshot contains a 296x576 panel surface and the 760x1000 screenshot contains a 736x976 panel surface.',
    'The preview page clamps its internal frame height to 1000px, so 760x1000 is used instead of 760x1100 to avoid an 88px empty band.',
    'finished mode opens the real GameOverDialog directly from visual-preview.tsx; no runtime mock injection is used for captures.',
    'The 查看流程/收起流程 workflow toggle is rendered only when the board starts collapsed (narrow layout + pending action); the expanded state is local to the board and resets when the board is remounted for a new run.',
  ];

  // Corroborating unit evidence for the run-switch reset: the preview page has
  // a single fixed runId, so the browser cannot switch runs in place.
  const unitTest = path.resolve(OUT_DIR, '..', 'workflow-board.mjs');
  const panelDir = path.resolve(OUT_DIR, '..', '..', '..');
  if (existsSync(unitTest)) {
    const unit = spawnSync(process.execPath, [unitTest], { cwd: panelDir, encoding: 'utf8', timeout: 120_000 });
    const output = `${unit.stdout ?? ''}${unit.stderr ?? ''}`.trim();
    results.runSwitchReset = {
      verifiedBy: 'jsdom unit test test/undercover-game/workflow-board.mjs',
      command: 'cd apps/bcs/assets/panel && node test/undercover-game/workflow-board.mjs',
      exitCode: unit.status,
      pass: unit.status === 0,
      assertion: 'narrow action board starts collapsed, toggle expands/collapses the real nodes, and a run switch does not reuse the old expanded state',
      outputTail: output.split('\n').slice(-5).join('\n'),
      note: 'visual-preview.tsx uses one fixed runId, so the browser cannot switch runs in place; the jsdom test drives the runId prop change and asserts data-workflow-expanded=false after the switch. A fresh browser page load (also covered by the layout sweep) remounts the board and also starts collapsed.',
    };
  } else {
    results.runSwitchReset = { pass: false, error: `jsdom test not found at ${unitTest}` };
  }

  // Redact the preview's synthetic private word from the committed evidence bundle.
  results.environment.syntheticData = results.environment.syntheticData.replace('word 苹果 masked by the panel', 'private word masked by the panel');
  results.privacyScan.needles = results.privacyScan.needles.map((needle) => (needle === '苹果' ? '<synthetic-private-word>' : needle));
  const resultsPath = path.join(OUT_DIR, 'acceptance-results.json');
  writeFileSync(resultsPath, `${JSON.stringify(results, null, 2)}\n`);
  console.log(`[acceptance] wrote ${resultsPath}`);
  console.log(`[acceptance] ${results.summary.pass ? 'PASS' : 'FAIL'} — screenshots=${results.screenshots.length} failedChecks=${failed.length}`);
  for (const failure of failed) console.log(`  FAILED: ${failure}`);
  for (const shot of results.screenshots) console.log(`  ${shot.file} ${shot.width}x${shot.height} ${(shot.bytes / 1024).toFixed(1)}KB`);
  if (results.recording) console.log(`  recording: ${results.recording.status}${results.recording.bytes ? ` ${(results.recording.bytes / 1024).toFixed(1)}KB ${results.recording.gifFrames}frames` : ''}`);
  if (!results.summary.pass) process.exitCode = 1;
}

main().catch((error) => {
  console.error('[acceptance] fatal:', error);
  process.exitCode = 1;
});

/**
 * Captures the 5 undercover states of the Ops theme (and the club theme as a
 * control) into `apps/bcs/assets/panel/test/undercover-game/screenshots/`.
 *
 * Requires a running preview server (npm exec vite -- --host 127.0.0.1 --port 4178)
 * and a local Chrome binary. Use: node scripts/capture-preview.mjs
 */
import { execFileSync } from 'node:child_process';
import { mkdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const outDir = join(root, 'test', 'undercover-game', 'screenshots');
mkdirSync(outDir, { recursive: true });

const CHROME = process.env.CHROME_BIN
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = process.env.PREVIEW_BASE || 'http://127.0.0.1:4178/test/undercover-game/visual-preview.html';

const shots = [
  // [id, width, height, query]
  ['speech',     760, 760, '?theme=ops&mode=speech&width=760&height=760'],
  ['vote',       760, 760, '?theme=ops&mode=vote&width=760&height=760'],
  ['pk',         420, 700, '?theme=ops&mode=pk-vote&width=420&height=700'],
  ['eliminated', 760, 760, '?theme=ops&mode=eliminated&width=760&height=760'],
  ['finale',     760, 760, '?theme=ops&mode=finale&width=760&height=760'],
  ['narrow',     320, 620, '?theme=ops&mode=speech&width=320&height=620'],
];

if (!existsSync(CHROME)) {
  console.error(`Chrome not found at ${CHROME}; set CHROME_BIN.`);
  process.exit(1);
}

for (const [id, width, height, query] of shots) {
  const url = `${BASE}${query}`;
  const file = join(outDir, `${id}.png`);
  try {
    execFileSync(CHROME, [
      '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
      '--run-all-compositor-stages-before-draw', '--virtual-time-budget=3000',
      `--window-size=${width},${height + 40}`,
      `--screenshot=${file}`,
      url,
    ], { stdio: 'ignore', timeout: 30000 });
    console.log(`captured ${id} (${width}x${height}) -> ${file}`);
  } catch (e) {
    console.error(`failed ${id}: ${e instanceof Error ? e.message : e}`);
  }
}
console.log(`Screenshots written to ${outDir}`);
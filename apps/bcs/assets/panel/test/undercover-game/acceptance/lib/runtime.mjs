import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PREVIEW_BASE = (process.env.PREVIEW_BASE || 'http://127.0.0.1:4178').replace(/\/+$/, '');
const PREVIEW_PATH = '/test/undercover-game/visual-preview.html';
function detectPython() {
  const candidates = [process.env.PYTHON, 'python3'].filter(Boolean);
  for (const candidate of candidates) {
    const probe = spawnSync(candidate, ['-c', 'import PIL; print(PIL.__version__)'], { encoding: 'utf8' });
    if (!probe.error && probe.status === 0) return candidate;
  }
  console.warn('[acceptance] python3 with Pillow not found; GIF assembly and PNG optimization are skipped. Set PYTHON=/path/to/python3-with-pillow to enable them.');
  return null;
}
const PYTHON = detectPython();
const SKIP_GIF = process.env.SKIP_GIF === '1';

const WIDE = { width: 760, height: 1000 };
const NARROW_320 = { width: 320, height: 600 };
const NARROW_360 = { width: 360, height: 640 };
const ACTION_MODES = ['speech', 'vote', 'pk-vote'];
const PRIVACY_NEEDLES = ['苹果', 'target_actor_id', '"kind":"vote"', 'artifact_text', 'instruction'];
const SAFE_SPEECH_TEXT = '在忙碌的一天结束之后才会用到';

/**
 * `mode=finished` is self-contained in visual-preview.tsx: it serves the real
 * vote graph with a synthetic public host finale, so opening the URL directly
 * opens the real GameOverDialog. No runtime injection or business-data wrapper
 * is used by this capture script.
 */

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const round = (value) => Math.round(value * 10) / 10;

function sha256(buffer) {
  return createHash('sha256').update(buffer).digest('hex');
}

function gitInfo() {
  try {
    const branch = spawnSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'], { cwd: OUT_DIR, encoding: 'utf8' }).stdout.trim();
    const head = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: OUT_DIR, encoding: 'utf8' }).stdout.trim();
    const status = spawnSync('git', ['status', '--porcelain'], { cwd: OUT_DIR, encoding: 'utf8' }).stdout.trim();
    return { branch: branch || null, head: head || null, dirty: Boolean(status) };
  } catch {
    return { branch: null, head: null, dirty: null };
  }
}


export {
  OUT_DIR, CHROME, PREVIEW_BASE, PREVIEW_PATH, PYTHON, SKIP_GIF,
  WIDE, NARROW_320, NARROW_360, ACTION_MODES, PRIVACY_NEEDLES, SAFE_SPEECH_TEXT,
  sleep, round, sha256, gitInfo,
};

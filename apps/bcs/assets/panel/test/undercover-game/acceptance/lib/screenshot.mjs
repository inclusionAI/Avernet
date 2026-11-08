import { readFileSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { evaluate } from './cdp.mjs';
import { OUT_DIR, PYTHON, sha256 } from './runtime.mjs';

/* ------------------------------------------------------- screenshots ----- */

function screenshotPath(name) {
  return path.join(OUT_DIR, name);
}

async function capturePng(page, fileName) {
  // Visible synthetic annotation for the review images. It is not business data
  // and lives in the harness's 12px page padding below the panel surface.
  await evaluate(page.cdp, `(() => {
    let bar = document.getElementById('acceptance-synthetic-watermark');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'acceptance-synthetic-watermark';
      bar.textContent = 'SYNTHETIC PREVIEW · NOT A LIVE GAME';
      bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;height:12px;z-index:2147483647;display:flex;align-items:center;justify-content:center;background:#05070c;color:#e8b45e;font:8px/12px monospace;letter-spacing:.12em;pointer-events:none';
      document.body.appendChild(bar);
    }
  })()`);
  const screenshot = await page.cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false });
  const buffer = Buffer.from(screenshot.data, 'base64');
  const filePath = screenshotPath(fileName);
  writeFileSync(filePath, buffer);
  const optimized = optimizePngIfNeeded(filePath);
  return {
    file: fileName,
    path: `acceptance/${fileName}`,
    bytes: statSync(filePath).size,
    sha256: sha256(readFileSync(filePath)),
    optimized,
    width: page.width,
    height: page.height,
  };
}

const PNG_OPTIMIZE_SCRIPT = `
import sys
from PIL import Image
path = sys.argv[1]
image = Image.open(path).convert('RGB')
image.quantize(colors=192, method=Image.MEDIANCUT).save(path, format='PNG', optimize=True)
`;
function optimizePngIfNeeded(filePath) {
  const limit = 600 * 1024;
  if (statSync(filePath).size <= limit) return null;
  const before = statSync(filePath).size;
  if (!PYTHON) return { attempted: false, skipped: 'Pillow/python3 unavailable' };
  const result = spawnSync(PYTHON, ['-', filePath], { input: PNG_OPTIMIZE_SCRIPT, encoding: 'utf8' });
  if (result.status !== 0) return { attempted: true, error: (result.stderr || result.error?.message || 'unknown').slice(0, 300) };
  return { attempted: true, beforeBytes: before, afterBytes: statSync(filePath).size };
}


export { screenshotPath, capturePng, optimizePngIfNeeded };

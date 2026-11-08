import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { openPage, closePage, previewUrl, READY_EXPRESSION, waitFor } from './cdp.mjs';
import { pressKey } from './keyboard.mjs';
import { OUT_DIR, PYTHON, SAFE_SPEECH_TEXT, sleep, sha256 } from './runtime.mjs';

/* -------------------------------------------------------- recording ------ */

const GIF_SCRIPT = `
import json, os, sys
from PIL import Image, ImageDraw, ImageFont

frames_dir, meta_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
meta = json.load(open(meta_path, encoding='utf-8'))
label = 'SYNTHETIC PREVIEW - not a live game'
images = []
for item in meta['frames']:
    image = Image.open(os.path.join(frames_dir, item['file'])).convert('RGB')
    if image.width > 720:
        image = image.resize((720, max(1, round(image.height * 720 / image.width))), Image.LANCZOS)
    images.append([image, max(130, int(item['delayMs']))])

try:
    font = ImageFont.load_default()
except Exception:
    font = None

for entry in images:
    image = entry[0]
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        box = draw.textbbox((0, 0), label, font=font)
        text_w, text_h = box[2] - box[0], box[3] - box[1]
    except Exception:
        text_w, text_h = 6 * len(label), 11
    x = max(4, (image.width - text_w) // 2 - 6)
    y = image.height - text_h - 16
    draw.rectangle([x, y, x + text_w + 12, y + text_h + 8], fill=(0, 0, 0, 185))
    draw.text((x + 6, y + 3), label, fill=(255, 226, 150, 255), font=font)
    entry[0] = Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')

palette_frames = [entry[0].quantize(colors=128, method=Image.ADAPTIVE, dither=Image.NONE) for entry in images]
durations = [entry[1] for entry in images]
palette_frames[0].save(out_path, save_all=True, append_images=palette_frames[1:], duration=durations, loop=0, optimize=True, disposal=2)
`;

async function recordWalkthroughGif(port) {
  const viewport = { width: 360, height: 640 };
  const page = await openPage(port, { ...viewport, mode: 'speech' });
  const frames = [];
  let ackFailure = null;
  page.cdp.on('Page.screencastFrame', (params) => {
    frames.push({ data: params.data, timestamp: params.metadata?.timestamp ?? Date.now() / 1000 });
    page.cdp.send('Page.screencastFrameAck', { sessionId: params.sessionId }).catch((error) => { ackFailure = error.message; });
  });
  await page.cdp.send('Page.startScreencast', { format: 'jpeg', quality: 65, maxWidth: viewport.width, maxHeight: viewport.height, everyNthFrame: 1 });
  const navigate = async (mode) => {
    await page.cdp.send('Page.navigate', { url: previewUrl(viewport.width, viewport.height, mode) });
    await waitFor(page.cdp, READY_EXPRESSION, { label: `${mode} recording page ready` });
    await sleep(500);
  };
  const key = async (name, options) => pressKey(page.cdp, name, options);

  await navigate('speech');
  await sleep(1_000);
  // Narrow speech: Tab to 查看流程, Enter expands the real graph, Space collapses.
  for (let index = 0; index < 4; index += 1) {
    await key('Tab');
    await sleep(250);
  }
  await sleep(400);
  await key('Enter');
  await sleep(1_500);
  await key('Space');
  await sleep(1_000);
  for (let index = 0; index < 5; index += 1) {
    await key('Tab', { shift: true });
    await sleep(180);
  }
  await sleep(400);
  await page.cdp.send('Input.insertText', { text: SAFE_SPEECH_TEXT });
  await sleep(700);
  await key('Tab');
  await sleep(500);
  await key('Enter');
  await sleep(1_600);
  await navigate('vote');
  await sleep(800);
  await key('ArrowDown');
  await sleep(600);
  await key('Tab');
  await sleep(400);
  await key('Space');
  await sleep(500);
  await key('Tab');
  await sleep(400);
  await key('Enter');
  await sleep(1_600);
  await navigate('finished');
  await sleep(1_600);
  await key('Escape');
  await sleep(700);
  await key('Enter');
  await sleep(1_400);
  await page.cdp.send('Page.stopScreencast').catch(() => {});
  await sleep(200);

  // Merge consecutive identical frames, derive delays from screencast timestamps.
  const castDir = mkdtempSync(path.join(tmpdir(), 'undercover-acceptance-cast-'));
  const kept = [];
  for (let index = 0; index < frames.length; index += 1) {
    const frame = frames[index];
    const nextTimestamp = frames[index + 1]?.timestamp ?? frame.timestamp + 0.35;
    const delay = Math.min(800, Math.max(130, (nextTimestamp - frame.timestamp) * 1000));
    const buffer = Buffer.from(frame.data, 'base64');
    const hash = sha256(buffer);
    if (kept.length && kept[kept.length - 1].hash === hash) {
      kept[kept.length - 1].delayMs += delay;
      continue;
    }
    const file = `frame-${String(kept.length).padStart(4, '0')}.jpg`;
    writeFileSync(path.join(castDir, file), buffer);
    kept.push({ file, hash, delayMs: delay, bytes: buffer.length });
  }
  // Downsample to keep playback and file size reasonable: <=30000ms total.
  let totalMs = kept.reduce((sum, frame) => sum + frame.delayMs, 0);
  while (totalMs > 30_000 && kept.length > 24) {
    const trimmed = kept.filter((_, index) => index % 2 === 0);
    totalMs = trimmed.reduce((sum, frame) => sum + frame.delayMs, 0);
    kept.length = 0;
    kept.push(...trimmed);
  }
  const metaPath = path.join(castDir, 'meta.json');
  writeFileSync(metaPath, JSON.stringify({ frames: kept.map(({ file, delayMs }) => ({ file, delayMs })) }));
  const gifPath = path.join(OUT_DIR, 'walkthrough-synthetic.gif');
  await closePage(port, page);
  if (!PYTHON) return { status: 'skipped', reason: 'python3 with Pillow not available; screenshots and results were still generated', frames: frames.length, ackFailure };
  const python = spawnSync(PYTHON, ['-', castDir, metaPath, gifPath], { input: GIF_SCRIPT, encoding: 'utf8' });
  if (python.status !== 0) {
    return { status: 'failed', error: (python.stderr || python.error?.message || 'Pillow GIF assembly failed').slice(0, 500), frames: frames.length, ackFailure };
  }
  rmSync(castDir, { recursive: true, force: true });
  const actualFps = kept.length / (totalMs / 1000);
  return {
    status: 'assembled',
    file: 'walkthrough-synthetic.gif',
    method: 'CDP Page.startScreencast (JPEG), frames assembled with Pillow',
    capturedFrames: frames.length,
    gifFrames: kept.length,
    width: viewport.width,
    totalDurationMs: totalMs,
    maxFps: 8,
    effectiveFps: Math.round(actualFps * 10) / 10,
    bytes: statSync(gifPath).size,
    label: 'SYNTHETIC PREVIEW - not a live game (bottom watermark on every frame)',
    synthetic: true,
    liveGame: false,
    ackFailure,
  };
}


export { recordWalkthroughGif };

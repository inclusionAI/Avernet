import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { CHROME, PREVIEW_BASE, PREVIEW_PATH, sleep } from './runtime.mjs';

/* ------------------------------------------------------------------ CDP -- */

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = (event) => reject(new Error(`CDP WebSocket error: ${event?.message ?? 'unknown'}`));
    });
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(`CDP ${message.error.message} (${message.error.code})`));
        else resolve(message.result);
        return;
      }
      if (message.method) {
        for (const listener of this.listeners.get(message.method) ?? []) listener(message.params);
      }
    };
  }

  on(method, listener) {
    const list = this.listeners.get(method) ?? [];
    list.push(listener);
    this.listeners.set(method, list);
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.nextId;
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  close() {
    try { this.ws.close(); } catch { /* ignore */ }
  }
}

async function launchChrome() {
  if (!existsSync(CHROME)) throw new Error(`Chrome not found at ${CHROME}`);
  const userDataDir = mkdtempSync(path.join(tmpdir(), 'undercover-acceptance-chrome-'));
  const proc = spawn(CHROME, [
    '--headless=new', '--no-sandbox', '--disable-gpu-sandbox', '--disable-dev-shm-usage',
    '--disable-gpu', '--mute-audio', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', `--user-data-dir=${userDataDir}`, 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore'] });
  const portFile = path.join(userDataDir, 'DevToolsActivePort');
  const deadline = Date.now() + 20_000;
  while (!existsSync(portFile)) {
    if (Date.now() > deadline) throw new Error(`Chrome DevToolsActivePort did not appear (${portFile})`);
    if (proc.exitCode !== null) throw new Error(`Chrome exited early with code ${proc.exitCode}`);
    await sleep(100);
  }
  await sleep(150);
  const port = Number(readFileSync(portFile, 'utf8').split('\n')[0].trim());
  if (!port) throw new Error('Chrome DevToolsActivePort has no port');
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) break;
    } catch { /* retry */ }
    await sleep(100);
  }
  return { proc, port, userDataDir };
}

async function createTarget(port) {
  const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' });
  if (!response.ok) throw new Error(`/json/new failed: ${response.status}`);
  return response.json();
}

async function closeTarget(port, targetId) {
  try { await fetch(`http://127.0.0.1:${port}/json/close/${targetId}`); } catch { /* ignore */ }
}

async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) {
    const detail = result.exceptionDetails.exception?.description || result.exceptionDetails.text;
    throw new Error(`page evaluate failed: ${detail}`);
  }
  return result.result?.value;
}

async function waitFor(cdp, expression, { timeout = 25_000, interval = 100, label = 'condition' } = {}) {
  const deadline = Date.now() + timeout;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      if (await evaluate(cdp, expression)) return;
    } catch (error) { lastError = error; }
    await sleep(interval);
  }
  throw new Error(`timeout waiting for ${label}${lastError ? ` (${lastError.message})` : ''}`);
}

const READY_EXPRESSION = `(() => {
  const panel = document.querySelector('[data-panel-viewport]');
  const dock = document.querySelector('[data-region="action-dock"]');
  const board = document.querySelector('[data-region="workflow-board"]');
  const loading = document.body.innerText.includes('正在加载游戏房间');
  return document.readyState === 'complete' && !!panel && !!dock && !!board && !loading;
})()`;

function previewUrl(width, height, mode) {
  const url = new URL(`${PREVIEW_BASE}${PREVIEW_PATH}`);
  url.searchParams.set('width', String(width));
  url.searchParams.set('height', String(height));
  url.searchParams.set('mode', mode);
  return url.toString();
}

function isIgnoredConsoleNoise(entry) {
  const text = String(entry.text ?? '');
  const url = String(entry.url ?? '');
  if (url.includes('/favicon.ico') || text.includes('/favicon.ico')) return true;
  // React DevTools suggestion is an info message, not an app error.
  if (/React DevTools/i.test(text)) return true;
  return false;
}

/**
 * Open one fresh target for a preview scenario. A fresh target per scenario is
 * deliberate: visual-preview.tsx keeps a module-level `submitted` flag, so a
 * reused page would leak state between captures.
 */
async function openPage(port, { width, height, mode }) {
  const target = await createTarget(port);
  const cdp = new CDP(target.webSocketDebuggerUrl);
  await cdp.ready;
  const consoleEntries = [];
  const rawConsoleEntries = [];
  const record = (kind, payload) => {
    const entry = { kind, ...payload };
    rawConsoleEntries.push(entry);
    if (!isIgnoredConsoleNoise(entry)) consoleEntries.push(entry);
  };
  cdp.on('Runtime.exceptionThrown', (params) => record('exception', {
    text: params.exceptionDetails?.exception?.description || params.exceptionDetails?.text,
  }));
  cdp.on('Runtime.consoleAPICalled', (params) => {
    if (params.type !== 'error' && params.type !== 'assert') return;
    record('console', { text: params.args.map((arg) => arg.value ?? arg.description ?? '').join(' ') });
  });
  cdp.on('Log.entryAdded', (params) => {
    if (params.entry.level !== 'error') return;
    record('log', { text: params.entry.text, url: params.entry.url });
  });
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
  const url = previewUrl(width, height, mode);
  await cdp.send('Page.navigate', { url });
  await waitFor(cdp, READY_EXPRESSION, { label: `${mode} panel ready at ${width}x${height}` });
  await sleep(250);
  return { cdp, target, consoleEntries, rawConsoleEntries, url, width, height, mode };
}

async function closePage(port, page) {
  page.cdp.close();
  await closeTarget(port, page.target.id);
}


export { CDP, launchChrome, createTarget, closeTarget, evaluate, waitFor, READY_EXPRESSION, previewUrl, isIgnoredConsoleNoise, openPage, closePage };

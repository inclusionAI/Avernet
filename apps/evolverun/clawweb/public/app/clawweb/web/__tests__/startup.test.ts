// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement, lazy, StrictMode, Suspense } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The same behavioral contract is exercised in both independently shipped hosts.
const hostRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const html = readFileSync(resolve(hostRoot, 'index.html'), 'utf8');
let root: Root | undefined;
let commit: typeof import('../startup').StartupCommit;
let guardWindow: Window & { __clawwebStartup: { commit(): void } };
let reload: ReturnType<typeof vi.fn>;
let listeners: Map<string, Array<(event: Event) => void>>;

function emit(type: string, target: EventTarget = guardWindow) {
  const event = new Event(type, { cancelable: true });
  Object.defineProperty(event, 'target', { value: target });
  for (const listener of listeners.get(type) ?? []) listener(event);
  return event;
}

function status() { return document.getElementById('clawweb-startup')!; }
function message() { return document.getElementById('clawweb-startup-message')!.textContent!; }
function button() { return document.getElementById('clawweb-startup-reload') as HTMLButtonElement; }

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  document.head.replaceChildren(...Array.from(parsed.head.childNodes));
  document.body.replaceChildren(...Array.from(parsed.body.childNodes));
  listeners = new Map();
  reload = vi.fn();
  guardWindow = {
    location: { reload },
    setTimeout, clearTimeout,
    addEventListener(type: string, listener: (event: Event) => void) {
      listeners.set(type, [...(listeners.get(type) ?? []), listener]);
    },
  } as unknown as typeof guardWindow;
  const script = document.getElementById('clawweb-startup-guard');
  // Execute the shipped classic script, without evaluating the module entry.
  runInNewContext(script?.textContent ?? '', { window: guardWindow, document });
  window.__clawwebStartup = guardWindow.__clawwebStartup;
});

afterEach(async () => {
  if (root) await act(() => { root!.unmount(); });
  root = undefined;
  delete window.__clawwebStartup;
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('independent HTML startup guard', () => {
  it('keeps the root marker, accessible visible placeholder and native noscript outside React', () => {
    expect(html).toContain('<div id="root"></div>');
    expect(status().parentElement).toBe(document.body);
    expect(status().hidden).toBe(false);
    expect(status().getAttribute('role')).toBe('status');
    expect(status().getAttribute('aria-live')).toBe('polite');
    expect(status().getAttribute('aria-atomic')).toBe('true');
    expect(document.getElementById(status().getAttribute('aria-labelledby')!)).not.toBeNull();
    expect(button().type).toBe('button');
    expect(button().textContent).toBe('重新加载页面');
    expect(document.getElementById('clawweb-startup-title')!.textContent).toBe('正在启动 ClawWeb…');
    expect(button().hidden).toBe(true);
    expect(html).toMatch(/<noscript>[\s\S]*JavaScript[\s\S]*<\/noscript>/);
    const script = document.getElementById('clawweb-startup-guard') as HTMLScriptElement;
    expect(script.type).not.toBe('module');
    expect(script.src).toBe('');
    expect(html.indexOf(script.outerHTML)).toBeLessThan(html.indexOf('<script type="module"'));
  });

  it('captures failure before the body is parsed, independently of head module/style requests', () => {
    const markup = Array.from(document.body.childNodes, node => node.cloneNode(true));
    document.body.replaceChildren();
    vi.clearAllTimers();
    listeners.clear();
    const script = document.getElementById('clawweb-startup-guard')!;
    expect(script.parentElement).toBe(document.head);
    vi.spyOn(document, 'readyState', 'get').mockReturnValue('loading');
    runInNewContext(script.textContent!, { window: guardWindow, document });
    const entry = document.createElement('script');
    entry.type = 'module';
    emit('error', entry);
    document.body.append(...markup);
    // The parser is done, but a different deferred request may still block DOMContentLoaded.
    document.dispatchEvent(new Event('readystatechange'));
    expect(button().hidden).toBe(false);
    emit('DOMContentLoaded');
    expect(status().hidden).toBe(false);
    expect(status().getAttribute('role')).toBe('alert');
    expect(button().hidden).toBe(false);
    expect(reload).not.toHaveBeenCalled();
    button().click();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it.each(['entry', 'dependency', 'stylesheet', 'evaluation', 'rejection'])('shows manual recovery when %s fails before main can commit', (kind) => {
    let target: EventTarget = guardWindow;
    if (kind === 'entry') {
      const script = document.createElement('script');
      script.type = 'module';
      target = script;
    } else if (kind === 'dependency' || kind === 'stylesheet') {
      const link = document.createElement('link');
      link.rel = kind === 'dependency' ? 'modulepreload' : 'stylesheet';
      target = link;
    }
    emit(kind === 'rejection' ? 'unhandledrejection' : 'error', target);
    expect(status().hidden).toBe(false);
    expect(status().getAttribute('role')).toBe('alert');
    expect(button().hidden).toBe(false);
    expect(message()).toMatch(/加载|启动/);
    vi.runAllTimers();
    expect(reload).not.toHaveBeenCalled();
    button().click();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('ignores failed images and icons rather than diagnosing unrelated resource errors', () => {
    emit('error', document.createElement('img'));
    const icon = document.createElement('link');
    icon.rel = 'icon';
    emit('error', icon);
    expect(status().getAttribute('role')).toBe('status');
    expect(button().hidden).toBe(true);
  });

  it('bounds startup waiting without claiming a version mismatch, and allows a late commit', () => {
    vi.advanceTimersByTime(19_999);
    expect(button().hidden).toBe(true);
    vi.advanceTimersByTime(1);
    expect(status().getAttribute('role')).toBe('alert');
    expect(message()).toMatch(/耗时比预期更长/);
    expect(message()).not.toMatch(/版本|过期|version|outdated|stale/i);
    expect(button().hidden).toBe(false);
    expect(reload).not.toHaveBeenCalled();
    guardWindow.__clawwebStartup.commit();
    expect(status().hidden).toBe(true);
  });

  it('keeps loading after module evaluation, then hides only after a real React commit (including StrictMode)', async () => {
    ({ StartupCommit: commit } = await import('../startup'));
    expect(status().hidden).toBe(false);
    root = createRoot(document.getElementById('root')!);
    await act(() => { root!.render(createElement(StrictMode, null, createElement(commit))); });
    expect(status().hidden).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(120_000);
    expect(status().hidden).toBe(true);
    expect(reload).not.toHaveBeenCalled();
  });

  it('does not mark an uncommitted suspended tree successful', async () => {
    ({ StartupCommit: commit } = await import('../startup'));
    const Waiting = lazy(() => new Promise<never>(() => {}));
    root = createRoot(document.getElementById('root')!);
    await act(() => {
      root!.render(createElement(Suspense, { fallback: 'Waiting' }, createElement(commit), createElement(Waiting)));
    });
    expect(status().hidden).toBe(false);
    vi.advanceTimersByTime(20_000);
    expect(button().hidden).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it('leaves post-commit ordinary errors to the existing React error handling', () => {
    guardWindow.__clawwebStartup.commit();
    emit('error');
    emit('unhandledrejection');
    vi.runAllTimers();
    expect(status().hidden).toBe(true);
  });

  it.each([false, true])('keeps repeated lazy failures visible without reload loops (committed: %s)', (committed) => {
    if (committed) guardWindow.__clawwebStartup.commit();
    const app = document.getElementById('root')!;
    const existing = document.createElement('p');
    existing.textContent = 'Existing error boundary or unsaved work';
    app.append(existing);
    for (let i = 0; i < 3; i++) {
      const event = emit('vite:preloadError');
      expect(event.defaultPrevented).toBe(false); // Vite must still throw to React's boundary.
      guardWindow.__clawwebStartup.commit(); // A delayed effect cannot hide a chunk failure.
      expect(status().hidden).toBe(false);
      expect(status().getAttribute('role')).toBe('alert');
      expect(button().hidden).toBe(false);
      expect(message()).toMatch(/重新加载/);
      vi.runAllTimers();
    }
    expect(app.contains(existing)).toBe(true);
    expect(reload).not.toHaveBeenCalled();
    button().click();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('wires the commit marker inside the host error boundary without changing the root mount', () => {
    const main = readFileSync(resolve(hostRoot, 'web/main.tsx'), 'utf8');
    expect(main).toContain('<StartupCommit />');
    expect(main.indexOf('<StartupCommit />')).toBeGreaterThan(main.indexOf('<StartupErrorBoundary>'));
    expect(main.indexOf('<StartupCommit />')).toBeLessThan(main.indexOf('</StartupErrorBoundary>'));
    expect(main).toMatch(/createRoot\(document\.getElementById\(['"]root['"]\)!\)\.render/);
  });
});

it('the public boundary catches a rejected lazy page with accessible manual recovery', async () => {
  const { StartupErrorBoundary } = await import('../startup');
  guardWindow.__clawwebStartup.commit();
  const Broken = lazy(() => {
    emit('vite:preloadError');
    return Promise.reject(new Error('Private error detail must not be rendered'));
  });
  vi.spyOn(console, 'error').mockImplementation(() => {});
  root = createRoot(document.getElementById('root')!);
  await act(async () => {
    root!.render(createElement(StartupErrorBoundary, null,
      createElement(Suspense, { fallback: 'Loading' }, createElement(Broken))));
  });
  const alert = document.querySelector('#root [role="alert"]');
  expect(alert?.textContent).toMatch(/重新加载/);
  expect(alert?.textContent).not.toContain('Private error detail');
  const recovery = alert?.querySelector('button');
  expect(recovery?.type).toBe('button');
  expect(status().hidden).toBe(false);
  expect(button().hidden).toBe(false);
  vi.runAllTimers();
  expect(reload).not.toHaveBeenCalled();
  vi.stubGlobal('window', new Proxy(window, {
    get(target, key) {
      return key === 'location' ? { reload } : Reflect.get(target, key, target);
    },
  }));
  recovery!.click();
  expect(reload).toHaveBeenCalledTimes(1);
});

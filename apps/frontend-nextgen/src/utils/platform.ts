export type Platform = 'web' | 'electron' | 'dingtalk' | 'vscode';

declare global {
  interface Window {
    electronAPI?: unknown;
    vscodeApi?: unknown;
    acquireVsCodeApi?: () => unknown;
  }
}

function getWindow(): Window | undefined {
  return typeof window === 'undefined' ? undefined : window;
}

function getUserAgent(): string {
  const win = getWindow();
  return win?.navigator?.userAgent ?? '';
}

export function isElectron(): boolean {
  const win = getWindow();
  return Boolean(win?.electronAPI) || /electron/i.test(getUserAgent());
}

export function isDingTalk(): boolean {
  const win = getWindow();
  const href = win?.location?.href ?? '';
  return /dingtalk/i.test(getUserAgent()) || /[?&]ddtab=|[?&]dingtalk=/.test(href);
}

export function isVSCode(): boolean {
  const win = getWindow();
  const href = win?.location?.href ?? '';
  return Boolean(win?.vscodeApi || win?.acquireVsCodeApi) || /[?&]source=vscode/.test(href);
}

export function isChromeBrowser(): boolean {
  const ua = getUserAgent();
  return /chrome|chromium/i.test(ua) && !/edg\//i.test(ua);
}

export function getPlatform(): Platform {
  if (isElectron()) return 'electron';
  if (isVSCode()) return 'vscode';
  if (isDingTalk()) return 'dingtalk';
  return 'web';
}

export function isWeb(): boolean {
  return getPlatform() === 'web';
}

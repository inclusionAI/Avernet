/**
 * 平台/渠道判断工具
 * 用于区分 Web、Electron、DingTalk 等不同运行环境
 */
import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';

let _vscodeApi: { postMessage: (msg: any) => void } | null = null;

/**
 * 判断是否为 VSCode 扩展环境
 * 检测条件（满足任一即可）:
 * 1. iframe 模式：在 iframe 中运行 + URL 参数 source=vscode
 * 2. webview 模式：VSCode webview 面板，存在 acquireVsCodeApi 或 window.__vscodeApi
 */
export function isVSCode(): boolean {
  // webview 面板：已被注入脚本挂载到 window 上的 API 实例
  if ((window as any).__vscodeApi) {
    return true;
  }

  // webview 面板：VSCode 注入的全局 API（尚未被调用过）
  if (typeof (window as any).acquireVsCodeApi === 'function') {
    return true;
  }

  // 已通过 getVSCodeApi() 缓存的实例
  if (_vscodeApi) {
    return true;
  }

  // iframe 模式：嵌入在 VSCode webview 的 iframe 中
  const isInIframe = window.parent !== window;
  const searchParams = new URLSearchParams(window.location.search);
  const source = searchParams.get('source');

  return isInIframe && source === 'vscode';
}

/**
 * 判断是否为 Web 端（非 Electron、非 DingTalk、非 VSCode）
 */

export function getVSCodeApi(): { postMessage: (msg: any) => void } | null {
  if (_vscodeApi) return _vscodeApi;

  // 优先从 window 上读取（注入脚本已调用过 acquireVsCodeApi 并挂载）
  if ((window as any).__vscodeApi) {
    _vscodeApi = (window as any).__vscodeApi;
    return _vscodeApi;
  }

  // 兜底：自行调用（仅在注入脚本未执行时才走到这里）
  if (typeof (window as any).acquireVsCodeApi === 'function') {
    _vscodeApi = (window as any).acquireVsCodeApi();
    return _vscodeApi;
  }

  return null;
}

/**
 * 向 VSCode 扩展宿主发送 postMessage
 * 自动选择 webview API（面板模式）或 window.parent.postMessage（iframe 模式）
 */
export function postMessageToVSCode(message: any): void {
  const api = getVSCodeApi();
  if (api) {
    api.postMessage(message);
  } else {
    window.parent.postMessage(message, '*');
  }
}

/**
 * 判断是否为 Electron 桌面端
 */
export function isElectron(): boolean {
  return Boolean((window as any).ELECTRON_ENV?.isElectron);
}

/**
 * 判断是否为 DingTalk 环境
 */
export function isDingTalk(): boolean {
  const searchParams = new URLSearchParams(window.location.search);
  return Boolean(searchParams.get('ddtab'));
}

/**
 * 判断是否为 Web 端（非 Electron、非 DingTalk、非 VSCode）
 */
export function isWeb(): boolean {
  return !isElectron() && !isDingTalk() && !isVSCode();
}

/**
 * 判断是否为本地开发环境
 * 真实判断经 AppExt.env 注入：开源默认恒 false（无本地开发 SSO 旁路），
 * 内部 extend 读 window.__TERN__._localDev。
 */
export function isLocalDev(): boolean {
  return getExt(AppExt).env.isLocalDev();
}

/**
 * 判断是否为 BCN 专属域名
 * 该域名只允许访问 /bcn 相关页面。
 * 真实 hostname 判断经 AppExt.bcnDomain 注入：开源默认恒 false（无门禁，全放行），
 * 内部 extend 走 BCN 专属域名的等值判断。
 */
export function isBcnDomain(): boolean {
  return getExt(AppExt).bcnDomain.isBcnHostname(window.location.hostname);
}

/**
 * 获取当前平台类型
 */
export function getPlatform(): 'electron' | 'dingtalk' | 'web' {
  if (isElectron()) return 'electron';
  if (isDingTalk()) return 'dingtalk';
  return 'web';
}

/**
 * 获取 Electron 环境信息
 */
export function getElectronEnv() {
  return (window as any).ELECTRON_ENV || null;
}

/**
 * 判断是否为 Chrome 内核浏览器
 * 桌面版 Bot 需要使用 Chrome/Edge 等 Chromium 内核浏览器以获得最佳体验
 */
export function isChromeBrowser(): boolean {
  const userAgent = navigator.userAgent.toLowerCase();
  // 检测 Chrome 浏览器（包含 Edge，因为 Edge 也是 Chromium 内核）
  const isChrome = /chrome/.test(userAgent);
  // 也检测 Chromium
  const isChromium = /chromium/.test(userAgent);
  return isChrome || isChromium;
}

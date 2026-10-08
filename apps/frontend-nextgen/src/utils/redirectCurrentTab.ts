/**
 * 当前标签页硬跳转(平台级,非 umi router)。供 useGatewayLoginRedirect 调用,作为测试可 mock 的接缝
 * (jsdom 的 window.location 不可 spy/重定义,直接 inline 调用难以单测)。
 * SSR/非浏览器或 location.replace 缺失时静默 no-op,不抛错。
 */
export function redirectCurrentTab(url: string): void {
  if (typeof window === 'undefined') return;
  if (typeof window.location?.replace !== 'function') return;
  window.location.replace(url);
}

/**
 * 当前标签页导航到外部登录 / provider 授权页（`href`，非 `replace`）。供 hook（useExternalAuth.login）
 * 调用，作为 jsdom 可 mock 接缝。非浏览器 / location 缺失时静默 no-op。
 */
export function navigateToUrl(url: string): void {
  if (typeof window === 'undefined') return;
  if (typeof window.location === 'undefined') return;
  window.location.href = url;
}

/**
 * 当前标签页刷新。供 hook（useExternalAuth.logout）调用，作为 jsdom 可 mock 接缝。
 * 非浏览器 / reload 缺失时静默 no-op。
 */
export function reloadCurrentTab(): void {
  if (typeof window === 'undefined') return;
  if (typeof window.location?.reload !== 'function') return;
  window.location.reload();
}

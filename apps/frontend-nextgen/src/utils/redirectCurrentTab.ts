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

/**
 * 新标签页打开重登链接，返回窗口引用（真被弹窗拦截时为 null，调用方据此提示与降级）。
 * 供重登编排 hook 在用户手势内调用，作为 jsdom 可 mock 接缝（ace-gateway-popup-reauth D4）。
 *
 * ⚠️ 不用 `window.open(url, '_blank', 'noopener,noreferrer')`：features 带 `noopener` 时按
 * HTML 规范返回 null（Chrome/Firefox 已实装）——句柄永远拿不到，真拦截与正常打开不可区分
 * （曾因此误弹「登录窗口被浏览器拦截」提示，真机回归 #2）。改用等价手法：普通打开取引用，
 * 随后立即 `opener = null` 切断反向引用（reverse-tabnabbing 面与 noopener 等价收敛，断开
 * 越早窗口越小）。
 */
export function openLoginInNewTab(url: string): Window | null {
  if (typeof window === 'undefined') return null;
  if (typeof window.open !== 'function') return null;
  const popup = window.open(url, '_blank');
  if (popup) {
    try {
      popup.opener = null;
    } catch {
      // 个别环境禁止跨域写 opener：静默放弃断开（登录域为公司内网可信域），句柄照常返回。
    }
  }
  return popup;
}

/** 聚焦已存在的重登窗口；窗口已不可访问（跨域/已关闭）时返回 false。 */
export function focusLoginTab(target: Window): boolean {
  try {
    if (target.closed) return false;
    target.focus();
    return true;
  } catch {
    return false;
  }
}

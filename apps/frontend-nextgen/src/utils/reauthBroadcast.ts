/**
 * 重登恢复广播接缝（ace-gateway-popup-reauth D4 快路径）。
 *
 * 背景：ACE pubLogin 登录完成后回跳 `goto`（= 被拦截请求的 Referer 回声，实测落回本站 SPA 业务页），
 * 新标签页会完整 boot 一个同源 SPA 实例——广播在启动时无条件发出，不需要跨域登录页配合、
 * 不依赖 window.opener（打开侧句柄一经取得即 `opener = null` 断开反向引用，见
 * `redirectCurrentTab.openLoginInNewTab` 与 design D3/D4/D7）。
 * 原标签页只在「等待重登」状态受理该消息，受理后立即探活；普通用户新开的 tab 同样广播，
 * 但非等待状态的 tab 忽略之，零副作用。
 *
 * BroadcastChannel 为同源通道（可达性以同源 SPA 为前提，与 goto 回跳落点一致）。运行环境不支持时
 * 两个接缝均静默 no-op——仅损失秒级快路径，轮询探活主干不受影响（见 design 风险表）。
 *
 * 接缝化目的同 `redirectCurrentTab`：jsdom 下可 mock，不直连全局。
 */

/** 频道与消息名固定（版本后缀随 change 名，避免与站点内其他广播串台）。 */
const REAUTH_BROADCAST_CHANNEL = 'teamclaw-reauth';
const REAUTH_RETURNED_MESSAGE = 'auth-returned';

/**
 * 广播「本 SPA 实例已启动」。每个 tab 的 SPA boot 无条件调用一次——登录回流标签页借此
 * 唤醒原页面的快路径感知；普通新开标签页的广播被非等待状态的原页面忽略。
 */
export function broadcastAuthReturned(): void {
  if (typeof BroadcastChannel === 'undefined') return;
  try {
    const channel = new BroadcastChannel(REAUTH_BROADCAST_CHANNEL);
    channel.postMessage(REAUTH_RETURNED_MESSAGE);
    channel.close();
  } catch {
    // 广播失败不影响任何主流程（轮询主干兜底）。
  }
}

/**
 * 监听重登回流广播。返回退订函数。调用方自行做「等待重登状态」门控——本接缝只做通道收发。
 */
export function listenAuthReturned(handler: () => void): () => void {
  if (typeof BroadcastChannel === 'undefined') return () => {};
  try {
    const channel = new BroadcastChannel(REAUTH_BROADCAST_CHANNEL);
    channel.onmessage = (event: MessageEvent) => {
      if (event.data === REAUTH_RETURNED_MESSAGE) handler();
    };
    return () => {
      channel.close();
    };
  } catch {
    return () => {};
  }
}

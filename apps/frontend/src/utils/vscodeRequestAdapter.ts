/**
 * VSCode postMessage 请求适配器
 *
 * 在 VSCode 扩展环境下，将 HTTP 请求通过 postMessage 转发给 VSCode 扩展宿主，
 * 由扩展宿主代理发出实际 HTTP 请求后将响应回传。
 *
 * 消息协议：
 * Web → VSCode: { source: 'web', type: 'api-request', requestId, url, method, headers, data, params }
 * VSCode → Web:  { type: 'api-response', requestId, status, statusText, data, headers }
 * VSCode → Web:  { type: 'api-request-error', requestId, message }
 */

import { postMessageToVSCode } from './platform';

/** 请求超时时间（ms） */
const REQUEST_TIMEOUT = 30_000;

/** 递增 ID 计数器 */
let requestIdCounter = 0;

/** 待处理请求的 resolve/reject 映射 */
const pendingRequests = new Map<
  string,
  {
    resolve: (value: any) => void;
    reject: (reason: any) => void;
    timer: ReturnType<typeof setTimeout>;
  }
>();

/** 是否已初始化消息监听 */
let listenerInitialized = false;

/**
 * 初始化 postMessage 响应监听器（仅执行一次）
 */
function ensureListener(): void {
  if (listenerInitialized) return;
  listenerInitialized = true;

  window.addEventListener('message', (event: MessageEvent) => {
    const msg = event.data;
    const { type, requestId } = msg || {};

    if (type === 'api-response' || type === 'api-request-error') {
      const pending = pendingRequests.get(requestId);
      if (!pending) return;

      clearTimeout(pending.timer);
      pendingRequests.delete(requestId);

      if (type === 'api-response') {
        const { status, statusText, data, headers } = msg;
        pending.resolve({
          data,
          status,
          statusText: statusText || '',
          headers: headers || {},
          config: {},
        });
      } else {
        pending.reject(new Error(msg.message || '请求失败'));
      }
    }
  });
}

/**
 * Axios 自定义适配器：通过 postMessage 发送请求
 *
 * 用法：在 requestInterceptor 中设置 config.adapter = vscodeRequestAdapter
 */
export function vscodeRequestAdapter(config: any): Promise<any> {
  ensureListener();

  const requestId = `vscode-req-${++requestIdCounter}-${Date.now()}`;

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingRequests.delete(requestId);
      reject(new Error(`[VSCode] 请求超时: ${config.url}`));
    }, REQUEST_TIMEOUT);

    pendingRequests.set(requestId, { resolve, reject, timer });

    postMessageToVSCode({
      source: 'web',
      type: 'api-request',
      requestId,
      url: config.url,
      method: (config.method || 'GET').toUpperCase(),
      headers: config.headers || {},
      data: config.data,
      params: config.params,
    });
  });
}

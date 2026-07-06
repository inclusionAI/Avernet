/**
 * Bootstrap 工具函数
 */
import React from 'react';
import ReactDOM from 'react-dom/client';

/**
 * 检查并移除单个 loading 元素
 */
function hideLoadingElement(el: Element) {
  const htmlEl = el as HTMLElement;
  htmlEl.style.display = 'none';
  htmlEl.style.visibility = 'hidden';
  htmlEl.setAttribute('data-bigfish-loading-hidden', 'true');
}

/**
 * 检查元素是否是 loading 组件
 */
function isLoadingElement(el: Element): boolean {
  // 通过 data-testid
  if (el.getAttribute('data-testid') === 'bigfish-initial-loading') {
    return true;
  }
  // 通过特征：包含 "加载中" 且是 fixed 定位
  const htmlEl = el as HTMLElement;
  if (
    el.textContent?.includes('加载中') &&
    (el.classList?.contains('fixed') || htmlEl.style?.position === 'fixed')
  ) {
    return true;
  }
  return false;
}

/**
 * 移除 bigfish 初始状态 loading 组件
 */
function removeBigfishLoading(observer?: MutationObserver) {
  // 查找并隐藏所有 loading 元素
  const allElements = document.body.querySelectorAll('*');
  let foundCount = 0;
  allElements.forEach((el) => {
    if (isLoadingElement(el)) {
      hideLoadingElement(el);
      foundCount++;
    }
  });

  // 如果找到了 loading 元素，停止观察（减少性能开销）
  if (foundCount > 0 && observer) {
    observer.disconnect();
  }

  return foundCount;
}

/**
 * 创建并挂载 React 组件到 DOM
 * 挂载前会移除 bigfish 的初始状态 loading 组件
 */
export function mountComponent(component: React.ReactElement) {
  // 创建容器并挂载组件
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  root.render(component);

  // 使用 MutationObserver 监听新添加的 loading 元素
  const observer = new MutationObserver((mutations) => {
    let shouldCheck = false;
    for (const mutation of mutations) {
      if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
        shouldCheck = true;
        break;
      }
    }
    if (shouldCheck) {
      removeBigfishLoading(observer);
    }
  });

  // 开始监听 body 的变化
  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });

  // 立即尝试移除（可能已经存在）
  removeBigfishLoading(observer);

  // 延迟再次尝试（确保 bigfish 异步渲染的 loading 被移除）
  setTimeout(() => removeBigfishLoading(observer), 0);
  setTimeout(() => removeBigfishLoading(observer), 100);
  setTimeout(() => removeBigfishLoading(observer), 500);

  // 5 秒后停止观察
  setTimeout(() => {
    observer.disconnect();
  }, 5000);

  return { container, root };
}

/**
 * 卸载 React 组件并移除 DOM 节点
 */
export function unmountComponent(
  container: HTMLDivElement,
  root: ReactDOM.Root,
) {
  root.unmount();
  if (container.parentNode) {
    container.parentNode.removeChild(container);
  }
}

/**
 * 获取 URL 参数（支持大小写不敏感）
 * @param key 参数名（不区分大小写）
 * @returns 参数值或 null
 *
 * @example
 * // URL: ?skipBrain=true
 * getSearchParam('skipBrain')  // 'true'
 * getSearchParam('skipbrain')  // 'true'
 * getSearchParam('SKIPBRAIN')  // 'true'
 */
export function getSearchParam(key: string): string | null {
  const params = new URLSearchParams(window.location.search);
  const lowerKey = key.toLowerCase();

  // 遍历所有参数，找到大小写不敏感匹配的参数
  for (const [paramKey, paramValue] of params.entries()) {
    if (paramKey.toLowerCase() === lowerKey) {
      return paramValue;
    }
  }

  return null;
}

import { useEffect, type RefObject } from 'react';

/**
 * useStickToBottom —— 聊天列表「贴底跟随」。
 *
 * 背景（bug 根因）：@tc-chat/ui BubbleList（单聊 ChatLayout.List 与群聊共用内核）的
 * isStreaming 贴底 effect 依赖只有 [isStreaming, messages.length]：
 * - bot 流式输出时只有最后一条消息内容增长（scrollHeight 变大）而条数不变 → 不重跑；
 * - 流式结束后的最终渲染（操作栏出现、markdown 终态、图片异步加载）发生在
 *   isRequesting 翻转之后 → 同样不跟随；
 * - 输入框区域增高挤压列表（滚动容器 clientHeight 变小）不产生任何消息变化 → 视口
 *   停在原 scrollTop，最新一行落到输入框后面。
 * 库为外部 npm 包无法修改，这里在应用层补齐。
 *
 * 行为约定（与 BubbleList atBottomThreshold 语义一致，标准 chat「follow output」交互）：
 * - 用户在底部附近（< 96px）时，任何内容增高都保持贴底：DOM 变化（流式 token）、
 *   图片等媒体异步 load、滚动容器自身被挤压/放大；
 * - 用户上滚后不抢滚动条；滚回底部附近自动恢复跟随；
 * - 滚动容器可能延迟出现（loading 占位 → 列表）或被重建（切换会话），每次 DOM 变化
 *   时校验并按需重新绑定；
 * - rAF 节流，一帧内多次变化只滚一次。
 */

const AT_BOTTOM_THRESHOLD = 96;

/** 在容器子树中找 BubbleList 的滚动元素（inline overflow:auto 的 div）。 */
function findScrollElement(root: HTMLElement): HTMLElement | null {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>('div'))) {
    const overflowY = getComputedStyle(el).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') return el;
  }
  return null;
}

export function useStickToBottom(rootRef: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    let scrollEl: HTMLElement | null = null;
    let resizeObserver: ResizeObserver | null = null;
    let raf = 0;
    // 新绑定的滚动容器（首屏 / 切会话重建）默认视为贴底，与 BubbleList
    // initialScrollToBottom 的语义一致；此后仅由 scroll 事件更新——内容增高
    // 本身不触发 scroll 事件，不会把 atBottom 误判为 false。
    let atBottom = true;

    const distanceToBottom = () =>
      scrollEl ? scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight : Number.POSITIVE_INFINITY;

    const handleScroll = () => {
      atBottom = distanceToBottom() < AT_BOTTOM_THRESHOLD;
    };

    const pinToBottom = () => {
      if (raf || !scrollEl || !atBottom) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (scrollEl && atBottom) scrollEl.scrollTop = scrollEl.scrollHeight;
      });
    };

    const unbind = () => {
      if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
      resizeObserver?.disconnect();
      resizeObserver = null;
      if (scrollEl) {
        scrollEl.removeEventListener('scroll', handleScroll);
        // 捕获阶段监听媒体 load：图片/视频加载完成会增高内容但不产生 DOM mutation。
        scrollEl.removeEventListener('load', pinToBottom, true);
        scrollEl = null;
      }
    };

    const bind = (el: HTMLElement) => {
      scrollEl = el;
      atBottom = true;
      el.addEventListener('scroll', handleScroll, { passive: true });
      el.addEventListener('load', pinToBottom, true);
      if (typeof ResizeObserver !== 'undefined') {
        // 滚动容器自身尺寸变化（如输入框增高挤压列表）时，贴底状态下重新钉底。
        resizeObserver = new ResizeObserver(pinToBottom);
        resizeObserver.observe(el);
      }
    };

    const observer = new MutationObserver(() => {
      if (!scrollEl || !scrollEl.isConnected) {
        unbind();
        const next = findScrollElement(root);
        if (next) bind(next);
      }
      pinToBottom();
    });
    observer.observe(root, { subtree: true, childList: true, characterData: true });

    const initial = findScrollElement(root);
    if (initial) bind(initial);

    return () => {
      observer.disconnect();
      unbind();
    };
  }, [rootRef]);
}

export default useStickToBottom;

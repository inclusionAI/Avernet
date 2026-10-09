// 会话页 URL 双向同步:入向 mount/浏览器导航 → parseConversationRoute → onRouteSelection;
// 出向 hydrated 后把当前选中的 section/bot/origin/scope/friend/session 投影为规范 URL。
// 设计依据:docs/superpowers/plans/2026-09-24-workspace-conversation-navigation-refactor.md Task 4。
// section 缺失的旧/外部链接原样透传(由目录 Hook 匹配);永不序列化 `current` 与旧 `tab`。
// 自写防回环:自己 history.replace 产生的 location 变化不再重新分发路由。
import type { ConversationRouteState } from '@/domain/conversation';
import { parseConversationRoute, serializeConversationRoute } from '@/domain/conversation';
import { history, useLocation } from '@umijs/max';
import { useEffect, useRef } from 'react';

export function useConversationUrlSync(input: {
  hydrated: boolean;
  selection: ConversationRouteState;
  onRouteSelection: (route: ConversationRouteState) => void;
}): void {
  const { hydrated, selection, onRouteSelection } = input;
  const location = useLocation();
  const lastWrittenRef = useRef<string | null>(null);
  const onRouteSelectionRef = useRef(onRouteSelection);
  onRouteSelectionRef.current = onRouteSelection;
  const selectionRef = useRef(selection);
  selectionRef.current = selection;
  const hydratedRef = useRef(hydrated);
  hydratedRef.current = hydrated;

  // 入向:mount 与浏览器导航(排除自己刚刚写回的 URL)。
  useEffect(() => {
    const search = location.search.replace(/^\?/, '');
    if (search === lastWrittenRef.current) return;
    lastWrittenRef.current = search;
    onRouteSelectionRef.current(parseConversationRoute(search));
  }, [location.search]);

  // 出向:目录 hydration 完成后,把当前选中/展开状态投影为规范 URL。
  useEffect(() => {
    if (!hydrated) return;
    const next = serializeConversationRoute(selection);
    const current = window.location.search.replace(/^\?/, '');
    if (next === current) {
      lastWrittenRef.current = next;
      return;
    }
    lastWrittenRef.current = next;
    history.replace(
      next
        ? `${window.location.pathname}?${next}${window.location.hash}`
        : `${window.location.pathname}${window.location.hash}`,
    );
  }, [hydrated, selection]);
}

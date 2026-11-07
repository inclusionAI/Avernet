import type { GroupSidebarProps } from '@/pages/Workspace/components/GroupSidebar';
import { useEffect, useMemo, useRef } from 'react';

/** 定位可显示当前首屏之外的已验证会话，不修改分页缓存、offset 或历史加载状态。 */
export function useGroupSidebarLocation(props: GroupSidebarProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const handledRequestRef = useRef<number>();
  const target =
    props.location?.group.groupId === props.selectedGroupId &&
    props.location?.session.sessionId === props.selectedSessionId
      ? props.location
      : undefined;
  const groups = useMemo(
    () =>
      target && !props.groups.some((group) => group.groupId === target.group.groupId)
        ? [target.group, ...props.groups]
        : props.groups,
    [props.groups, target],
  );
  const sessionsByGroupId = useMemo(() => {
    if (!target) return props.sessionsByGroupId;
    const sessions = props.sessionsByGroupId[target.group.groupId] ?? [];
    if (sessions.some((session) => session.sessionId === target.session.sessionId)) return props.sessionsByGroupId;
    return { ...props.sessionsByGroupId, [target.group.groupId]: [target.session, ...sessions] };
  }, [props.sessionsByGroupId, target]);

  // 定位只展开 UI，不切换 store.expandedGroupIds；后者会触发选中群缓存清理及历史重连。
  const expandedGroupIds = useMemo(
    () => (target ? { ...props.expandedGroupIds, [target.group.groupId]: true as const } : props.expandedGroupIds),
    [props.expandedGroupIds, target],
  );
  const onToggleGroupExpanded = (groupId: string) => {
    if (target) {
      props.onDismissLocation?.();
      if (target.group.groupId === groupId && !props.expandedGroupIds[groupId]) return;
    }
    props.onToggleGroupExpanded(groupId);
  };

  useEffect(() => {
    if (!target || handledRequestRef.current === target.request) return undefined;
    // 等待侧栏展开/移动抽屉挂载后的布局，避免滚动到 aria-hidden 的桌面副本。
    const frame = requestAnimationFrame(() => {
      const root = rootRef.current;
      if (!root || root.closest('[aria-hidden="true"]')) return;
      const row = Array.from(root.querySelectorAll<HTMLElement>('[data-group-session-id]')).find(
        (item) =>
          item.dataset.groupSessionId === target.session.sessionId && item.dataset.groupId === target.group.groupId,
      );
      if (!row) return;
      handledRequestRef.current = target.request;
      const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      row.scrollIntoView({ block: 'center', behavior: reducedMotion ? 'auto' : 'smooth' });
      row.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [target, groups, sessionsByGroupId, props.isLoading, props.expandedGroupIds]);
  return { rootRef, groups, sessionsByGroupId, expandedGroupIds, onToggleGroupExpanded };
}

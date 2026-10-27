import type { TaskListItem } from '@/domain/tasks/models';
import { resolveTaskMainSessionId } from '@/hooks/useTaskSwitcherTasks';
import { isEnvelopeFailure, listMyTasks, normalizeMyTaskPage } from '@/services/myTask';
import { resolveTaskPanelDefaultWidth } from '@/services/workspace/panelWidthMemory';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle, PanelTab } from '@tc-chat/core';
import { useEffect, useRef } from 'react';

type PanelRef = React.RefObject<PanelHandle | null>;

function parseTaskTime(value: string | undefined): number {
  // list API 返回 `YYYY-MM-DD HH:mm:ss`；补 T 兼容 Safari。
  if (!value?.trim()) return Number.NEGATIVE_INFINITY;
  const normalized = value.includes('T') ? value : value.replace(' ', 'T');
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
}

function taskSortTime(record: TaskListItem): number {
  return parseTaskTime(record.gmt_modified) || parseTaskTime(record.gmt_create) || Number.NEGATIVE_INFINITY;
}

/** 选择会话内最近更新的任务；无有效时间时保持列表返回顺序。 */
export function findLatestTaskForSession(records: TaskListItem[], sessionId: string): TaskListItem | null {
  let latest: TaskListItem | null = null;
  for (const record of records) {
    if (resolveTaskMainSessionId(record) !== sessionId) continue;
    if (!latest || taskSortTime(record) > taskSortTime(latest)) latest = record;
  }
  return latest;
}

export function buildSessionTaskPanelTab(record: TaskListItem, userId: string): PanelTab {
  const title =
    record.task_spec?.context?.title?.trim() ||
    record.task_spec?.goal?.objective?.trim() ||
    `任务 ${record.task_id}`;
  return {
    id: `task-${record.task_id}`,
    title,
    type: 'taskPanel.TaskLoopView',
    closable: true,
    defaultWidth: resolveTaskPanelDefaultWidth(),
    params: {
      taskId: record.task_id,
      apiBaseUrl: '',
      bcsBaseUrl: '',
      userId,
    },
  };
}

/**
 * 进入会话时用任务列表反查该会话最新任务并打开副屏。
 *
 * 历史消息中的 <AixUI type="panel"> 只有进入已加载分页后才渲染；长会话里任务消息
 * 可能还在更早分页，不能作为默认进入时的副屏触发源。此 hook 以 list_tasks 为主数据源。
 */
export function useSessionTaskPanelBootstrap({
  sessionId,
  userId,
  panelRef,
}: {
  sessionId: string | null;
  userId: string | null;
  panelRef: PanelRef;
}): void {
  const bootstrapKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!sessionId || !userId) return undefined;
    const bootstrapKey = `${userId}:${sessionId}`;
    if (bootstrapKeyRef.current === bootstrapKey) return undefined;
    bootstrapKeyRef.current = bootstrapKey;
    let cancelled = false;

    void (async () => {
      try {
        const result = await listMyTasks({ user_id: userId });
        if (cancelled || isEnvelopeFailure(result) || !result.data) return;
        const records = Array.isArray(result.data) ? result.data : normalizeMyTaskPage(result.data).items;
        const record = findLatestTaskForSession(records, sessionId);
        const panel = panelRef.current;
        if (cancelled || !record || !panel) return;
        useTaskSidePanelStore.getState().setActiveTask(record.task_id);
        panel.openTab(buildSessionTaskPanelTab(record, userId));
      } catch {
        // 副屏自动恢复是增强体验：查询失败不阻断会话，历史分页加载后旧消息仍可兜底。
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [panelRef, sessionId, userId]);
}

export default useSessionTaskPanelBootstrap;

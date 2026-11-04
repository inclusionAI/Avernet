import type { TaskListItem } from '@/domain/tasks/models';
import { resolveTaskMainSessionId } from '@/hooks/useTaskSwitcherTasks';
import { getTaskBySession, isEnvelopeFailure, listMyTasks, normalizeMyTaskPage } from '@/services/myTask';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { resolveTaskPanelDefaultWidth } from '@/services/workspace/panelWidthMemory';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle, PanelTab } from '@tc-chat/core';
import { useEffect, useRef } from 'react';

type PanelRef = React.RefObject<PanelHandle | null>;
/**
 * 深链进入时，目标 Session 与持久副屏 owner 可异步就位。by_session 命中可能先返回；
 * 短暂等待 PanelHandle 出现，避免把此次自动恢复静默丢失。
 */
const PANEL_HANDLE_WAIT_TIMEOUT_MS = 2000;
const PANEL_HANDLE_POLL_INTERVAL_MS = 100;

async function waitForPanelHandle(
  panelRef: PanelRef,
  isCancelled: () => boolean,
): Promise<PanelHandle | null> {
  const deadline = Date.now() + PANEL_HANDLE_WAIT_TIMEOUT_MS;
  while (!isCancelled()) {
    const panel = panelRef.current;
    if (panel) return panel;
    if (Date.now() >= deadline) return null;
    await new Promise<void>((resolve) => {
      setTimeout(resolve, PANEL_HANDLE_POLL_INTERVAL_MS);
    });
  }
  return null;
}

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

export function buildRelayTaskPanelTab(
  taskId: string,
  ownerUserId: string,
  title?: string,
): PanelTab {
  return {
    id: `task-${taskId}`,
    title: title ?? '任务',
    type: 'taskPanel.TaskLoopView',
    closable: true,
    defaultWidth: resolveTaskPanelDefaultWidth(),
    params: {
      taskId,
      apiBaseUrl: '',
      bcsBaseUrl: '',
      userId: ownerUserId,
      taskApiBase: TASK_READ_API_BASE,
    },
  };
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
    const requestedTaskId = useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId;
    const bootstrapKey = `${userId}:${sessionId}:${requestedTaskId ?? 'latest'}`;
    if (bootstrapKeyRef.current === bootstrapKey) return undefined;
    bootstrapKeyRef.current = bootstrapKey;
    let cancelled = false;

    void (async () => {
      try {
        // 第二棒 session 没有声明式面板消息，先按执行记录反查；命中即恢复整图任务副屏。
        const sessionTask = await getTaskBySession(sessionId);
        if (cancelled) return;
        if (sessionTask.ok) {
          const relayTask = sessionTask.data;
          if (relayTask) {
            const panel = panelRef.current ?? (await waitForPanelHandle(panelRef, () => cancelled));
            if (cancelled || !panel) return;
            if (requestedTaskId) useTaskSidePanelStore.getState().consumeTaskPanelOpenIntent(requestedTaskId);
            useTaskSidePanelStore.getState().setActiveTask(relayTask.task_id);
            useTaskSidePanelStore.getState().markTaskPanelOpenIntent(relayTask.task_id);
            panel.openTab(buildRelayTaskPanelTab(relayTask.task_id, relayTask.owner_user_id));
            useTaskSidePanelStore.getState().consumeTaskPanelOpenIntent(relayTask.task_id);
            return;
          }
        }
        // 普通会话保持既有策略：显式任务优先，普通进入已有副屏则沿用。
        if (!requestedTaskId && useTaskSidePanelStore.getState().activeTaskId) return;
        const result = await listMyTasks({ user_id: userId });
        if (cancelled) return;
        if (requestedTaskId) useTaskSidePanelStore.getState().consumeTaskPanelOpenIntent(requestedTaskId);
        if (isEnvelopeFailure(result) || !result.data) return;
        const records = Array.isArray(result.data) ? result.data : normalizeMyTaskPage(result.data).items;
        const record = requestedTaskId
          ? records.find((item) => item.task_id === requestedTaskId && resolveTaskMainSessionId(item) === sessionId)
          : findLatestTaskForSession(records, sessionId);
        const panel = panelRef.current;
        if (cancelled || !record || !panel) return;
        useTaskSidePanelStore.getState().setActiveTask(record.task_id);
        useTaskSidePanelStore.getState().markTaskPanelOpenIntent(record.task_id);
        panel.openTab(buildSessionTaskPanelTab(record, userId));
        if (requestedTaskId) useTaskSidePanelStore.getState().consumeTaskPanelOpenIntent(record.task_id);
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

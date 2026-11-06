import type { TaskListItem } from '@/domain/tasks/models';
import { resolveTaskMainSessionId } from '@/hooks/useTaskSwitcherTasks';
import {
  getTaskBySession,
  isEnvelopeFailure,
  listMyTasks,
  normalizeMyTaskPage,
} from '@/services/myTask';
import type { ListMyTaskResult } from '@/services/myTask';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { resolveTaskPanelDefaultWidth } from '@/services/workspace/panelWidthMemory';
import { useTaskSessionAssociationStore } from '@/stores/taskSessionAssociationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle, PanelTab } from '@tc-chat/core';
import { useEffect, useRef } from 'react';

type PanelRef = React.RefObject<PanelHandle | null>;
/**
 * 深链进入时，GroupChatPane/PanelHandle 可能晚于 Session selection 异步就位；
 * by_session/list 可能先返回。等待必须跟随当前 session 生命周期，而不是固定 2s，
 * 否则冷启动深链加载群列表/会话较慢时，自动恢复会在 panel 挂载前静默丢失。
 */
const PANEL_HANDLE_POLL_INTERVAL_MS = 100;

async function waitForPanelHandle(
  panelRef: PanelRef,
  isCancelled: () => boolean,
): Promise<PanelHandle | null> {
  while (!isCancelled()) {
    const panel = panelRef.current;
    if (panel) return panel;
    await new Promise<void>((resolve) => {
      setTimeout(resolve, PANEL_HANDLE_POLL_INTERVAL_MS);
    });
  }
  return null;
}

/**
 * 解析会话归属人工号（session_id 末尾 :user:<owner>）。
 *
 * 任务列表兜底要按会话归属人查询；但打开后的 panel 交互身份仍需用当前登录人。
 * 无 :user: 段（测试/旧会话）时退回当前登录人。
 */
export function resolveSessionOwnerUserId(sessionId: string | null, fallbackUserId: string | null): string | null {
  const owner = sessionId?.match(/:user:([^:]+)$/)?.[1]?.trim();
  return owner || fallbackUserId;
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
  userId: string,
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
      userId,
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

function taskRecords(result: ListMyTaskResult): TaskListItem[] | null {
  if (!result || isEnvelopeFailure(result) || !result.data) return null;
  return Array.isArray(result.data) ? result.data : normalizeMyTaskPage(result.data).items;
}

/**
 * 进入会话时用任务执行记录或任务列表反查任务并打开副屏。
 *
 * by_session 返回同会话的全部轻量 task ref：默认打开最新一个；显式 task intent
 * 优先；AixUI 已经触发展示时不允许 by_session 抢焦点；普通会话保持 list 兜底。
 */
export function useSessionTaskPanelBootstrap({
  sessionId,
  userId,
  panelRef,
  enabled = true,
}: {
  sessionId: string | null;
  userId: string | null;
  panelRef: PanelRef;
  enabled?: boolean;
}): void {
  const bootstrapKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || !sessionId || !userId) return undefined;
    const requestedTaskId = useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId;
    const sessionOwnerUserId = resolveSessionOwnerUserId(sessionId, userId);
    if (!sessionOwnerUserId) return undefined;
    const bootstrapKey = `${sessionOwnerUserId}:${sessionId}:${requestedTaskId ?? 'latest'}`;
    if (bootstrapKeyRef.current === bootstrapKey) return undefined;
    bootstrapKeyRef.current = bootstrapKey;
    let cancelled = false;

    const openTaskPanel = async (
      taskId: string,
      records: TaskListItem[] | null,
      waitForPanel: boolean,
    ): Promise<void> => {
      const panel = waitForPanel
        ? panelRef.current ?? (await waitForPanelHandle(panelRef, () => cancelled))
        : panelRef.current;
      if (cancelled || !panel) return;
      const record = records?.find((item) => item.task_id === taskId) ?? null;
      useTaskSidePanelStore.getState().setActiveTask(taskId);
      useTaskSidePanelStore.getState().markTaskPanelOpenIntent(taskId);
      panel.openTab(
        record
          ? buildSessionTaskPanelTab(record, userId)
          : buildRelayTaskPanelTab(taskId, userId),
      );
      useTaskSidePanelStore.getState().consumeTaskPanelOpenIntent(taskId);
    };

    void (async () => {
      try {
        const associationStore = useTaskSessionAssociationStore.getState();
        associationStore.setActiveSession(sessionId);

        const sessionTask = await getTaskBySession(sessionId);
        if (cancelled) return;
        const sessionTaskRefs = sessionTask.ok ? sessionTask.data ?? [] : [];
        associationStore.setActiveSessionTaskIds(
          sessionId,
          sessionTaskRefs.map((ref) => ref.task_id),
        );

        // 显式任务来源（任务列表“进入对话”）具有最高优先级，不会被默认 latest task 覆盖。
        if (requestedTaskId) {
          associationStore.setActiveSessionTaskIds(sessionId, [requestedTaskId]);
          const requestedResult = await listMyTasks({ user_id: sessionOwnerUserId });
          if (cancelled) return;
          await openTaskPanel(requestedTaskId, taskRecords(requestedResult), false);
          return;
        }

        // by_session 已命中：默认恢复最新关联 task。已展示的 AixUI task 不被抢焦点。
        if (sessionTaskRefs.length > 0) {
          if (useTaskSidePanelStore.getState().activeTaskId) return;
          const latestResult = await listMyTasks({ user_id: sessionOwnerUserId });
          if (cancelled) return;
          const latestRef = sessionTaskRefs[0];
          await openTaskPanel(latestRef.task_id, taskRecords(latestResult), true);
          return;
        }

        // by_session 未命中时保持既有策略：已有副屏沿用，否则从任务列表选择当前会话最新任务。
        if (useTaskSidePanelStore.getState().activeTaskId) return;
        const result = await listMyTasks({ user_id: sessionOwnerUserId });
        if (cancelled) return;
        const records = taskRecords(result);
        const latestRecord = records ? findLatestTaskForSession(records, sessionId) : null;
        if (!latestRecord) return;
        associationStore.setActiveSessionTaskIds(sessionId, [latestRecord.task_id]);
        await openTaskPanel(latestRecord.task_id, records, false);
      } catch {
        // by_session 失败不算会话错误；恢复是增强体验，不能阻断会话打开。
        if (!cancelled) {
          useTaskSessionAssociationStore.getState().setActiveSessionTaskIds(sessionId, []);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (bootstrapKeyRef.current === bootstrapKey) bootstrapKeyRef.current = null;
    };
  }, [enabled, panelRef, sessionId, userId]);
}

export default useSessionTaskPanelBootstrap;

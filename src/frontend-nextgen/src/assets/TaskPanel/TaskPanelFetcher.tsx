// @asset-migrated: teamclaw 自研资产
/**
 * TaskPanelFetcher —— 任务副屏轮询内核（路 A 自管）。
 * - 数据流：由上层 wrapper 透传 {apiBaseUrl(host), taskApiBase(路径前缀), taskId}，组件内 raw fetch，不自读 config / 不依赖 taskService / 不反查 capability。
 * - apiBaseUrl 为 '' 时走相对路径，由当前环境代理转发；taskApiBase 缺省回退内面 /api/v1。
 * - 轮询：图级 status 非终态时 setTimeout 重排（默认 1000ms）；产品态 DONE/FAILED/REVIEWING/CANCELLED 停，兼容旧 HUNG。
 * - 取消：AbortController，切 taskId / 卸载时 abort。
 * - 日志：include_action_log 默认 false（日志抽屉打开时由上层单独请求，P0 常规轮询不携带）。
 */
import { extractLoginUrl, isAceLoginResponse } from '@/services/backendApi/aceLoginBody';
import { triggerAceLoginRedirect } from '@/services/backendApi/httpClient';
import { isEnvelopeFailure } from '@/services/backendApi/types';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { Envelope, TaskDashboardResponse } from './contract';
import { unwrapHttpEnvelope } from './outputEnvelope';
import { mapDashboard } from './taskPanelMapper';
import { SOURCE_LABELS, TASK_TYPE_LABELS } from './tokens';
import type { TaskView } from './types';

const POLLING_INTERVAL = 5000; // spec §4.6：5s
const MAX_RETRIES = 3;

function joinUrl(baseUrl: string, path: string): string {
  const b = (baseUrl ?? '').replace(/\/+$/, '');
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${b}${p}`;
}

export interface TaskPanelFetcherProps {
  apiBaseUrl: string;
  // task API 路径前缀（不含 host）：Open Core /openapi/v1/collaboration/tasks、内部 /api/v1/collaboration/tasks。
  // 由 app-level 经 capability 解析透传，缺省回退内面路径（向后兼容纯 assets 渲染）。
  taskApiBase?: string;
  taskId: string;
  userId?: string;
  includeActionLog?: boolean;
  children: (state: {
    task: TaskView | null;
    loading: boolean;
    refreshing: boolean;
    error: string | null;
    retry: () => void;
  }) => React.ReactNode;
}

export const TaskPanelFetcher: React.FC<TaskPanelFetcherProps> = ({
  apiBaseUrl,
  taskApiBase = '/api/v1/collaboration/tasks',
  taskId,
  userId,
  includeActionLog = false,
  children,
}) => {
  const [task, setTask] = useState<TaskView | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retrySignal, setRetrySignal] = useState(0);
  const [listFallback, setListFallback] = useState<
    Partial<Pick<TaskView, 'taskTypeLabel' | 'sourceLabel' | 'ownerBotName' | 'createdAt' | 'finishedAt'>>
  >({});

  const abortRef = useRef<AbortController | null>(null);
  const retryRef = useRef(0);

  // dashboard 图接口返回的 TaskExecutionGraphDTO 可能不含任务列表元信息；
  // 用同一 taskId 从任务列表补齐创建时间/结束时间/Owner Bot，失败静默降级。
  useEffect(() => {
    if (!taskId || !userId) {
      setListFallback({});
      return undefined;
    }
    let cancelled = false;
    const loadListFallback = async () => {
      try {
        const url = joinUrl(
          apiBaseUrl,
          `${taskApiBase}/list?user_id=${encodeURIComponent(userId)}&page=1&page_size=100`,
        );
        const response = await fetch(url, { credentials: 'include' });
        if (!response.ok) return;
        const envelope = (await response.json()) as { data?: unknown };
        const data = envelope.data;
        const items = Array.isArray(data)
          ? data
          : data && typeof data === 'object' && Array.isArray((data as { items?: unknown[] }).items)
          ? (data as { items: unknown[] }).items
          : [];
        const record = items.find(
          (item) => item && typeof item === 'object' && (item as { task_id?: string }).task_id === taskId,
        ) as
          | {
              task_id?: string;
              source_type?: string;
              owner_bot_id?: string;
              owner_bot_name?: string;
              execution_config?: { task_type?: string };
              gmt_create?: string;
              gmt_modified?: string;
              status?: string;
            }
          | undefined;
        if (!record || cancelled) return;
        setListFallback({
          taskTypeLabel: record.execution_config?.task_type
            ? TASK_TYPE_LABELS[record.execution_config.task_type] ?? record.execution_config.task_type
            : undefined,
          sourceLabel: record.source_type ? SOURCE_LABELS[record.source_type] ?? record.source_type : undefined,
          ownerBotName: record.owner_bot_name ?? record.owner_bot_id,
          createdAt: record.gmt_create,
          finishedAt: ['DONE', 'FAILED', 'CANCELLED', 'REVIEWING'].includes(record.status ?? '')
            ? record.gmt_modified ?? null
            : null,
        });
      } catch {
        // 任务详情主请求不应因补充列表信息失败而失败。
      }
    };
    void loadListFallback();
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, taskApiBase, taskId, userId]);

  const fetchDashboard = useCallback(
    async (mode: 'initial' | 'refresh') => {
      if (!taskId || apiBaseUrl === undefined || apiBaseUrl === null) return;

      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      if (mode === 'initial') {
        setLoading(true);
      } else {
        setRefreshing(true);
      }
      setError(null);

      try {
        const url = joinUrl(
          apiBaseUrl,
          `${taskApiBase}/dashboard?task_id=${encodeURIComponent(taskId)}&include_action_log=${
            includeActionLog ? 'true' : 'false'
          }`,
        );
        const resp = await fetch(url, { credentials: 'include', signal: ctrl.signal });
        if (!resp.ok) {
          throw new Error(`请求失败（${resp.status}）`);
        }
        const json = (await resp.json()) as Envelope<TaskDashboardResponse>;
        // 网关级 ACE 登录拦截体:本副屏走 raw fetch 打团队网关,未登录时绕过 httpClient,登记单飞跳转后中断本次加载。
        if (isAceLoginResponse(json)) {
          const loginUrl = extractLoginUrl(json);
          triggerAceLoginRedirect(loginUrl);
          return;
        }
        if (isEnvelopeFailure(json)) {
          throw new Error(json.message || `业务错误码 ${json.code}`);
        }
        const dashboard = unwrapHttpEnvelope(json);
        if (!dashboard || typeof dashboard !== 'object' || Array.isArray(dashboard)) {
          throw new Error('响应数据为空');
        }
        if (abortRef.current !== ctrl) return;
        const mapped = mapDashboard(dashboard as TaskDashboardResponse);
        retryRef.current = 0;
        setTask(mapped);
      } catch (err) {
        if ((err as Error).name === 'AbortError') return;
        if (abortRef.current !== ctrl) return;
        setError((err as Error).message || '加载任务失败');
      } finally {
        if (abortRef.current === ctrl) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [apiBaseUrl, taskApiBase, taskId, includeActionLog],
  );

  // 首帧 + taskId/baseUrl 变更重置
  useEffect(() => {
    setTask(null);
    setError(null);
    setLoading(false);
    setRefreshing(false);
    abortRef.current?.abort();
    fetchDashboard('initial');
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [fetchDashboard]);

  // 轮询：setTimeout 重排，终态自然停
  // 产品态：EXECUTING → 继续轮询；DONE/FAILED/REVIEWING(HUNG) → 停
  useEffect(() => {
    if (!task) {
      if (retrySignal > 0 && retryRef.current < MAX_RETRIES) {
        const t = window.setTimeout(() => fetchDashboard('initial'), POLLING_INTERVAL);
        return () => window.clearTimeout(t);
      }
      return undefined;
    }
    // 轮询终止条件：产品终态 DONE/FAILED/REVIEWING/CANCELLED，兼容旧 HUNG 已在 mapper 中转为 REVIEWING。
    const hasActiveNode = task.nodes.some((n) => n.status === 'running' || n.status === 'pending');
    const isCancelled = task.status === 'CANCELLED';
    const isTerminal = isCancelled || (['DONE', 'FAILED', 'REVIEWING'].includes(task.status) && !hasActiveNode);
    if (isTerminal) return undefined;
    const timer = window.setTimeout(() => fetchDashboard('refresh'), POLLING_INTERVAL);
    return () => window.clearTimeout(timer);
  }, [task, fetchDashboard, retrySignal]);

  const retry = useCallback(() => {
    retryRef.current = 0;
    setRetrySignal((s) => s + 1);
    fetchDashboard('initial');
  }, [fetchDashboard]);

  return (
    <>
      {children({
        task: task
          ? {
              ...task,
              taskTypeLabel: task.taskTypeLabel || listFallback.taskTypeLabel || '',
              sourceLabel: task.sourceLabel || listFallback.sourceLabel || '',
              ownerBotName: task.ownerBotName || listFallback.ownerBotName || '',
              createdAt: task.createdAt || listFallback.createdAt || '',
              finishedAt: task.finishedAt || listFallback.finishedAt || null,
            }
          : null,
        loading,
        refreshing,
        error,
        retry,
      })}
    </>
  );
};

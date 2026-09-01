// @asset-migrated: teamclaw 自研资产
/**
 * TaskPanelFetcher —— 任务副屏轮询内核（路 A 自管）。
 * - 数据流：从 params.{apiBaseUrl, taskId} 取参，组件内 raw fetch，不自读 config / 不依赖 taskService。
 * - apiBaseUrl 为 '' 时走相对路径，由当前环境代理转发 dashboard 到对应网关。
 * - 轮询：图级 status 非终态时 setTimeout 重排（默认 1000ms）；产品态 DONE/FAILED/REVIEWING/CANCELLED 停，兼容旧 HUNG。
 * - 取消：AbortController，切 taskId / 卸载时 abort。
 * - 日志：include_action_log 默认 false（日志抽屉打开时由上层单独请求，P0 常规轮询不携带）。
 */
import { extractLoginUrl, isAceLoginResponse } from '@/services/backendApi/aceLoginBody';
import { triggerAceLoginRedirect } from '@/services/backendApi/httpClient';
import { isEnvelopeFailure } from '@/services/backendApi/types';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { Envelope, TaskDashboardResponse } from './contract';
import { mapDashboard } from './taskPanelMapper';
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
  taskId: string;
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
  taskId,
  includeActionLog = false,
  children,
}) => {
  const [task, setTask] = useState<TaskView | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retrySignal, setRetrySignal] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const retryRef = useRef(0);

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
          `/api/v1/collaboration/tasks/dashboard?task_id=${encodeURIComponent(taskId)}&include_action_log=${
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
        if (!json.data) {
          throw new Error('响应数据为空');
        }
        if (abortRef.current !== ctrl) return;
        const mapped = mapDashboard(json.data);
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
    [apiBaseUrl, taskId, includeActionLog],
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

  return <>{children({ task, loading, refreshing, error, retry })}</>;
};

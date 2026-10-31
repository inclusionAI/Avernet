// useBotPublicRisk：Bot 公开风险前置检测状态机 Hook（bot-public-risk-check，design D4/D5）。
// - 弹窗打开恢复在途（getCurrent）、发起（client_request_id 幂等生命周期）、编辑器存续期 15s 轮询；
// - 手动刷新（refreshCurrent，§2.2）：终局失败后立即查询原安全单同步最新结论，不重新建单；
// - 4xx 终止轮询并提示（可重试错误静默继续）；审批态/关闭停止轮询；
// - 与老前端（open-claw useBotPublicRisk）语义同构，但 userId 由协作身份上下文注入、record 派生不含
//   legacy public_approval 双源合并（新版审批态由后端 phase=APPROVING 表达）。

import { notifyError } from '@/components/ui/notify';
import { deriveRiskUiState, type BotPublicRiskUiState } from '@/domain/collaborationPrivacy/botPublicRisk';
import {
  createBotPublicRiskCheck,
  getCurrentBotPublicRiskCheck,
  refreshBotPublicRiskCheck,
  type BotPublicRiskCheckRecord,
} from '@/services/collaborationPrivacy/botPublicRiskService';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const POLL_INTERVAL_MS = 15_000;

function isFatalHttpError(error: unknown): boolean {
  const status = (error as { response?: { status?: unknown } })?.response?.status;
  return typeof status === 'number' && status >= 400 && status < 500;
}

function generateRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `bpr-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 终局失败后的同意图重发必须换新 ID，否则幂等重放永远返回旧失败记录（design D5）。
 * 审批 DISAGREE/CANCEL 的终态是 phase='CLOSED' 而非 REJECT（后端 approval_guard），
 * 必须一并覆盖；PUBLISHED 不在此列：同意图已发布时重放属不变位场景。
 */
function isTerminalRejectedRecord(record: BotPublicRiskCheckRecord | null): boolean {
  return (
    !!record && (record.security_status === 'REJECT' || record.phase === 'NEEDS_ATTENTION' || record.phase === 'CLOSED')
  );
}

export interface UseBotPublicRiskOptions {
  /** 协作域 Bot UUID（复合地址） */
  botId: string | undefined;
  /** 已就绪操作者 user_id；null 时 hook 整体静默（调查询/发起都不发请求） */
  userId: string | null;
  publicScope?: 'user' | 'agent';
  /** 挂载者存续开关（通用配置弹窗/编辑器打开期）；false 时停轮询 */
  enabled?: boolean;
}

export interface BotPublicRiskCreateIntent {
  visibility: 'public' | 'protected';
  view_depts: Array<{ deptNo: string; deptName: string }>;
}

export function useBotPublicRisk({ botId, userId, publicScope = 'user', enabled = true }: UseBotPublicRiskOptions) {
  const [currentRecord, setCurrentRecord] = useState<BotPublicRiskCheckRecord | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const requestIdRef = useRef<string | null>(null);
  const requestIntentKeyRef = useRef<string | null>(null);
  const pollFatalRef = useRef<Error | null>(null);

  const queryCurrent = useCallback(async () => {
    if (!botId || !userId) return;
    try {
      const resp = await getCurrentBotPublicRiskCheck(botId, userId, { public_scope: publicScope });
      setCurrentRecord(resp.data ?? null);
    } catch {
      // 恢复查询失败按无在途申请兜底，不阻塞弹窗打开（消息中心仍有终态触达）。
    }
  }, [botId, publicScope, userId]);

  /** 发起风险申请；成功返回记录（编辑器据此进入已发起终态），失败返回 null。 */
  const create = useCallback(
    async (intent: BotPublicRiskCreateIntent): Promise<BotPublicRiskCheckRecord | null> => {
      if (!botId || !userId) return null;
      const intentKey = `${publicScope}|${intent.visibility}|${JSON.stringify(
        intent.view_depts.map((d) => [d.deptNo, d.deptName]).sort(),
      )}`;
      if (requestIntentKeyRef.current !== intentKey || isTerminalRejectedRecord(currentRecord)) {
        requestIdRef.current = generateRequestId();
        requestIntentKeyRef.current = intentKey;
      }
      if (!requestIdRef.current) return null;
      setIsCreating(true);
      try {
        const resp = await createBotPublicRiskCheck(botId, userId, {
          client_request_id: requestIdRef.current,
          public_scope: publicScope,
          visibility: intent.visibility,
          view_depts: intent.view_depts,
        });
        const record = resp.data ?? null;
        if (!record) {
          notifyError(resp.message || '发起公开风险检测失败，请稍后重试');
          return null;
        }
        setCurrentRecord(record);
        return record;
      } catch (error) {
        notifyError((error as Error).message || '发起公开风险检测失败，请稍后重试');
        return null;
      } finally {
        setIsCreating(false);
      }
    },
    [botId, currentRecord, publicScope, userId],
  );

  /**
   * 手动刷新（终局失败兜底）：立即查询原安全单并更新本地视图（§2.2 手动刷新）。
   * REJECT/NEEDS_ATTENTION 记录仍 current 且轮询已停——工单结论被外部改判后，
   * 用户须不重新建单即可同步到最新状态；无当前记录时退化为恢复查询。
   * outcome=NOT_QUERIED 仅按返回 record 更新展示，不构成任何检测结论。
   */
  const refreshCurrent = useCallback(async () => {
    const riskCheckId = currentRecord?.risk_check_id;
    if (!botId || !userId) return;
    if (!riskCheckId) {
      await queryCurrent();
      return;
    }
    setIsRefreshing(true);
    try {
      const resp = await refreshBotPublicRiskCheck(botId, userId, riskCheckId);
      const record = resp.data?.record ?? null;
      if (record) setCurrentRecord(record);
    } catch (error) {
      notifyError((error as Error).message || '刷新检测状态失败，请稍后重试');
    } finally {
      setIsRefreshing(false);
    }
  }, [botId, currentRecord, queryCurrent, userId]);

  const uiState: BotPublicRiskUiState = useMemo(() => deriveRiskUiState(currentRecord), [currentRecord]);

  // 切换 Bot 重置会话态
  useEffect(() => {
    requestIdRef.current = null;
    requestIntentKeyRef.current = null;
    pollFatalRef.current = null;
    setCurrentRecord(null);
  }, [botId]);

  // 编辑器会话边界（enabled false→true，页面模式下编辑器重开）：作废上轮申请标识并
  // 复位轮询致命位，避免同 ID 重发命中已被 REPLACED/CLOSED 旧记录的幂等重放；
  // 弹窗模式（fixedBotId）hook 随弹窗卸载重建，天然无此残留。
  const prevEnabledRef = useRef(enabled);
  useEffect(() => {
    if (!prevEnabledRef.current && enabled) {
      requestIdRef.current = null;
      requestIntentKeyRef.current = null;
      pollFatalRef.current = null;
    }
    prevEnabledRef.current = enabled;
  }, [enabled]);

  // 编辑器存续期轮询：checking/advancing 打开，4xx 终止，approving/关闭清理
  const shouldPoll = enabled && (uiState.kind === 'checking' || uiState.kind === 'advancing');
  useEffect(() => {
    if (!shouldPoll || pollFatalRef.current || !botId || !userId) return;
    let fatal = false;
    const timer = setInterval(() => {
      if (fatal) return;
      void (async () => {
        try {
          const resp = await getCurrentBotPublicRiskCheck(botId!, userId!, { public_scope: publicScope });
          // 输出引用可分离：闭包里只比对最新 setState
          setCurrentRecord(resp.data ?? null);
        } catch (error) {
          if (isFatalHttpError(error)) {
            fatal = true;
            pollFatalRef.current = new Error((error as Error).message);
            notifyError('查询检测状态失败，已停止自动刷新');
          }
        }
      })();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
    // uiState 由 currentRecord 派生，checking/advancing 期间查询自身不重置节奏
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldPoll, botId, userId, publicScope]);

  return {
    currentRecord,
    uiState,
    isCreating,
    isRefreshing,
    queryCurrent,
    create,
    refreshCurrent,
  };
}

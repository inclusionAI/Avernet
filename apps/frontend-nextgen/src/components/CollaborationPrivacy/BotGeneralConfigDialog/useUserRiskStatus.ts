/**
 * 公开风险前置检测行级在途态派生（bot-public-risk-check，仅「通用配置」弹窗对用户行消费）：
 * approving 起钩子不再自动轮询（审批终态由后端工单回调推进），行级「刷新状态」做双刷——
 * 先经 refresh 端点重读风险申请记录（checking 暂停态还强制反查安全单，穿透暂停），
 * 再静默重载总览：审批终态由回调写回 BCS friend_ext/可见性，行级「待审批」与配置须随
 * 之同步（同老前端 open-claw 刷新按钮的 onRefreshBotDetail + onRefreshRisk 双刷语义）。
 * failed/attention 恢复编辑不在行上展示，返回 null。
 */
import type { BotPublicRiskUiState } from '@/domain/collaborationPrivacy/botPublicRisk';
import type { BotPublicRiskCheckRecord } from '@/services/collaborationPrivacy/botPublicRiskService';
import { history } from '@umijs/max';
import { useCallback, useMemo } from 'react';

/** 行级在途态所需的协作权限数据面切片（useCollaborationPrivacy 的最小消费面）。 */
interface UserRiskStatusSource {
  userRisk: {
    uiState: BotPublicRiskUiState;
    record: BotPublicRiskCheckRecord | null;
    refreshCurrent: () => void | Promise<void>;
    isRefreshing: boolean;
  };
  load: (signal?: AbortSignal, options?: { silent?: boolean }) => Promise<void>;
}

export interface UserRiskStatus {
  kind: 'checking' | 'advancing' | 'approving';
  detailUrl: string | null;
  onViewApprovalProgress: () => void;
  onRefreshStatus: () => Promise<void>;
  refreshingStatus: boolean;
}

export function useUserRiskStatus({ userRisk, load }: UserRiskStatusSource): UserRiskStatus | null {
  const refreshUserRiskStatus = useCallback(async () => {
    await userRisk.refreshCurrent();
    await load(undefined, { silent: true });
  }, [userRisk.refreshCurrent, load]);
  const { uiState, record, isRefreshing } = userRisk;
  return useMemo(() => {
    if (uiState.kind === 'checking' || uiState.kind === 'advancing' || uiState.kind === 'approving') {
      return {
        kind: uiState.kind,
        detailUrl: uiState.kind === 'approving' ? null : record?.detail_url ?? null,
        onViewApprovalProgress: () => history.push('/ticket-center'),
        onRefreshStatus: refreshUserRiskStatus,
        refreshingStatus: isRefreshing,
      };
    }
    return null;
  }, [uiState, record, isRefreshing, refreshUserRiskStatus]);
}

import { notifySuccess } from '@/components/ui/notify';
import { getRiskRetryIntent } from '@/domain/collaborationPrivacy/botPublicRisk';
import type { CollaborationBot } from '@/domain/collaborationPrivacy/types';
import { useBotPublicRisk, type BotPublicRiskCreateIntent } from '@/hooks/useBotPublicRisk';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import { useCallback, useEffect, useRef } from 'react';
import type { PublicationEditorState } from './collaborationPrivacyHelpers';

/**
 * Bot 公开风险前置检测接线（TC-G005 从 useCollaborationPrivacy 拆出，行为不变）：
 * 仅「对用户」维度启用；外部 Bot 无对用户公开审批。进入审批边沿经 silentReload
 * 静默刷新一次协作数据；userRisk 透传给编辑器/通用配置弹窗，createUserRisk 与
 * blockedRiskApprovalPuid 供提交通道复用。
 */
export function useCollaborationPrivacyUserRisk({
  userId,
  fixedBotId,
  isExternalBot,
  visibleBots,
  publicationBot,
  publicationEditor,
  silentReload,
}: {
  userId: string | null;
  fixedBotId?: string;
  isExternalBot: boolean;
  visibleBots: CollaborationBot[];
  publicationBot?: CollaborationBot;
  publicationEditor: PublicationEditorState | null;
  /** 进入审批（APPROVING）边沿时回调：静默刷新协作数据，让 friend_ext 审批投影落地。 */
  silentReload: () => void;
}) {
  // Bot 公开风险前置检测（bot-public-risk-check，design D3/D4）：仅「对用户」维度启用。
  // 外部 Bot 无对用户公开审批；仅普通 Bot 的通用配置弹窗/公开编辑器启用查询和轮询。
  const riskBotId = publicationBot?.id ?? visibleBots[0]?.id;
  const riskEnabled = !isExternalBot && Boolean(riskBotId && userId && (fixedBotId || publicationEditor));
  const risk = useBotPublicRisk({ botId: riskBotId, userId, publicScope: 'user', enabled: riskEnabled });
  useEffect(() => {
    if (riskEnabled) void risk.queryCurrent();
    // 打开弹窗/编辑器时恢复在途申请状态（spec「通用配置行在途状态」）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [riskEnabled, riskBotId, userId]);
  // 检测进入审批（APPROVING）边沿静默刷新一次协作数据：审批工单（puid + approval_url）
  // 已由后端写入 BCS friend_ext，刷新让 RelationCard 的 pending 分支以审批外链接管
  // 「查看审批进度」入口（risk 轮询只更新检测记录，不更新 friend_ext 视图）。
  const riskApprovingRef = useRef(false);
  useEffect(() => {
    if (risk.uiState.kind !== 'approving') {
      riskApprovingRef.current = false;
      return;
    }
    if (riskApprovingRef.current) return;
    riskApprovingRef.current = true;
    void silentReload();
    // 仅在进入 approving 的边沿触发一次；重复触发无额外收益
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [risk.uiState.kind]);
  const createUserRisk = useCallback(
    async (botId: string, intent: BotPublicRiskCreateIntent) => {
      const { busyAction, setBusyAction } = useCollaborationPrivacyStore.getState();
      if (busyAction || risk.isRefreshing) return;
      setBusyAction(`${botId}:publication:user`);
      try {
        const record = await risk.create(intent);
        if (record) notifySuccess('Bot公开风险前置检测已发起');
      } finally {
        setBusyAction(null);
      }
    },
    [risk.create, risk.isRefreshing],
  );
  // 审批中外链：取 risk 记录同一笔审批的 friend_ext 投影（审批边沿静默刷新后写入
  // pendingPublications），判定与 RelationCard 的 pendingApproval 同构——仅 approving
  // 且投影属于当前审批（puid 为空或与 pending.id 一致）时透出，供「查看审批进度」
  // 入口（弹窗在途卡与行级）共用同一审批工单地址。
  const riskSourceBot = publicationBot ?? visibleBots[0];
  const pendingUserApproval =
    risk.uiState.kind === 'approving' ? riskSourceBot?.pendingPublications.user : undefined;
  const riskApprovalUrl =
    pendingUserApproval?.approvalUrl &&
    (!risk.currentRecord?.approval_puid || pendingUserApproval.id === risk.currentRecord.approval_puid)
      ? pendingUserApproval.approvalUrl
      : null;
  const retryIntent = getRiskRetryIntent(risk.uiState);
  const blockedRiskApprovalPuid = retryIntent ? risk.currentRecord?.approval_puid : null;
  const retryCurrent =
    riskEnabled && publicationEditor?.audience === 'user' && retryIntent
      ? () => createUserRisk(publicationEditor.botId, retryIntent)
      : undefined;

  return {
    userRisk: {
      uiState: risk.uiState,
      record: risk.currentRecord,
      approvalUrl: riskApprovalUrl,
      isCreating: risk.isCreating,
      refreshCurrent: risk.refreshCurrent,
      isRefreshing: risk.isRefreshing,
      retryCurrent,
    },
    createUserRisk,
    blockedRiskApprovalPuid,
  };
}

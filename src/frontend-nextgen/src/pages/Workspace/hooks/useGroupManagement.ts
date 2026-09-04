import type { DeliveryPolicy, GroupView } from '@/domain/collaboration';
import type { DingTalkBindingState, GroupDingTalkConfig } from '@/services/workspace/channelBindingService';
import { channelBindingService } from '@/services/workspace/channelBindingService';
import { groupMemberService } from '@/services/workspace/groupMemberService';
import { groupService } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { invitationService } from '@/services/workspace/invitationService';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export interface UseGroupManagementResult {
  updateGroup: (patch: {
    name?: string;
    visibility?: 'private' | 'public';
    deliveryPolicy?: DeliveryPolicy;
  }) => Promise<DomainResult<GroupView> | null>;
  addMember: (actorId: string) => Promise<boolean>;
  removeMember: (actorId: string) => Promise<boolean>;
  leaveGroup: (actorId: string) => Promise<boolean>;
  createShare: (targetGroupId?: string) => Promise<DomainResult<{ invitationUrl: string }>>;
  /** 当前群的钉钉绑定状态：null 未绑定 / view 已绑定 / 'conflict' 多绑定冲突。 */
  dingTalkBinding: DingTalkBindingState;
  dingTalkLoading: boolean;
  /** 保存钉钉配置：已绑定走 PATCH config，未绑定走 POST 创建。appSecret 无论新建/编辑均需填。 */
  saveDingTalk: (config: GroupDingTalkConfig) => Promise<boolean>;
  /** 启停钉钉绑定（PATCH active）。 */
  toggleDingTalkActive: (active: boolean) => Promise<boolean>;
  /** 解绑钉钉机器人（DELETE）。 */
  deleteDingTalk: () => Promise<boolean>;
}

function notifyError(res: { ok: false; error: { friendlyMessage: string } }): void {
  toast.error(res.error.friendlyMessage);
}

/**
 * 群管理写操作的编排 Hook：更新、成员增删/退出、分享链接、钉钉机器人绑定。
 * 组件只调用此 Hook，不直接依赖 groupService/invitationService/channelBindingService。
 */
export function useGroupManagement(
  groupId: string | null,
  onMutated: () => Promise<void>,
  loadChannelBindingsEnabled: boolean = true,
): UseGroupManagementResult {
  const [dingTalkBinding, setDingTalkBinding] = useState<DingTalkBindingState>(null);
  const [dingTalkLoading, setDingTalkLoading] = useState(false);

  const reloadDingTalk = useCallback(async () => {
    if (!groupId) {
      setDingTalkBinding(null);
      return;
    }
    setDingTalkLoading(true);
    const res = await channelBindingService.loadGroupDingTalkBinding(groupId);
    setDingTalkLoading(false);
    if (res.ok) setDingTalkBinding(res.data);
    else setDingTalkBinding(null);
  }, [groupId]);

  useEffect(() => {
    if (!loadChannelBindingsEnabled) return;
    void reloadDingTalk();
  }, [loadChannelBindingsEnabled, reloadDingTalk]);

  const updateGroup = useCallback(
    async (patch: Parameters<UseGroupManagementResult['updateGroup']>[0]) => {
      if (!groupId) return null;
      const res = await groupService.updateGroup(groupId, patch);
      if (!res.ok) {
        notifyError(res);
        return null;
      }
      await onMutated();
      toast.success('协作群信息已更新');
      return res;
    },
    [groupId, onMutated],
  );

  const addMember = useCallback(
    async (actorId: string) => {
      if (!groupId) return false;
      const res = await groupMemberService.addMember(groupId, actorId);
      if (!res.ok) {
        notifyError(res);
        return false;
      }
      await onMutated();
      return true;
    },
    [groupId, onMutated],
  );

  const removeMember = useCallback(
    async (actorId: string) => {
      if (!groupId) return false;
      const res = await groupMemberService.removeMember(groupId, actorId);
      if (!res.ok) {
        notifyError(res);
        return false;
      }
      await onMutated();
      toast.success('已移除群成员');
      return true;
    },
    [groupId, onMutated],
  );

  const leaveGroup = useCallback(
    async (actorId: string) => {
      if (!groupId) return false;
      const res = await groupMemberService.leaveGroup(groupId, actorId);
      if (!res.ok) {
        notifyError(res);
        return false;
      }
      toast.success('已退出协作群');
      return true;
    },
    [groupId],
  );

  const createShare = useCallback(
    async (targetGroupId?: string) => {
      const shareGroupId = targetGroupId ?? groupId;
      if (!shareGroupId) {
        return {
          ok: false as const,
          error: { code: 'GROUP_MISSING', friendlyMessage: '未选择协作群', canRetry: false },
        };
      }
      const res = await invitationService.createGroupShare(shareGroupId);
      if (!res.ok) notifyError(res);
      return res;
    },
    [groupId],
  );

  const saveDingTalk = useCallback(
    async (config: GroupDingTalkConfig) => {
      if (!groupId) return false;
      // 多绑定冲突态不允许前端写入，需联系管理员。
      if (dingTalkBinding === 'conflict') {
        toast.error('当前群存在多条钉钉绑定，请联系管理员处理后再操作。');
        return false;
      }
      const existing = dingTalkBinding;
      const res = existing
        ? await channelBindingService.updateGroupDingTalkBindingConfig(existing.bindingId, config)
        : await channelBindingService.createGroupDingTalkBinding(groupId, config);
      if (!res.ok) {
        notifyError(res);
        return false;
      }
      await reloadDingTalk();
      toast.success(existing ? '钉钉机器人配置已更新' : '钉钉机器人绑定成功');
      return true;
    },
    [groupId, dingTalkBinding, reloadDingTalk],
  );

  const toggleDingTalkActive = useCallback(
    async (active: boolean) => {
      const existing = dingTalkBinding;
      if (!existing || existing === 'conflict') return false;
      const res = await channelBindingService.setGroupDingTalkBindingActive(existing.bindingId, active);
      if (!res.ok) {
        notifyError(res);
        return false;
      }
      await reloadDingTalk();
      toast.success(active ? '已启用钉钉机器人' : '已停用钉钉机器人');
      return true;
    },
    [dingTalkBinding, reloadDingTalk],
  );

  const deleteDingTalk = useCallback(async () => {
    const existing = dingTalkBinding;
    if (!existing || existing === 'conflict') return false;
    const res = await channelBindingService.deleteGroupDingTalkBinding(existing.bindingId);
    if (!res.ok) {
      notifyError(res);
      return false;
    }
    await reloadDingTalk();
    toast.success('已解绑钉钉机器人');
    return true;
  }, [dingTalkBinding, reloadDingTalk]);

  return {
    updateGroup,
    addMember,
    removeMember,
    leaveGroup,
    createShare,
    dingTalkBinding,
    dingTalkLoading,
    saveDingTalk,
    toggleDingTalkActive,
    deleteDingTalk,
  };
}

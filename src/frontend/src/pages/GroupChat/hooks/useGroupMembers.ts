/**
 * useGroupMembers - 协作群成员增删管理 Hook
 *
 * 与 useSessionMembers 的区别：
 * - useSessionMembers 管理「会话」内成员（POST/DELETE /sessions/{id}/members）
 * - 本 hook 管理「协作群」成员（POST/DELETE /groups/{id}/members）
 *
 * 后端接口：
 * - 添加 = POST /bcnproxy/groups/{group_id}/members  body: { bot_uuid, role? }
 * - 移除 = DELETE /bcnproxy/groups/{group_id}/members/{bot_uuid}
 *
 * 业务约束：
 * - 仅 driver/coordinator 有权操作；UI 层用 isOwner 控制按钮露出
 * - DM 群禁止操作；禁止移除 driver 自身
 */

import * as BcnController from '@/services/backend-api/BcnController';
import { useGroupChatStore } from '@/stores/groupChatStore';
import { extractErrorMessage } from '@/utils/requestErrorHandler';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import type { GroupMember } from '../types';

export interface AddGroupMemberItem {
  /** Bot UUID */
  bot_uuid: string;
  /** 名称（仅用于乐观更新与提示） */
  bot_name: string;
  /** 头像 */
  avatar_url?: string;
  /** 可选角色，不传由后端按群策略给默认值 */
  role?: BcnController.GroupMemberRoleApi;
}

export interface AddBatchResult {
  success: number;
  failed: number;
}

export function useGroupMembers() {
  const [isAdding, setIsAdding] = useState(false);
  const [isRemoving, setIsRemoving] = useState(false);

  /** 添加单个成员（不弹 toast，由调用方汇总） */
  const addOne = useCallback(
    async (groupId: string, item: AddGroupMemberItem): Promise<boolean> => {
      const resp = await BcnController.addGroupMember({
        group_id: groupId,
        bot_uuid: item.bot_uuid,
        role: item.role,
      });
      // 乐观写回 store：用接口确认的 role 兜底
      const { addMember } = useGroupChatStore.getState();
      const member: GroupMember = {
        id: item.bot_uuid,
        botUuid: item.bot_uuid,
        name: item.bot_name,
        type: 'bot',
        role: (resp.member?.role ||
          item.role ||
          'consultant') as GroupMember['role'],
        avatar: item.avatar_url,
      };
      addMember(groupId, member);
      return true;
    },
    [],
  );

  /** 批量添加（串行执行，汇总 toast） */
  const addGroupMembersBatch = useCallback(
    async (
      groupId: string,
      items: AddGroupMemberItem[],
    ): Promise<AddBatchResult> => {
      if (items.length === 0) return { success: 0, failed: 0 };

      let success = 0;
      let failed = 0;

      try {
        setIsAdding(true);
        for (const item of items) {
          try {
            await addOne(groupId, item);
            success += 1;
          } catch (error: any) {
            failed += 1;
            console.error(
              '[useGroupMembers] addGroupMembersBatch one failed:',
              item,
              error,
            );
          }
        }

        if (failed === 0) {
          toast.success(`已添加 ${success} 位成员`);
        } else if (success === 0) {
          toast.error(`添加失败 (${failed} 位)`);
        } else {
          toast.error(`部分添加失败：成功 ${success} 位,失败 ${failed} 位`);
        }
      } finally {
        setIsAdding(false);
      }

      return { success, failed };
    },
    [addOne],
  );

  /** 移除单个成员（DELETE） */
  const removeGroupMember = useCallback(
    async (
      groupId: string,
      botUuid: string,
      displayName?: string,
    ): Promise<boolean> => {
      try {
        setIsRemoving(true);
        await BcnController.deleteGroupMember({
          group_id: groupId,
          bot_uuid: botUuid,
        });
        const { removeMember } = useGroupChatStore.getState();
        removeMember(groupId, botUuid);
        toast.success(
          displayName ? `已将「${displayName}」移出协作群` : '已移出协作群',
        );
        return true;
      } catch (error: any) {
        console.error('[useGroupMembers] removeGroupMember failed:', error);
        toast.error(extractErrorMessage(error, '移除成员失败'));
        return false;
      } finally {
        setIsRemoving(false);
      }
    },
    [],
  );

  return {
    addGroupMembersBatch,
    removeGroupMember,
    isAdding,
    isRemoving,
  };
}

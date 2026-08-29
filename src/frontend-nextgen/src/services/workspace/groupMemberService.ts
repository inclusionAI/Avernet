import type { GroupView } from '@/domain/collaboration';
import {
  addGroupParticipant,
  deleteGroupParticipant,
} from '@/services/backendApi/collaboration/collaborationGroupController';
import { groupService } from './groupService';
import type { DomainError, DomainResult } from './identityService';

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

function conflictError(): DomainError {
  return toDomainError('GROUP_CONFLICT', '协作群状态已变更，请刷新后重试。');
}

export const groupMemberService = {
  /** 新增群成员；成功后拉取最新群详情以便 UI 就地刷新成员列表。 */
  async addMember(groupId: string, actorId: string): Promise<DomainResult<GroupView>> {
    try {
      await addGroupParticipant(groupId, actorId);
      return groupService.loadGroupDetail(groupId);
    } catch (err) {
      const status = (err as { status?: number })?.status;
      return {
        ok: false,
        error:
          status === 409 ? conflictError() : toDomainError('GROUP_MEMBER_ADD_FAILED', '添加群成员失败，请稍后重试。'),
      };
    }
  },

  /** 移除群成员；成功后拉取最新群详情。 */
  async removeMember(groupId: string, actorId: string): Promise<DomainResult<GroupView>> {
    try {
      await deleteGroupParticipant(groupId, actorId);
      return groupService.loadGroupDetail(groupId);
    } catch (err) {
      const status = (err as { status?: number })?.status;
      return {
        ok: false,
        error:
          status === 409
            ? conflictError()
            : toDomainError('GROUP_MEMBER_REMOVE_FAILED', '移除群成员失败，请稍后重试。'),
      };
    }
  },

  /** 退出协作群：与移除成员复用同一接口，从自己的 actorId 视角执行。 */
  async leaveGroup(groupId: string, actorId: string): Promise<DomainResult<null>> {
    try {
      await deleteGroupParticipant(groupId, actorId);
      return { ok: true, data: null };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      return {
        ok: false,
        error: status === 409 ? conflictError() : toDomainError('GROUP_LEAVE_FAILED', '退出协作群失败，请稍后重试。'),
      };
    }
  },
};

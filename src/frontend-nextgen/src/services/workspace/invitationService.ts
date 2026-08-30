import {
  acceptInvitation,
  createGroupInvitation,
  createSessionInvitation,
} from '@/services/backendApi/collaboration/collaborationInvitationController';
import type { DomainError, DomainResult } from './identityService';

const SHARE_TTL_SECONDS = 24 * 60 * 60;

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

function inviteBase(): string {
  // SSR / 测试环境兜底；运行时由前端 host 决定，不硬编码域名。
  if (typeof window === 'undefined') return 'http://localhost:8000';
  return window.location.origin;
}

function buildInvitationUrl(token: string, type: 'group' | 'session'): string {
  return `${inviteBase()}/workspace/invite/${encodeURIComponent(token)}?type=${type}`;
}

export interface InvitationAcceptResult {
  targetType: 'group' | 'session' | null;
  targetId: string;
  alreadyJoined: boolean;
}

export const invitationService = {
  async createGroupShare(groupId: string): Promise<DomainResult<{ invitationUrl: string }>> {
    try {
      const resp = await createGroupInvitation(groupId, {
        expires_in_seconds: SHARE_TTL_SECONDS,
      });
      const token = resp.data?.token;
      if (!token) {
        return {
          ok: false,
          error: toDomainError('SHARE_FAILED', '生成邀请链接失败，请稍后重试。'),
        };
      }
      return {
        ok: true,
        data: { invitationUrl: buildInvitationUrl(token, 'group') },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('SHARE_FAILED', '生成邀请链接失败，请稍后重试。'),
      };
    }
  },

  async createSessionShare(sessionId: string): Promise<DomainResult<{ invitationUrl: string }>> {
    try {
      const resp = await createSessionInvitation(sessionId, {
        expires_in_seconds: SHARE_TTL_SECONDS,
      });
      const token = resp.data?.token;
      if (!token) {
        return {
          ok: false,
          error: toDomainError('SHARE_FAILED', '生成邀请链接失败，请稍后重试。'),
        };
      }
      return {
        ok: true,
        data: { invitationUrl: buildInvitationUrl(token, 'session') },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('SHARE_FAILED', '生成邀请链接失败，请稍后重试。'),
      };
    }
  },

  // 预览不支持（openapi 无 lookup 接口）：用户点击确认加入前不调用任何后端接口。
  // 这里仅做本地非空/长度校验，确认框由 UI 直接调 acceptInvitation。
  async getAcceptPageState(token: string): Promise<DomainResult<{ isValid: true }>> {
    // openapi 没有 lookup 接口；此处仅做本地非空校验，确认框由 UI 直接调 acceptInvitation。
    // 不引入额外长度阈值，避免与真实 token 形态耦合（brief 测试用 'tk' 视为合法）。
    if (!token) {
      return {
        ok: false,
        error: toDomainError('INVITATION_INVALID', '该邀请已失效，请让群主重新生成。'),
      };
    }
    return { ok: true, data: { isValid: true } };
  },

  async acceptInvitation(token: string): Promise<DomainResult<InvitationAcceptResult>> {
    try {
      const resp = await acceptInvitation(token);
      const data = resp.data;
      const targetId = data?.target_id ?? '';
      if (!targetId || !data?.target_type) {
        return {
          ok: false,
          error: toDomainError('INVITATION_INVALID', '该邀请已失效，请让群主重新生成。'),
        };
      }
      return {
        ok: true,
        data: {
          targetType: data.target_type,
          targetId,
          alreadyJoined: Boolean(data.already_joined),
        },
      };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      // 409 already joined：后端把「已加入」做成错误返回，按场景归一为「已加入」成功。
      // 此时拿不到 target_id/target_type，UI 凭 alreadyJoined:true 跳转回协作群列表。
      if (status === 409) {
        return { ok: true, data: { targetType: null, targetId: '', alreadyJoined: true } };
      }
      if (status === 400 || status === 404 || status === 410) {
        return {
          ok: false,
          error: toDomainError('INVITATION_INVALID', '该邀请已失效，请让群主重新生成。'),
        };
      }
      if (status === 401 || status === 403) {
        return {
          ok: false,
          error: toDomainError('INVITATION_UNAUTHENTICATED', '请登录后再加入协作群。'),
        };
      }
      return {
        ok: false,
        error: toDomainError('INVITATION_ACCEPT_FAILED', '加入协作群失败，请稍后重试。'),
      };
    }
  },
};

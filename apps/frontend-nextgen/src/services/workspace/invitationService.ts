import type { MessageViewScope } from '@/domain/collaboration/types';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import {
  acceptInvitation,
  createGroupInvitation,
  createSessionInvitation,
  previewInvitation,
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
  // 路径形态：/workspace/invite/groups/{token} | /workspace/invite/sessions/{token}
  return `${inviteBase()}/workspace/invite/${type}s/${encodeURIComponent(token)}`;
}

export interface InvitationPreviewResult {
  isValid: true;
  targetType: 'group' | 'session';
  targetId: string;
  groupId: string;
  alreadyJoined: boolean;
}

export interface InvitationAcceptResult {
  targetType: 'group' | 'session' | null;
  targetId: string;
  alreadyJoined: boolean;
}

export const invitationService = {
  /**
   * 生成协作群邀请链接。expiresInSeconds 缺省回落 1 天（SHARE_TTL_SECONDS）；
   * 分享弹窗「链接有效期」选择值（dmore artboard-010）透传至此，接口契约可选参数。
   */
  async createGroupShare(
    groupId: string,
    expiresInSeconds: number = SHARE_TTL_SECONDS,
  ): Promise<DomainResult<{ invitationUrl: string }>> {
    try {
      const resp = await createGroupInvitation(groupId, {
        expires_in_seconds: expiresInSeconds,
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

  async createSessionShare(
    sessionId: string,
    expiresInSeconds: number = SHARE_TTL_SECONDS,
  ): Promise<DomainResult<{ invitationUrl: string }>> {
    try {
      const resp = await createSessionInvitation(sessionId, {
        expires_in_seconds: expiresInSeconds,
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

  /** 只读预检；成员身份由 BCS 的当前 Human 权威快照判定，不调用接受接口。 */
  async getAcceptPageState(token: string): Promise<DomainResult<InvitationPreviewResult>> {
    if (!token) {
      return {
        ok: false,
        error: toDomainError('INVITATION_INVALID', '该邀请已失效，请让群主重新生成。'),
      };
    }
    try {
      const resp = await previewInvitation(token);
      const data = resp.data;
      if (
        !data ||
        (data.target_type !== 'group' && data.target_type !== 'session') ||
        typeof data.target_id !== 'string' || !data.target_id.trim() ||
        typeof data.group_id !== 'string' || !data.group_id.trim() ||
        typeof data.already_joined !== 'boolean'
      ) {
        return {
          ok: false,
          error: { code: 'INVITATION_PREVIEW_FAILED', friendlyMessage: '邀请校验失败，请稍后重试。', canRetry: true },
        };
      }
      return {
        ok: true,
        data: {
          isValid: true,
          targetType: data.target_type,
          targetId: data.target_id,
          groupId: data.group_id,
          alreadyJoined: data.already_joined,
        },
      };
    } catch (err) {
      // Release 客户端由队列统一提示；预检错误交给落地页内联处理，不影响已执行的认证门禁。
      const toastKey = (err as { toastKey?: string })?.toastKey;
      if (toastKey) useErrorNotifyStore.getState().cancel(toastKey);
      const status = (err as { status?: number })?.status;
      if (status === 400 || status === 404 || status === 410) {
        return {
          ok: false,
          error: toDomainError('INVITATION_INVALID', '该邀请已失效，请让群主重新生成。'),
        };
      }
      if (status === 401 || status === 403) {
        return {
          ok: false,
          error: toDomainError('INVITATION_UNAUTHENTICATED', '请登录后再查看协作邀请。'),
        };
      }
      return {
        ok: false,
        error: { code: 'INVITATION_PREVIEW_FAILED', friendlyMessage: '邀请校验失败，请稍后重试。', canRetry: true },
      };
    }
  },

  async acceptInvitation(
    token: string,
    messageViewScope?: MessageViewScope,
  ): Promise<DomainResult<InvitationAcceptResult>> {
    try {
      const resp = await acceptInvitation(
        token,
        messageViewScope ? { message_view_scope: messageViewScope } : undefined,
      );
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

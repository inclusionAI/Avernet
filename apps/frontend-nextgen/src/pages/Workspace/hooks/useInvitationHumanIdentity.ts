import { getCapabilities } from '@/capabilities';
import { normalizeOpenApiUserId } from '@/domain/userIdentity';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';

export type InvitationHumanIdentityStatus = 'loading' | 'ready' | 'error';

/**
 * 邀请预检只观察现有认证/身份状态，绝不主动加载 mine 或创建 Human。
 * getHumanIdentity 是同步 capability；订阅两处既有状态使全局认证晚到时能继续跳转。
 */
export function useInvitationHumanIdentity(): {
  userId: string | null;
  status: InvitationHumanIdentityStatus;
  error?: string;
} {
  const identities = useWorkspaceStore((s) => s.identities);
  const oauthUser = useExternalAuthStore((s) => s.user);
  const authStatus = useExternalAuthStore((s) => s.status);
  const checkingAuth = useExternalAuthStore((s) => s.isCheckingAuth);
  const capabilities = getCapabilities();
  const externalAuth = capabilities.getLoginStrategy().value === 'oauth-provider';
  if (externalAuth && (checkingAuth || authStatus === 'unknown')) {
    return { userId: null, status: 'loading' };
  }
  if (externalAuth && (authStatus === 'unauthenticated' || authStatus === 'error')) {
    return { userId: null, status: 'error', error: '当前用户身份未就绪，请登录或刷新页面后重试。' };
  }
  const userId = normalizeOpenApiUserId(capabilities.getHumanIdentity().value?.userId);
  // 默认 capability 在身份列表只有 Bot 时也有展示兜底；不能将该 Bot 当作邀请的 Human。
  const knownHuman = !externalAuth ||
    normalizeOpenApiUserId(oauthUser?.userId) === userId ||
    identities.some((identity) => identity.kind === 'user' && normalizeOpenApiUserId(identity.id) === userId);
  if (userId && knownHuman) return { userId, status: 'ready' };
  return { userId: null, status: 'error', error: '无法确认当前用户身份，请刷新页面后重试。' };
}

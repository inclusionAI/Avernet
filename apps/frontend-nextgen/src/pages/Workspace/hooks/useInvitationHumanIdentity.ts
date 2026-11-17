import { getCapabilities } from '@/capabilities';
import { normalizeOpenApiUserId } from '@/domain/userIdentity';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, useState } from 'react';

export type InvitationHumanIdentityStatus = 'loading' | 'ready' | 'error';

interface InvitationHumanIdentity {
  userId: string | null;
  status: InvitationHumanIdentityStatus;
  error?: string;
}

const IDENTITY_INITIALIZATION_TIMEOUT_MS = 10_000;
const IDENTITY_OBSERVATION_INTERVAL_MS = 200;

function readCurrentIdentity(): InvitationHumanIdentity {
  const { identities } = useWorkspaceStore.getState();
  const { user: oauthUser, status: authStatus, isCheckingAuth: checkingAuth } = useExternalAuthStore.getState();
  const capabilities = getCapabilities();
  const externalAuth = capabilities.getLoginStrategy().value === 'oauth-provider';
  if (externalAuth && (checkingAuth || authStatus === 'unknown')) {
    return { userId: null, status: 'loading' };
  }
  if (externalAuth && (authStatus === 'unauthenticated' || authStatus === 'error')) {
    return { userId: null, status: 'error', error: '当前用户身份未就绪，请登录或刷新页面后重试。' };
  }
  const human = capabilities.getHumanIdentity();
  const userId = normalizeOpenApiUserId(human.value?.userId);
  // 默认 capability 在身份列表只有 Bot 时也有展示兜底；不能将该 Bot 当作邀请的 Human。
  const knownHuman = !externalAuth ||
    normalizeOpenApiUserId(oauthUser?.userId) === userId ||
    identities.some((identity) => identity.kind === 'user' && normalizeOpenApiUserId(identity.id) === userId);
  if (userId && knownHuman) return { userId, status: 'ready' };
  // 网关身份缓存可能晚于邀请预检初始化；空缓存并不代表身份加载失败。
  if (!externalAuth && human.status === 'available' && !userId) return { userId: null, status: 'loading' };
  return { userId: null, status: 'error', error: '无法确认当前用户身份，请刷新页面后重试。' };
}

/**
 * 邀请预检只观察现有认证/身份状态，绝不主动加载 mine 或创建 Human。
 * Store 更新立即生效；同步 capability 没有通知接口，初始化期间有限轮询缓存。
 */
export function useInvitationHumanIdentity(): InvitationHumanIdentity {
  useWorkspaceStore((s) => s.identities);
  useExternalAuthStore((s) => s.user);
  useExternalAuthStore((s) => s.status);
  useExternalAuthStore((s) => s.isCheckingAuth);
  const identity = readCurrentIdentity();
  const waiting = identity.status === 'loading';
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    setElapsed(0);
    if (!waiting) return;
    const startedAt = Date.now();
    const timer = setInterval(() => {
      const duration = Date.now() - startedAt;
      setElapsed(duration);
      if (duration >= IDENTITY_INITIALIZATION_TIMEOUT_MS) clearInterval(timer);
    }, IDENTITY_OBSERVATION_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [waiting]);

  if (waiting && elapsed >= IDENTITY_INITIALIZATION_TIMEOUT_MS) {
    return { userId: null, status: 'error', error: '当前用户身份加载超时，请刷新页面后重试。' };
  }
  return identity;
}

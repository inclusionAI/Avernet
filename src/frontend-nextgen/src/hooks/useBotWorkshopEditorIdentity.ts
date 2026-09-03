import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useIdentityStore } from '@/stores/identityStore';
import { useEffect } from 'react';

/**
 * Bot 工坊编辑二级页的请求身份门禁。
 *
 * 预发优先复用 Internal Overlay 从 Tern 运行时解析出的 human 工号；Open Core
 * 则复用既有 identityService 加载结果。身份写入统一请求 Store 后，编辑页各
 * Controller 继续通过 backendRequest 自动追加 user_id，不扩散内部身份实现。
 */
export function useBotWorkshopRequestIdentity(): {
  ready: boolean;
  loading: boolean;
  error?: string;
  userId?: string;
} {
  const { identity, status, error } = useHumanIdentity();
  const userId = identity?.userId.trim();

  useEffect(() => {
    if (status === 'ready' && userId) {
      useIdentityStore.getState().setCurrentIdentityId(userId);
    }
  }, [status, userId]);

  if (status === 'loading') return { ready: false, loading: true, error: undefined };
  if (status === 'error') return { ready: false, loading: false, error: error ?? '当前用户身份加载失败' };
  if (!userId) return { ready: false, loading: false, error: '未获取到当前用户身份' };
  return { ready: true, loading: false, error: undefined, userId };
}

/** 编辑二级页兼容命名；身份门禁由 Bot 工坊一级页和编辑页共同复用。 */
export const useBotWorkshopEditorIdentity = useBotWorkshopRequestIdentity;

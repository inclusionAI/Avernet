import { ENABLE_TEST_USER, TEST_USER_IDENTITY, TEST_USER_IDENTITY_ID, workspaceService } from '@/services/workspace';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect } from 'react';
import { toast } from 'sonner';

/** 挂载时拉取真实身份；失败时按开关兜底注入测试用户并提示。 */
export function useWorkspaceIdentityBootstrap() {
  useEffect(() => {
    let cancelled = false;
    void workspaceService.initWorkspace().then((res) => {
      if (cancelled) return;
      if (res.ok) return;
      toast.error(ENABLE_TEST_USER ? '加载可协作身份失败，已切换到测试用户模式。' : '加载可协作身份失败，请刷新重试。');
      if (!ENABLE_TEST_USER) return;
      const current = useWorkspaceStore.getState().identities;
      const hasTestUser = current.some((i) => i.id === TEST_USER_IDENTITY_ID);
      if (hasTestUser) return;
      useWorkspaceStore.getState().setIdentities([TEST_USER_IDENTITY], TEST_USER_IDENTITY_ID);
    });
    return () => {
      cancelled = true;
    };
  }, []);
}

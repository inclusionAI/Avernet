/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * useHumanIdentity - 当前 human 身份收口 Hook
 *
 * 身份来源差异由 AppExt.authAdapter.getCurrentUser 决定（差异类型 C）：
 * - 开源形态：走 /bcnproxy/me（defaultAuthAdapter）
 * - 内部形态：读 window.__TERN__.user（src/internal/auth.ts 注入）
 *
 * 挂载时解析身份并写入 userStore，作为 BCN 协作页面（GroupChat / SessionOnlyPage /
 * GroupChatPage / TopNavBar）的统一身份源，替代直读 useModel('@@initialState')。
 */
import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';
import { useUserStore } from '@/stores/userStore';
import { useEffect } from 'react';

export function useHumanIdentity() {
  const userId = useUserStore((state) => state.userId);
  const nickName = useUserStore((state) => state.nickName);
  const avatarUrl = useUserStore((state) => state.avatarUrl);
  const setUserId = useUserStore((state) => state.setUserId);
  const setNickName = useUserStore((state) => state.setNickName);
  const setAvatarUrl = useUserStore((state) => state.setAvatarUrl);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const identity = await getExt(AppExt).authAdapter.getCurrentUser();
        if (cancelled || !identity) return;
        if (identity.userId) setUserId(identity.userId);
        if (identity.nickName) setNickName(identity.nickName);
        if (identity.avatarUrl !== undefined) setAvatarUrl(identity.avatarUrl);
      } catch (error) {
        console.error('[useHumanIdentity] getCurrentUser 失败:', error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setUserId, setNickName, setAvatarUrl]);

  return { userId, nickName, avatarUrl };
}

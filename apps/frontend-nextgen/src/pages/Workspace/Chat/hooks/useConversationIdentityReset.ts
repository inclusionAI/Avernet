// useConversationIdentityReset —— 登录用户变化才整仓重置(不随挂载/重渲染触发;首次出现不算切换)
// (2026-10-21,自 ConversationPage 拆出):缓存按 botId 记忆、无用户来源,防止上一用户的
// 缓存/选中串号(控制器裁定)。返回 identityChanged 供调用方在切换瞬间停用旧用户会话装配
// (identityChanged 防串话,expand-selection 竞态保护)。
import { useEffect, useRef } from 'react';
import { useConversationStore } from '@/stores/conversationStore';

export function useConversationIdentityReset(userId: string | null): { identityChanged: boolean } {
  const previousUserIdRef = useRef<string | null>(null);
  const identityChanged = previousUserIdRef.current !== null && previousUserIdRef.current !== userId;
  useEffect(() => {
    const previousUserId = previousUserIdRef.current;
    previousUserIdRef.current = userId;
    if (previousUserId !== null && previousUserId !== userId) {
      useConversationStore.getState().reset();
    }
  }, [userId]);
  return { identityChanged };
}
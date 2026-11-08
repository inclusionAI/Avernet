import type { HumanIdentity } from '@/capabilities';
import type { IdentityView } from '@/domain/collaboration/types';
import { useMemo } from 'react';

/** 登录 Human 的聊天查看者视图，不参与 Bot 身份或会话选择。 */
export function useConversationViewer(human: HumanIdentity | null): IdentityView | null {
  return useMemo<IdentityView | null>(
    () =>
      human
        ? {
            id: human.userId,
            kind: 'user',
            displayName: human.displayName,
            avatarUrl: human.avatarUrl,
            online: human.online,
          }
        : null,
    [human],
  );
}

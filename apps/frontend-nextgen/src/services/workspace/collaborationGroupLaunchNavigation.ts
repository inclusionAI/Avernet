import { fullCollaborationUrl } from '@/domain/collaborationPresentation';
import type { CollaborationGroupLaunchReceipt } from './collaborationGroupLaunchService';

export function navigateToLaunchedGroup(receipt: CollaborationGroupLaunchReceipt): void {
  // 重建身份、群列表与会话上下文，避免同一路由沿用建群前的列表或原对话 Bot。
  window.location.assign(
    fullCollaborationUrl({
      currentIdentityId: receipt.driverBotId,
      groupId: receipt.groupId,
      sessionId: receipt.initialSessionId,
      membership: 'direct' as const,
    }),
  );
}

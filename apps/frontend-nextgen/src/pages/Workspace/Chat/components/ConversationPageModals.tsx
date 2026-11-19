// 对话页页面级弹窗:老版分享链接好友申请确认 + 好友 Bot 移除二次确认。
// 拆出以控制 ConversationPage 行数(TC-G005 300 行门禁),不改页面装配语义。
import type { HumanIdentity } from '@/capabilities';
import { FriendRequestConfirmModal } from '@/components/CollaborationSquare/FriendRequestConfirmModal';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import type { LegacyShareLinkGuard } from '../hooks/useLegacyShareLinkGuard';
import { FriendRemovalConfirmDialog } from './FriendRemovalConfirmDialog';

export interface ConversationPageModalsProps {
  human: HumanIdentity | null;
  legacyGuard: LegacyShareLinkGuard;
  removalTarget: ChatBotView | null;
  removalLoading: boolean;
  onConfirmRemoval(): void;
  onCancelRemoval(): void;
}

export function ConversationPageModals({
  human,
  legacyGuard,
  removalTarget,
  removalLoading,
  onConfirmRemoval,
  onCancelRemoval,
}: ConversationPageModalsProps) {
  return (
    <>
      {legacyGuard.legacyBot && human && (
        <FriendRequestConfirmModal
          bot={legacyGuard.legacyBot}
          actor={{ type: 'human', id: human.userId }}
          actorName={human.displayName || human.userId}
          onClose={legacyGuard.onClose}
          onConfirm={legacyGuard.onConfirm}
        />
      )}
      <FriendRemovalConfirmDialog
        open={removalTarget !== null}
        botName={removalTarget?.displayName}
        loading={removalLoading}
        onConfirm={onConfirmRemoval}
        onCancel={onCancelRemoval}
      />
    </>
  );
}
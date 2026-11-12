import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import type { UseGroupChatHeaderPanelsResult } from '../hooks/useGroupChatHeaderPanels';
import type { GroupWorkspaceAreaProps } from '../GroupWorkspaceArea.types';
import type { GroupPanelKind } from './GroupHeader';
import { GroupHistorySidebar } from './GroupChatPane/GroupHistorySidebar';
import { SharedInfoSidebar } from './GroupChatPane/SharedInfoSidebar';
import { SessionFilesSidebar } from './GroupChatPane/SessionFilesSidebar';

interface GroupSessionSidebarsProps extends Pick<GroupWorkspaceAreaProps, 'userIdentityId' | 'userIdentityName'> {
  activePanel: GroupPanelKind;
  session: SessionView;
  group: GroupView | null;
  activeIdentity: IdentityView | null;
  search: UseGroupChatHeaderPanelsResult['historySearch'];
  onLocate: UseGroupChatHeaderPanelsResult['locateMessage'];
  onClose: () => void;
}

export function GroupSessionSidebars({
  activePanel,
  session,
  group,
  activeIdentity,
  userIdentityId,
  userIdentityName,
  search,
  onLocate,
  onClose,
}: GroupSessionSidebarsProps) {
  return (
    <>
      {/* 资源管理/共享信息/历史消息：对话区内联副屏（open 开合、宽度记忆），与单聊侧同形。 */}
      <SessionFilesSidebar
        open={activePanel === 'resources'}
        sessionId={session.sessionId}
        participants={session.participants}
        authenticatedUser={userIdentityId ? { userId: userIdentityId, name: userIdentityName } : null}
        onClose={onClose}
      />
      {activePanel === 'sharedInfo' && (
        <SharedInfoSidebar
          open
          view={{
            groupId: session.groupId,
            sessionId: session.sessionId,
            view_bot_id: activeIdentity?.kind === 'bot' ? activeIdentity.id : undefined,
          }}
          viewerId={activeIdentity?.id ?? userIdentityId}
          names={new Map([
            ...(group?.participants ?? []).map(({ actorId, name }) => [actorId, name] as const),
            ...session.participants.map(({ actorId, name }) => [actorId, name] as const),
            ...(userIdentityId && userIdentityName ? [[userIdentityId, userIdentityName] as const] : []),
          ])}
          onClose={onClose}
        />
      )}
      <GroupHistorySidebar
        open={activePanel === 'history'}
        search={search}
        onLocate={onLocate}
        onClose={onClose}
      />
    </>
  );
}

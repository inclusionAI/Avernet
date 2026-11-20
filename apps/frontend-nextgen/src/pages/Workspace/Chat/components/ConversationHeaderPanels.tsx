import { TaskEscortDemoPanel } from '@/components/Workspace/TaskEscortDemo';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { useRef } from 'react';
import type { UseChatHeaderPanelsResult } from '../hooks/useChatHeaderPanels';
import { ChatHeaderRightPanel, SessionDetailPanel } from './ChatHeaderRightPanel';
import { SessionHistorySearchPanel } from './SessionHistorySearchPanel';

interface ConversationHeaderPanelsProps {
  headerPanels: UseChatHeaderPanelsResult;
  session: BotChatSessionView | null;
  botName?: string;
}

/** 保留详情/历史内联面板的收起过渡；护航常挂载，草稿仅属于当前 Bot / 会话。 */
export function ConversationHeaderPanels({ headerPanels, session, botName }: ConversationHeaderPanelsProps) {
  const openPanel = headerPanels.openPanel === 'escort' ? null : headerPanels.openPanel;
  const renderPanelKindRef = useRef<'detail' | 'history'>('detail');
  if (openPanel) renderPanelKindRef.current = openPanel;
  const renderPanelKind = openPanel ?? renderPanelKindRef.current;

  return (
    <>
      <ChatHeaderRightPanel
        open={openPanel !== null}
        title={renderPanelKind === 'history' ? '历史消息' : '会话详情'}
        onClose={headerPanels.closePanel}
      >
        {renderPanelKind === 'history' ? (
          <SessionHistorySearchPanel search={headerPanels.historySearch} onLocate={headerPanels.locateMessage} />
        ) : (
          <SessionDetailPanel session={session} />
        )}
      </ChatHeaderRightPanel>
      <TaskEscortDemoPanel
        key={JSON.stringify([session?.botId, session?.sessionId])}
        open={headerPanels.openPanel === 'escort'}
        onOpenChange={(open) => {
          if (!open) headerPanels.closePanel();
        }}
        botName={botName}
      />
    </>
  );
}

import { TaskEscortPanel } from '@/components/Workspace/TaskEscortPanel';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { useRef } from 'react';
import type { UseChatHeaderPanelsResult } from '../hooks/useChatHeaderPanels';
import { ChatHeaderRightPanel, SessionDetailPanel } from './ChatHeaderRightPanel';
import { SessionHistorySearchPanel } from './SessionHistorySearchPanel';

interface ConversationHeaderPanelsProps {
  headerPanels: UseChatHeaderPanelsResult;
  session: BotChatSessionView | null;
  botName?: string;
  taskComposerContext: TaskComposerContext | null;
}

/** 保留详情/历史内联面板的收起过渡；实时护航复用任务菜单上下文。 */
export function ConversationHeaderPanels({ headerPanels, session, botName, taskComposerContext }: ConversationHeaderPanelsProps) {
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
      <TaskEscortPanel
        open={headerPanels.openPanel === 'escort'}
        onOpenChange={(open) => {
          if (!open) headerPanels.closePanel();
        }}
        botName={botName}
        context={taskComposerContext}
      />
    </>
  );
}

// ConversationInteractiveStage —— 交互式(Human-facing)聊天主舞台。
// Task 7 从旧混合 Workspace 页抽出的 ChatPanel 完整装配(发送/任务/文件/模型/
// 历史分页/Agent Coding 引导),保持既有能力不裁剪。分支无关:`origin=others`
// 的只读分支由页边界(Task 8 ConversationPage)裁决,本组件只渲染模型给的内容;
// no-session / Agent Coding 分支按模型字段降级。
import { ChatPanel } from '@/components/Workspace/ChatPanel';
import { BotModelSelectorContainer } from '@/components/Workspace/ChatPanel/BotModelSelector';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import { AgentCodingGuide } from '@/pages/Workspace/components/AgentCodingGuide';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import type { PanelHandle } from '@tc-chat/core';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import type { RefObject } from 'react';
import type { ConversationInteractiveChatModel } from './hooks/useConversationInteractiveChat';

/** ChatPanel 以非空 RefObject 声明 ref prop;实际均为 useRef<T>(null) 形态的静态换形。 */
const asPanelRef = (ref: RefObject<PanelHandle | null>): RefObject<PanelHandle> => ref as RefObject<PanelHandle>;
const asSenderRef = (ref: RefObject<SenderRef | null>): RefObject<SenderRef> => ref as RefObject<SenderRef>;

export function ConversationInteractiveStage(props: {
  model: ConversationInteractiveChatModel;
  onOpenSessionList(): void;
  onOpenAgentCodingBot(bot: ChatBotView): void;
}): JSX.Element {
  const { model, onOpenSessionList, onOpenAgentCodingBot } = props;
  const { botChat, fileFeature, taskExecution } = model;
  return (
    <>
      {model.agentCodingBot ? (
        <AgentCodingGuide bot={model.agentCodingBot} onOpen={onOpenAgentCodingBot} />
      ) : (
        <ChatPanel
          target={model.target}
          viewer={model.viewer}
          sessionTitle={model.selectedSession?.title}
          authenticatedUserId={model.authenticatedUserId}
          authenticatedUserName={model.authenticatedUserName}
          userAvatarUrl={model.userAvatarUrl}
          messages={botChat.chat.messages}
          isRequesting={botChat.chat.isRequesting}
          isLoadingMessages={botChat.chat.isDefaultMessagesRequesting}
          connectionStatus={botChat.connectionStatus}
          retryCount={botChat.chat.retryCount}
          supportState={botChat.supportState}
          draft={model.draft}
          panelRef={asPanelRef(model.panelRef)}
          chatBridge={model.chatBridge}
          onDraftChange={model.onDraftChange}
          onSend={model.handleSend}
          onStop={botChat.stop}
          onReconnect={() => void botChat.reconnect()}
          onPanelAction={model.handlePanelAction}
          onOpenSessionList={onOpenSessionList}
          historyPagination={{
            hasMore: botChat.hasMoreHistory,
            isLoading: botChat.isLoadingMoreHistory,
            onLoadMore: () => void botChat.loadMoreHistory(),
          }}
          onManageFiles={model.desktopFileFeaturesHidden ? undefined : fileFeature.openFileDrawer}
          modelSelector={
            <BotModelSelectorContainer
              chatBots={model.chatBots}
              session={model.selectedSession}
              activeIdentityId={model.requesterIdentityId}
              onSessionModelChange={model.onSessionModelChange}
            />
          }
          taskComposer={
            <ComposerCapabilitiesMenu
              execution={taskExecution}
              enableWorkflow
              onUpload={model.desktopFileFeaturesHidden ? undefined : fileFeature.openUpload}
              disabled={model.taskComposerDisabled}
              disabledReason={model.taskComposerDisabledReason}
              selectedWorkflow={taskExecution.selectedWorkflow}
              pendingDynamic={taskExecution.pendingDynamic}
              onWorkflowSelected={taskExecution.selectWorkflow}
              onDynamicSelected={taskExecution.selectDynamic}
              onClearSelection={taskExecution.clearSelection}
            />
          }
          fileChip={model.desktopFileFeaturesHidden ? undefined : fileFeature.fileChip}
          command={fileFeature.command}
          fileToolbar={fileFeature.fileToolbar}
          senderRef={fileFeature.senderRef}
          mode="bot"
          interactive
          inputRef={asSenderRef(model.inputRef)}
        />
      )}
      {fileFeature.featureNode}
    </>
  );
}

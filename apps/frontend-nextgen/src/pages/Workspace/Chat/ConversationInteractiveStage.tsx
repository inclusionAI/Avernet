// ConversationInteractiveStage —— 交互式(Human-facing)聊天主舞台。
// Task 7 从旧混合 Workspace 页抽出的 ChatPanel 完整装配(发送/任务/文件/模型/
// 历史分页),保持既有能力不裁剪。分支无关:`origin=others`
// 的只读分支由页边界(Task 8 ConversationPage)裁决,本组件只渲染模型给的内容;
// 未选中会话由 ConversationSessionPlaceholder 承接；Agent Coding 已由页边界互斥地
// 交给 CodingBotStageBoundary，本组件只负责普通 OpenClaw Runtime。
import { ConversationComposerHost } from '@/components/Workspace/ConversationComposerHost';
import { AgentComputePanel } from '@/components/agentswarm/AgentComputePanel';
import { useAgentComputePanelStore } from '@/stores/agentComputePanelStore';
import { ChatPanel } from '@/components/Workspace/ChatPanel';
import { BotModelSelectorContainer } from '@/components/Workspace/ChatPanel/BotModelSelector';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import type { PanelHandle } from '@tc-chat/core';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import type { ReactNode, RefObject } from 'react';
import type { ConversationInteractiveChatModel } from './hooks/useConversationInteractiveChat';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

/** ChatPanel 以非空 RefObject 声明 ref prop;实际均为 useRef<T>(null) 形态的静态换形。 */
const asPanelRef = (ref: RefObject<PanelHandle | null>): RefObject<PanelHandle> => ref as RefObject<PanelHandle>;
const asSenderRef = (ref: RefObject<SenderRef | null>): RefObject<SenderRef> => ref as RefObject<SenderRef>;

export function ConversationInteractiveStage(props: {
  model: ConversationInteractiveChatModel;
  onOpenSessionList(): void;
  /** 头部五图标列（chat-header-panels）：页面装配，取代原「会话文件」单图标位。 */
  headerActions?: ReactNode;
  /** 头部右缘面板（会话详情/历史消息）：由页面按开合态装配（chat-header-panels）。 */
  headerRightPanels?: ReactNode;
  /** 历史面板结果点击的定位高亮消息 id（透传消息列表滚动定位）。 */
  highlightMessageId?: string | null;
  /** 副屏开合态 + toggle + 真实态同步入口（chat-header-panels）：供 ChatPanel 渲染副屏右上「收起」悬浮钮。 */
  sidePaneOpen?: boolean;
  onToggleSidePane?: () => void;
  onSidePaneIsOpenChange?: (open: boolean) => void;
}): JSX.Element {
  const {
    model,
    onOpenSessionList,
    headerActions,
    headerRightPanels,
    highlightMessageId,
    sidePaneOpen,
    onToggleSidePane,
    onSidePaneIsOpenChange,
  } = props;
  const { botChat, fileFeature, taskExecution, presentation } = model;
  const composerBot = presentation.composerExtensions
    ? model.chatBots.find((bot) => bot.botId === model.selectedSession?.botId) ?? null
    : null;
  const composerSession = presentation.composerExtensions ? model.selectedSession : null;
  return (
    <div
      className="relative isolate flex min-h-0 min-w-0 flex-1"
      data-workspace-conversation="true"
      data-guide-target="chat-input"
    >
      <ConversationComposerHost
        bot={composerBot}
        session={composerSession}
        command={presentation.fileFeatures ? fileFeature.command : undefined}
        onInvokeCommand={
          presentation.fileFeatures
            ? (command) => {
                // 往草稿插入命令 token 并聚焦输入框，唤起 Sender 命令面板（capability 注入的
                // 平台入口使用，如 /knowledge；token 前按草稿现状补空格分隔保证可解析）。
                // 尾部不补空格：面板触发正则要求光标紧邻 /token（/(\S*)$），尾空格会断开
                // 匹配导致面板不唤起（2026-11-04 实测反馈修正）。
                const sender = fileFeature.senderRef.current;
                if (!sender) return;
                const current = sender.getValue();
                const separator = current && !/\s$/.test(current) ? ' ' : '';
                sender.insert(`${separator}${command}`);
                sender.focus();
                // 命令面板唤起即止（2026-11-04 拍板）：tc-chat Sender 无命令子面板的程序直达
                // API（navPath 为 SDK 内部状态），模拟键盘实测不可靠已移除——入口等效
                // "手输 /knowledge"的面板形态（过滤出 knowledge 指令项），用户再点一项
                // 进入知识库列表；SDK 侧支持直达 API 后可回归完全等效。
              }
            : undefined
        }
        onSend={model.handleSend}
      >
        {(composer) => (
          <ChatPanel
            target={model.target}
            viewer={model.viewer}
            sessionTitle={model.selectedSession?.title}
            sessionCode={model.selectedSession?.sessionId}
            authenticatedUserId={model.authenticatedUserId}
            authenticatedUserName={model.authenticatedUserName}
            userAvatarUrl={model.userAvatarUrl}
            messages={botChat.chat.messages}
            headerActions={headerActions}
            highlightMessageId={highlightMessageId}
            sidePaneOpen={presentation.sidePane ? sidePaneOpen : false}
            onToggleSidePane={presentation.sidePane ? onToggleSidePane : undefined}
            onSidePaneIsOpenChange={presentation.sidePane ? onSidePaneIsOpenChange : undefined}
            sidePaneEnabled={presentation.sidePane}
            isRequesting={botChat.chat.isRequesting}
            isLoadingMessages={botChat.chat.isDefaultMessagesRequesting}
            historyReady={botChat.historyReady}
            connectionStatus={botChat.connectionStatus}
            retryCount={botChat.chat.retryCount}
            supportState={botChat.supportState}
            draft={model.draft}
            panelRef={asPanelRef(model.panelRef)}
            chatBridge={model.chatBridge}
            onDraftChange={model.onDraftChange}
            onSend={composer.onSend}
            composerHeader={presentation.composerExtensions ? composer.header : undefined}
            onStop={botChat.stop}
            onReconnect={() => void botChat.reconnect()}
            onPanelAction={model.handlePanelAction}
            onOpenSessionList={onOpenSessionList}
            historyPagination={{
              hasMore: botChat.hasMoreHistory,
              isLoading: botChat.isLoadingMoreHistory,
              onLoadMore: () => void botChat.loadMoreHistory(),
            }}
            modelSelector={
              presentation.modelSelector ? (
                <BotModelSelectorContainer
                  chatBots={model.chatBots}
                  session={model.selectedSession}
                  activeIdentityId={model.requesterIdentityId}
                  onSessionModelChange={model.onSessionModelChange}
                />
              ) : undefined
            }
            taskComposer={
              presentation.taskComposer ? (
                <ComposerCapabilitiesMenu
                  onOpened={() => Tracert.click(Points.workspaceChat.openWorkflowMenu)}
                  execution={taskExecution}
                  enableWorkflow
                  onUpload={model.desktopFileFeaturesHidden ? undefined : fileFeature.openUpload}
                  disabled={model.taskComposerDisabled}
                  disabledReason={model.taskComposerDisabledReason}
                  selections={taskExecution.selections}
                  onWorkflowSelected={taskExecution.selectWorkflow}
                  onDynamicSelected={taskExecution.selectDynamic}
                  onRemoveSelection={taskExecution.removeSelection}
                  /* 能力集只读查看：我的/团队空间 Bot 装配（好友 Bot 为 null 不渲染文案区）。 */
                  capabilitySets={fileFeature.capabilitySets}
                  /* 平台注入的额外菜单行（capability 覆盖式插槽），如知识库引用入口。 */
                  extraMenuRows={composer.extraMenuRows}
                  onAgentCompute={
                    composerSession?.sessionId
                      ? () => useAgentComputePanelStore.getState().open(composerSession.sessionId)
                      : undefined
                  }
                />
              ) : undefined
            }
            fileChip={
              presentation.fileFeatures && !model.desktopFileFeaturesHidden ? fileFeature.fileChip : undefined
            }
            command={presentation.fileFeatures ? composer.command : undefined}
            fileToolbar={presentation.fileFeatures ? fileFeature.fileToolbar : undefined}
            composerPlaceholder={composer.placeholder ?? presentation.composerPlaceholder}
            senderRef={fileFeature.senderRef}
            mode="bot"
            interactive
            inputRef={asSenderRef(model.inputRef)}
          />
        )}
      </ConversationComposerHost>
      {/* 计算任务副屏：与对话并排（xl+ 占宽，窄屏覆盖），按会话隔离。 */}
      <AgentComputePanel
        sessionId={composerSession?.sessionId ?? ''}
        userId={model.requesterIdentityId}
        defaultIdentityBotId={composerBot?.realBotId ?? composerBot?.botId ?? null}
        enabled={Boolean(composerSession?.sessionId)}
      />
      {presentation.fileFeatures ? fileFeature.featureNode : null}
      {headerRightPanels}
    </div>
  );
}

import { Button, Empty, Skeleton } from '@/components/ui';
import type { GroupView, IdentityView, ParticipantMode, SessionView } from '@/domain/collaboration';
import { useTaskExecuteFromCard } from '@/hooks/useTaskExecuteFromCard';
import { useTaskExecution } from '@/hooks/useTaskExecution';
import { useCollabPanel } from '@/pages/Workspace/hooks/useCollabPanel';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { resolveUserId } from '@/services/workspace/botSessionService';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import type { GroupChatState } from '@/services/workspace/groupChatProvider';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import type { ProviderConnectionStatus, UseChatResult } from '@tc-chat/adapters';
import type { ChatBridge, PanelAction, PanelHandle } from '@tc-chat/core';
import type { MentionConfig } from '@tc-chat/ui';
import { BubbleList } from '@tc-chat/ui/es/BubbleList';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import { RefreshCw, Sparkles } from 'lucide-react';
import { useEffect, useMemo, useState, type RefObject } from 'react';
import { GroupHeader, type GroupPanelKind } from '../GroupHeader';
import { CollabPanel } from './CollabPanel';
import { FuseSlot } from './FuseSlot';
import { GroupChatBubble, ThinkingBubble } from './GroupChatBubble';
import { GroupChatComposer } from './GroupChatComposer';
import { buildGroupMentionConfig } from './mentionHelpers';
import { resolveSender } from './messageHelpers';

export { resolveSender };

export interface GroupChatPaneProps {
  group: GroupView | null;
  session: SessionView | null;
  /** 当前浏览身份（决定是否渲染底部协作面板：bot 视角恒显，human absent 时显示加入条）。 */
  activeIdentity?: IdentityView | null;
  /** 会话成员 mode 更新出口（PATCH participants/{actor}），经由会话 Hook 注入。 */
  updateMemberMode?: (sessionId: string, actorId: string, mode: ParticipantMode) => Promise<boolean>;
  chat: UseChatResult<unknown>;
  supportState: GroupChatState;
  connectionStatus: ProviderConnectionStatus;
  send: (text: string, mentions?: string[], attachments?: SessionMessageAttachment[]) => void;
  /** 按当前群会话直发副屏 <AixUI> 消息（绕开全局桥 last-wins）。由 useGroupChat 提供。 */
  submitPanelMessage: (content: string) => void;
  stop: () => void;
  reconnect: () => Promise<void> | void;
  /** 重新加载会话历史：error 状态下的「重新加载历史」走此出口（直连 provider.loadHistory）。 */
  reloadHistory: () => Promise<void> | void;
  /** 历史消息是否还有更早一页可加载（顶部「加载更多」显隐）。 */
  hasMoreHistory?: boolean;
  /** 是否正在向上翻页加载更早的历史消息（顶部加载指示器）。 */
  isLoadingMoreHistory?: boolean;
  /** 用户滚到顶部时触发加载更早的历史消息。 */
  onLoadMoreHistory?: () => void;
  canManageGroup: PolicyResult;
  activePanel: GroupPanelKind;
  onTogglePanel: (panel: GroupPanelKind) => void;
  onRequestDissolve: () => void;
  onRequestShareGroup: () => Promise<DomainResult<{ invitationUrl: string }>>;
  onRequestShareSession: () => Promise<DomainResult<{ invitationUrl: string }>>;
  /** 副屏命令式 handle（来自 useGroupChat，供 ChatLayout.Panel ref + closePanelForce）。 */
  panelRef: RefObject<PanelHandle>;
  /**
   * 主屏输入框 ref（绑定 <ChatLayout.Sender ref>）。经 useGroupChat → useChatBridge 注册到全局桥,
   * 使 aixcore 卡片 bridge.getInputRef().insert(text) 填入主屏输入框并聚焦（"填输入框"症状修复）。
   * SenderRef 是 BridgeInputRef 超集;Hook 层声明为 SenderRef,这里以 SenderRef 接收匹配 Sender ref 类型。
   */
  inputRef?: RefObject<SenderRef>;
  /** 主→副事件通道桥（经 <ChatLayout.Panel bridge=...> 注入；不传则不接主→副事件）。 */
  chatBridge?: ChatBridge;
}

export function GroupChatPane(props: GroupChatPaneProps) {
  const {
    group,
    submitPanelMessage,
    session,
    activeIdentity,
    updateMemberMode,
    chat,
    supportState,
    connectionStatus,
    send,
    stop,
    reconnect,
    reloadHistory,
    hasMoreHistory,
    isLoadingMoreHistory,
    onLoadMoreHistory,
    canManageGroup,
    activePanel,
    onTogglePanel,
    onRequestDissolve,
    onRequestShareGroup,
    onRequestShareSession,
    panelRef,
    inputRef,
    chatBridge,
  } = props;

  const collabPanel = useCollabPanel(
    session,
    activeIdentity ?? null,
    updateMemberMode ?? (() => Promise.resolve(false)),
  );

  // SDK useChat.onRequest 会先插入一条空内容 assistant 占位消息（id=assistantMessageId），
  // 群聊 Provider 回调的回复 id 由 parser 独立生成，两者不匹配导致占位不会被替换。
  // 这里过滤掉无内容/无 blocks 的 pending 占位，避免渲染出空白气泡。
  const messages = (chat.messages ?? []).filter(
    (m) => !(m.role === 'assistant' && m.status === 'pending' && !m.content && !m.blocks?.length),
  );
  const isRequesting = !!chat.isRequesting;
  // 等待气泡：请求中但本轮尚无正在 streaming 的 assistant 回复时，渲染一个空内容 streaming
  // assistant 气泡作为「思考中」动画（对齐 open-claw thinking bubble footer）。
  const lastMsg = messages[messages.length - 1];
  const showThinkingBubble =
    isRequesting && (!lastMsg || lastMsg.role !== 'assistant' || lastMsg.status !== 'streaming');
  const isLoadingHistory = supportState.phase === 'loading-history' || supportState.phase === 'preparing';
  const mentionConfig: MentionConfig | undefined = useMemo(
    () => (activeIdentity?.kind === 'user' ? buildGroupMentionConfig(session?.participants ?? []) : undefined),
    [activeIdentity?.kind, session?.participants],
  );

  // 任务发起上下文（协作群）：owner_user_id=当前用户，owner_bot_id=群内首个 bot（群主/Master）。
  const taskComposerContext = useMemo<TaskComposerContext | null>(() => {
    if (!group || !session || activeIdentity?.kind !== 'user') return null;
    const ownerBot = group.participants.find((p) => p.kind === 'bot');
    if (!ownerBot || !activeIdentity?.id) return null;
    return {
      sourceType: 'coop_group',
      ownerUserId: resolveUserId(activeIdentity.id),
      ownerBotId: ownerBot.actorId,
      mainSessionId: session.sessionId,
      mainSessionName: session.title,
      sourceGroupId: group.groupId,
      parentTaskId: null,
    };
  }, [group, session, activeIdentity]);

  const taskExecution = useTaskExecution({ panelRef, context: taskComposerContext, submitPanelMessage });
  // 卡片「执行」按钮拦截：task_ready 点执行 → execute + 本地插 panel 消息 → 副屏持久。
  useTaskExecuteFromCard({ panelRef, context: taskComposerContext, submitPanelMessage });

  // 副屏受控 draft：供 GroupChatComposer 受控输入 + 副屏 onAction(fill_input) 回填。
  const [draft, setDraft] = useState('');
  // 命令式副屏 handle 本地副本（对齐单聊 useWorkspace：切换会话清空副屏 tab + draft）。
  useEffect(() => {
    setDraft('');
    panelRef.current?.closePanelForce();
  }, [session?.sessionId, panelRef]);

  // 副屏 onAction 回流：fill_input 回填输入框；send_message 走群聊统一发送链路。
  const handlePanelAction = (action: PanelAction) => {
    if (action.type === 'fill_input') {
      setDraft(action.content);
      return;
    }
    send(action.content);
  };

  if (!group) {
    return (
      <section className="flex min-w-0 flex-1 items-center justify-center bg-white">
        <Empty
          title="欢迎进入协作群对话现场"
          description="在左侧选择一个协作群，开始与多个 Bot 和用户协同。"
          icon={<Sparkles className="h-5 w-5" />}
        />
      </section>
    );
  }

  const showError = supportState.phase === 'error';
  const showReconnectToolbar =
    connectionStatus === 'error' || connectionStatus === 'disconnected' || connectionStatus === 'reconnecting';

  return (
    <section className="flex min-w-0 flex-1 flex-col">
      <ChatLayout className="h-full">
        <GroupHeader
          selectedGroup={group}
          selectedSession={session}
          supportState={supportState}
          connectionStatus={connectionStatus}
          onReconnect={() => {
            void reconnect();
          }}
          canManageGroup={canManageGroup}
          activePanel={activePanel}
          onTogglePanel={onTogglePanel}
          onRequestDissolve={onRequestDissolve}
          onRequestShareGroup={onRequestShareGroup}
          onRequestShareSession={onRequestShareSession}
        />

        {!session ? (
          <div className="flex min-h-0 flex-1 items-center justify-center px-6 py-4">
            <Empty
              title="请选择或创建一个会话"
              description="从左侧选择一个会话，或为当前协作群创建新会话后再发送消息。"
              icon={<Sparkles className="h-5 w-5" />}
            />
          </div>
        ) : (
          <div className="min-h-0 flex-1 px-6 py-4">
            {isLoadingHistory ? (
              <div className="space-y-3 py-6" aria-label="加载协作群会话历史">
                <Skeleton.Block className="h-12 w-3/4 rounded-xl" />
                <Skeleton.Block className="h-12 w-2/3 rounded-xl" />
                <Skeleton.Block className="h-12 w-5/6 rounded-xl" />
              </div>
            ) : showError ? (
              <Empty
                title="加载会话历史失败"
                description={supportState.error ?? '协作群连接出现异常，请重试。'}
                action={
                  <Button
                    size="sm"
                    leftIcon={<RefreshCw className="h-3.5 w-3.5" />}
                    onClick={() => {
                      void reloadHistory();
                    }}
                  >
                    重新加载历史
                  </Button>
                }
              />
            ) : (
              <BubbleList
                messages={messages}
                computeItemKey={(message) => message.id}
                isStreaming={isRequesting}
                hasMore={hasMoreHistory}
                isLoadingMore={isLoadingMoreHistory}
                onLoadMore={onLoadMoreHistory}
                emptyPlaceholder="发送一条消息开始协作群对话"
                footer={showThinkingBubble ? <ThinkingBubble /> : null}
                renderItem={(message, index) => (
                  <GroupChatBubble
                    message={message}
                    isLastMessage={index === messages.length - 1}
                    isRequesting={isRequesting}
                    group={group}
                    participants={session.participants}
                  />
                )}
              />
            )}
          </div>
        )}

        <CollabPanel panel={collabPanel} />

        {/* 输入框仅在 human 视角显示:bot 视角恒由协作面板控制 Bot 发言,不显示输入框;
         human 视角 absent 时由协作面板的「加入」条接管,加入后(present)才显示 Sender。 */}
        {session && activeIdentity?.kind !== 'bot' && !collabPanel.humanAbsentOnly ? (
          <GroupChatComposer
            session={session}
            isRequesting={isRequesting}
            connectionStatus={connectionStatus}
            mentionConfig={mentionConfig}
            showReconnectToolbar={showReconnectToolbar}
            onSend={send}
            onStop={stop}
            onReconnect={() => {
              void reconnect();
            }}
            draft={draft}
            onDraftChange={setDraft}
            inputRef={inputRef}
            execution={taskExecution}
          />
        ) : null}
        <ChatLayout.Panel ref={panelRef} onAction={handlePanelAction} bridge={chatBridge} />
      </ChatLayout>
      {session && <FuseSlot group={group} sessionId={session.sessionId} />}
    </section>
  );
}

export default GroupChatPane;

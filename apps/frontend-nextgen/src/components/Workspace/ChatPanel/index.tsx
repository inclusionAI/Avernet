import { getCapabilities } from '@/capabilities';
import { Empty } from '@/components/ui';
import { ChatMessageSkeleton } from '@/components/Workspace/ChatPanel/ChatMessageSkeleton';
import { ChatPanelHeader } from '@/components/Workspace/ChatPanel/ChatPanelHeader';
import { ChatLayoutPanelSlot } from '@/components/Workspace/ChatLayoutPanelSlot';
import { usePersistentChatLayoutPanelHost } from '@/components/Workspace/PersistentChatLayoutPanelHost';
import { SidePanelCollapseButton } from '@/components/Workspace/SidePanelCollapseButton';
import { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import { MessageEditBar, MessageQuoteBar } from '@/components/Workspace/MessageInteractionToolbar';
import { useMessageAreaSkeleton } from '@/pages/Workspace/hooks/useMessageAreaSkeleton';
import { useMessageEdit } from '@/pages/Workspace/hooks/useMessageEdit';
import {
  buildExplainPrompt,
  buildQuotePrompt,
  useMessageInteractions,
} from '@/pages/Workspace/hooks/useMessageInteractions';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import type { SubmitContext } from '@tc-chat/ui/es/Sender';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import { Sender, ToolbarButton } from '@tc-chat/ui/es/Sender';
import { Headphones, RefreshCw, Sparkles } from 'lucide-react';
import { useCallback, useEffect } from 'react';
import { ChatEmptyState } from './ChatEmptyState';
import { ChatMessageList } from './ChatMessageList';
import type { ChatPanelProps } from './chatPanelContracts';
import { getMessageBlocks, getMessageTime, resolveSingleSender } from './chatPanelPresentation';
export { resolveSingleSender } from './chatPanelPresentation';
export function ChatPanel({
  target,
  viewer,
  authenticatedUserId,
  authenticatedUserName,
  userAvatarUrl,
  messages,
  isRequesting,
  isLoadingMessages,
  connectionStatus,
  retryCount,
  supportState,
  draft,
  panelRef,
  chatBridge,
  onDraftChange,
  onSend,
  onStop,
  onReconnect,
  onPanelAction,
  modelSelector,
  fileChip,
  command,
  composerHeader,
  fileToolbar,
  senderRef,
  mode,
  interactive,
  taskComposer,
  inputRef,
  sessionTitle,
  sessionCode,
  onOpenSessionList,
  historyPagination,
  onManageFiles,
  headerActions,
  highlightMessageId,
  sidePaneOpen,
  onToggleSidePane,
  onSidePaneIsOpenChange,
}: ChatPanelProps) {
  const persistentPanelHost = usePersistentChatLayoutPanelHost();
  const linkedSession = useTaskSidePanelStore((state) => state.linkedSession);
  const closeLinkedSession = useTaskSidePanelStore((state) => state.closeLinkedSession);
  const messageInteractions = useMessageInteractions({
    sessionId: target?.id,
    messages,
    isRequesting,
    onStop,
  });
  // 消息区两态强制（Spec AC-3/AC-7，预览反馈第五轮）：空消息一律骨架屏，确认后的真空会话
  // 才显示空态；demo 模式不接入（保持演示空态）。
  const messageAreaSkeleton = useMessageAreaSkeleton({ messages, status: connectionStatus });
  const { editingMessageId, editMessage, cancelEdit, finishEdit } = useMessageEdit({
    sessionId: target?.id,
    isRequesting,
    onDraftChange,
    inputRef: senderRef,
  });
  const quoteSelectedMessage = (text: string) => {
    if (!target) return;
    const selectedMessage = messages.find((message) => message.id === messageInteractions.selection?.messageId);
    if (!selectedMessage) return;
    const sender = resolveSingleSender(
      selectedMessage,
      target,
      viewer,
      userAvatarUrl,
      authenticatedUserId,
      authenticatedUserName,
    );
    messageInteractions.quoteMessage(selectedMessage.id, sender.name, text);
  };

  const explainSelectedMessage = (text: string) => {
    if (!target) return;
    const selectedMessage = messages.find((message) => message.id === messageInteractions.selection?.messageId);
    if (!selectedMessage) return;
    const sender = resolveSingleSender(
      selectedMessage,
      target,
      viewer,
      userAvatarUrl,
      authenticatedUserId,
      authenticatedUserName,
    );
    onDraftChange(buildExplainPrompt(sender.name, text));
    messageInteractions.clearQuote();
    messageInteractions.setSelection(null);
    finishEdit();
    senderRef?.current?.focus();
  };
  // 把 bridge 的 inputRef.current 同步到 senderRef.current（native Sender ref），保证 aixcore 卡片
  // bridge.getInputRef().insert(text) 能填入主屏输入框（根因 5 修复）。
  useEffect(() => {
    if (inputRef) (inputRef as { current: SenderRef | null }).current = senderRef?.current ?? null;
  });
  // 空态欢迎文案的产品名经 capability 解析（Open=Avernet；internal=TeamClaw），不硬编码。
  const brand = getCapabilities().getProductBrand().value;
  // 空态富化（真空会话）：Bot 头像标记 + 主副文案 + 开场指令的常规空态解剖
  // （不借用消息几何，避免被误读为已有消息）；开场 chips 仅在可交互且命令面板装配时
  // 提供——点击写入输入框并聚焦，Sender 命令面板随之唤起。
  const insertDraft = useCallback(
    (text: string) => {
      senderRef?.current?.insert(text);
      senderRef?.current?.focus();
    },
    [senderRef],
  );
  if (!target) {
    return (
      <section className="flex min-w-0 flex-1 items-center justify-center bg-background">
        <Empty
          title={`欢迎进入 ${brand.name} 对话现场`}
          description="在这里用户可与Bot及时协作沟通。请选择一个Bot，开始当前对话。"
          icon={<Sparkles className="h-5 w-5" />}
        />
      </section>
    );
  }
  const isSupport = target.demoMode === 'teamclaw-support';
  const resolvedMode = mode ?? (isSupport ? 'support' : 'demo');
  const isInteractive = interactive ?? resolvedMode !== 'demo';
  const chatLabel = resolvedMode === 'bot' ? 'Bot 单聊' : '在线客服';
  // 连接状态文案：输入为 useSessionDisplayStatus 合成语义（Spec: docs/specs/workspace-session-connection-display.md），
  // 不再消费业务 phase——「准备中」等实现细节已在合成层收敛为 connecting；错误详情仍取 provider phase 的 error 信息。
  const connectionCopy =
    resolvedMode === 'demo'
      ? { label: '未接入', tone: 'neutral' as const, detail: '演示会话尚未接入在线服务' }
      : connectionStatus === 'connected'
      ? { label: '已连接', tone: 'success' as const, detail: '' }
      : connectionStatus === 'reconnecting'
      ? { label: '重连中', tone: 'warning' as const, detail: `正在重连${retryCount ? `（第 ${retryCount} 次）` : ''}` }
      : connectionStatus === 'connecting'
      ? { label: '连接中', tone: 'warning' as const, detail: `正在建立${chatLabel}连接` }
      : connectionStatus === 'error'
      ? { label: '连接失败', tone: 'error' as const, detail: supportState.error || `${chatLabel}连接失败` }
      : { label: '已断开', tone: 'neutral' as const, detail: `${chatLabel}未建立` };

  const submit = (content: string, context?: SubmitContext) => {
    if (!content.trim() || isRequesting) return;
    const outgoingContent = messageInteractions.quote
      ? `${buildQuotePrompt(messageInteractions.quote.senderName, messageInteractions.quote.text)}\n\n${content}`
      : content;
    messageInteractions.markRead();
    messageInteractions.clearQuote();
    finishEdit();
    onSend(outgoingContent, context);
  };

  return (
    // relative 供副屏右上「收起」悬浮钮绝对定位（SidePanelCollapseButton）。
    <section className="relative flex min-h-0 min-w-0 flex-1 flex-col bg-background">
      <ChatLayout className="min-h-0 flex-1">
        {linkedSession ? (
          <TaskLinkedSession
            target={linkedSession}
            userId={authenticatedUserId ?? undefined}
            onClose={closeLinkedSession}
          />
        ) : (
          <>
        <ChatPanelHeader
          target={target}
          sessionTitle={sessionTitle}
          sessionCode={sessionCode}
          connectionLabel={connectionCopy.label}
          connectionTone={connectionCopy.tone}
          onOpenSessionList={onOpenSessionList}
          onManageFiles={onManageFiles}
          actions={headerActions}
        />

        {/* 消息区两态强制：空消息区一律骨架屏，空态文案仅对确认后的真空会话渲染；demo 保持原空态。
            骨架为真实群聊解剖的回声(系统 pill/长短气泡/图片附件)，吸收历史装载缝隙。 */}
        {resolvedMode !== 'demo' && messageAreaSkeleton ? (
          <ChatMessageSkeleton />
        ) : (
          <ChatMessageList
            messages={messages}
            isRequesting={isRequesting}
            isLoadingMessages={isLoadingMessages}
            interactions={messageInteractions}
            onStop={onStop}
            onEditMessage={editMessage}
            onQuoteSelected={quoteSelectedMessage}
            onExplainSelected={explainSelectedMessage}
            resolveSender={(message) =>
              resolveSingleSender(message, target, viewer, userAvatarUrl, authenticatedUserId, authenticatedUserName)
            }
            getMessageTime={getMessageTime}
            getMessageBlocks={getMessageBlocks}
            hasMoreHistory={historyPagination?.hasMore}
            isLoadingMoreHistory={historyPagination?.isLoading}
            onLoadMoreHistory={historyPagination?.onLoadMore}
            highlightMessageId={highlightMessageId}
            emptyPlaceholder={
              <ChatEmptyState
                target={target}
                command={command}
                onInsertDraft={isInteractive && target.status === 'available' ? insertDraft : undefined}
              />
            }
          />
        )}

        {/* 单聊输入框用原生 <Sender>(forwardRef,暴露 SenderRef)替代 <ChatLayout.Sender>(普通函数组件,非 forwardRef,
            ref 恒 null)。ref={senderRef} 经 useChatBridge.setInputRef 注册到全局桥,使 aixcore 卡片
            bridge.getInputRef().insert(text) 填入主屏输入框(根因 5 修复,对齐 open-claw ChatInputArea)。 */}
        <div className="flex shrink-0 flex-col gap-2 bg-background px-3 py-1.5 sm:px-6 sm:py-2">
          {editingMessageId ? <MessageEditBar onCancel={cancelEdit} /> : null}
          <MessageQuoteBar quote={messageInteractions.quote} onClear={messageInteractions.clearQuote} />
          <Sender
            ref={senderRef as React.Ref<SenderRef>}
            className="sender-shell w-full"
            value={draft}
            onChange={onDraftChange}
            onSubmit={submit}
            onCancel={onStop}
            loading={isRequesting}
            disabled={target.status !== 'available' || !isInteractive || isRequesting}
            submitType="enter"
            placeholder="可使用 /file 引用文件、/link 引用链接、/skill 调用能力"
            fileChip={fileChip}
            command={command}
            slots={{ header: composerHeader }}
            toolbar={{
              left:
                taskComposer || modelSelector || connectionCopy.detail || fileToolbar ? (
                  <div className="flex items-center gap-2">
                    {taskComposer}
                    {fileToolbar}
                    {modelSelector}
                    {connectionCopy.detail ? (
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Headphones className="h-3.5 w-3.5" />
                        {connectionCopy.detail}
                      </span>
                    ) : null}
                  </div>
                ) : undefined,
              right:
                isInteractive && (connectionStatus === 'error' || connectionStatus === 'disconnected') ? (
                  <ToolbarButton label="重新连接" icon={<RefreshCw className="h-3.5 w-3.5" />} onClick={onReconnect} />
                ) : undefined,
            }}
          />
        </div>
          </>
        )}

        <ChatLayoutPanelSlot
          ref={panelRef}
          onAction={onPanelAction}
          bridge={chatBridge}
          dismissOnOutside={false}
          onOpenStateChange={onSidePaneIsOpenChange}
        />
      </ChatLayout>
      {/* 副屏右上「收起」钮：真实开合态开时悬于副屏 TopBar 右侧，点按关闭。 */}
      {!persistentPanelHost && sidePaneOpen && onToggleSidePane ? (
        <SidePanelCollapseButton onClick={onToggleSidePane} />
      ) : null}
    </section>
  );
}

import { getCapabilities } from '@/capabilities';
import { Empty, Skeleton } from '@/components/ui';
import { ChatPanelHeader } from '@/components/Workspace/ChatPanel/ChatPanelHeader';
import { MessageEditBar, MessageQuoteBar } from '@/components/Workspace/MessageInteractionToolbar';
import type { IdentityView } from '@/domain/collaboration';
import { useMessageAreaSkeleton } from '@/pages/Workspace/hooks/useMessageAreaSkeleton';
import { useMessageEdit } from '@/pages/Workspace/hooks/useMessageEdit';
import {
  buildExplainPrompt,
  buildQuotePrompt,
  useMessageInteractions,
} from '@/pages/Workspace/hooks/useMessageInteractions';
import type { ConversationTarget, SupportChatState } from '@/services/workspace/workspaceModel';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { ChatBridge, ChatMessage, PanelAction, PanelHandle } from '@tc-chat/core';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import type { CommandConfig, FileChipConfig, SenderRef, SubmitContext } from '@tc-chat/ui/es/Sender';
import { Sender, ToolbarButton } from '@tc-chat/ui/es/Sender';
import { Headphones, RefreshCw, Sparkles } from 'lucide-react';
import { useEffect, type ReactNode, type RefObject } from 'react';
import { ChatMessageList } from './ChatMessageList';
import { getMessageBlocks, getMessageTime, resolveSingleSender } from './chatPanelPresentation';
export { resolveSingleSender } from './chatPanelPresentation';
interface Props {
  target: ConversationTarget | null;
  /** 当前查看身份，用于在消息区展示真实发送者名称，避免使用有歧义的「你」。 */
  viewer?: IdentityView | null;
  /** 当前认证 human，用于按 message senderId 决定用户消息名称。 */
  authenticatedUserId?: string | null;
  authenticatedUserName?: string | null;
  /** 顶栏当前登录用户头像；用户消息优先复用此头像，与 Bot 消息保持区分。 */
  userAvatarUrl?: string;
  messages: ChatMessage[];
  isRequesting: boolean;
  isLoadingMessages: boolean;
  connectionStatus: ProviderConnectionStatus;
  retryCount: number;
  supportState: SupportChatState;
  draft: string;
  panelRef: RefObject<PanelHandle>;
  /** 主→副事件通道桥（经 <ChatLayout.Panel bridge=...> 注入；不传则不接主→副事件）。 */
  chatBridge?: ChatBridge;
  onDraftChange: (content: string) => void;
  onSend: (content: string, context?: SubmitContext) => void;
  onStop: () => void;
  onReconnect: () => void;
  onPanelAction: (action: PanelAction) => void;
  modelSelector?: ReactNode;
  fileChip?: FileChipConfig;
  command?: CommandConfig;
  fileToolbar?: ReactNode;
  senderRef?: React.RefObject<SenderRef | null>;
  mode?: 'demo' | 'support' | 'bot';
  interactive?: boolean;
  /** 任务发起入口（单 Bot / 协作群共用）。仅 bot 模式渲染。 */
  taskComposer?: ReactNode;
  /**
   * 主屏输入框 ref（绑定原生 <Sender ref>）。透传自 useWorkspace.inputRef，经 useChatBridge.setInputRef
   * 注册到全局桥，使 aixcore 卡片 bridge.getInputRef().insert(text) 填入单聊主屏输入框并聚焦（根因 5 修复）。
   * SenderRef 是 BridgeInputRef 超集；原生 Sender 是 forwardRef（ChatLayout.Sender 非 forwardRef,ref 恒 null）。
   */
  inputRef?: RefObject<SenderRef>;
  /** 当前会话标题（单聊选中会话后顶栏显示会话名，与协作群顶栏一致；缺省回退 target.name）。 */
  sessionTitle?: string;
  /** <lg 打开单聊会话列表。 */
  onOpenSessionList?: () => void;
  historyPagination?: { hasMore: boolean; isLoading: boolean; onLoadMore: () => void };
  /** 打开会话文件面板（验收微调：文件管理入口迁至顶栏，与协作群位置规则一致；缺省不渲染入口）。 */
  onManageFiles?: () => void;
}
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
  fileToolbar,
  senderRef,
  mode,
  interactive,
  taskComposer,
  inputRef,
  sessionTitle,
  onOpenSessionList,
  historyPagination,
  onManageFiles,
}: Props) {
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
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
      <ChatLayout className="min-h-0 flex-1">
        <ChatPanelHeader
          target={target}
          sessionTitle={sessionTitle}
          connectionLabel={connectionCopy.label}
          connectionTone={connectionCopy.tone}
          onOpenSessionList={onOpenSessionList}
          onManageFiles={onManageFiles}
        />

        {/* 消息区两态强制：空消息区一律骨架屏，空态文案仅对确认后的真空会话渲染；demo 保持原空态。 */}
        {resolvedMode !== 'demo' && messageAreaSkeleton ? (
          <div className="flex min-h-0 flex-1 flex-col space-y-3 px-3 py-6 sm:px-6" aria-label="加载会话消息">
            <Skeleton.Block className="h-12 w-3/4 rounded-xl" />
            <Skeleton.Block className="h-12 w-2/3 rounded-xl" />
            <Skeleton.Block className="h-12 w-5/6 rounded-xl" />
          </div>
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
            className="w-full"
            value={draft}
            onChange={onDraftChange}
            onSubmit={submit}
            onCancel={onStop}
            loading={isRequesting}
            disabled={target.status !== 'available' || !isInteractive || isRequesting}
            submitType="enter"
            placeholder={`给 ${target.name} 发送消息…`}
            fileChip={fileChip}
            command={command}
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

        <ChatLayout.Panel ref={panelRef} onAction={onPanelAction} bridge={chatBridge} />
      </ChatLayout>
    </section>
  );
}

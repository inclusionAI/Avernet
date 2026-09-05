import { Headphones, RefreshCw, Sparkles } from 'lucide-react';

import { getCapabilities } from '@/capabilities';
import { Avatar, Badge, Empty } from '@/components/ui';
import { MessageEditBar, MessageQuoteBar } from '@/components/Workspace/MessageInteractionToolbar';
import type { IdentityView } from '@/domain/collaboration';
import { useMessageEdit } from '@/pages/Workspace/hooks/useMessageEdit';
import {
  buildExplainPrompt,
  buildQuotePrompt,
  useMessageInteractions,
} from '@/pages/Workspace/hooks/useMessageInteractions';
import { buildMessageBlocks } from '@/services/workspace/messageBlockBuilder';
import type { ConversationTarget, SupportChatState } from '@/services/workspace/workspaceModel';
import { formatChatTime } from '@/utils/format';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { ChatBridge, ChatMessage, PanelAction, PanelHandle } from '@tc-chat/core';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import type { CommandConfig, FileChipConfig, SenderRef, SubmitContext } from '@tc-chat/ui/es/Sender';
import { Sender, ToolbarButton } from '@tc-chat/ui/es/Sender';
import { useEffect, type ReactNode, type RefObject } from 'react';
import { ChatMessageList } from './ChatMessageList';

interface Props {
  target: ConversationTarget | null;
  /** 当前查看身份，用于在消息区展示真实发送者名称，避免使用有歧义的「你」。 */
  viewer?: IdentityView | null;
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
}

function getMessageTime(message: ChatMessage) {
  const displayTime = message.extra?.displayTime;
  if (displayTime) return formatChatTime(displayTime);
  return formatChatTime(message.createdAt);
}

function getMessageBlocks(message: ChatMessage) {
  return buildMessageBlocks(message);
}

function renderUserAvatar(name: string, avatarUrl?: string) {
  return <Avatar name={name} src={avatarUrl} size={32} />;
}

function renderAvatar(name: string, avatarUrl?: string, fallbackAvatar?: string) {
  if (avatarUrl) {
    return <img src={avatarUrl} alt={name} className="h-8 w-8 shrink-0 rounded-full object-cover" />;
  }
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-foreground text-xs font-semibold text-background">
      {fallbackAvatar || name.charAt(0)}
    </span>
  );
}

export function resolveSingleSender(
  message: ChatMessage,
  target: ConversationTarget,
  viewer?: IdentityView | null,
  userAvatarUrl?: string,
): { name: string; avatar: ReactNode } {
  if (message.role === 'assistant') {
    return { name: target.name || '未命名 Bot', avatar: renderAvatar(target.name || 'Bot', undefined, target.avatar) };
  }
  const name = viewer?.displayName || '未命名成员';
  return { name, avatar: renderUserAvatar(name, userAvatarUrl ?? viewer?.avatarUrl) };
}

export function ChatPanel({
  target,
  viewer,
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
}: Props) {
  const messageInteractions = useMessageInteractions({
    sessionId: target?.id,
    messages,
    isRequesting,
    onStop,
  });
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
    const sender = resolveSingleSender(selectedMessage, target, viewer, userAvatarUrl);
    messageInteractions.quoteMessage(selectedMessage.id, sender.name, text);
  };

  const explainSelectedMessage = (text: string) => {
    if (!target) return;
    const selectedMessage = messages.find((message) => message.id === messageInteractions.selection?.messageId);
    if (!selectedMessage) return;
    const sender = resolveSingleSender(selectedMessage, target, viewer, userAvatarUrl);
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
          description={`${
            viewer?.displayName || '未命名成员'
          }可在这里与用户、Bot进行即时协作沟通。请选择一个对话，开始当前交流。`}
          icon={<Sparkles className="h-5 w-5" />}
        />
      </section>
    );
  }

  const isSupport = target.demoMode === 'teamclaw-support';
  const resolvedMode = mode ?? (isSupport ? 'support' : 'demo');
  const isInteractive = interactive ?? resolvedMode !== 'demo';
  const chatLabel = resolvedMode === 'bot' ? 'Bot 单聊' : '在线客服';
  const connectionCopy =
    resolvedMode === 'demo'
      ? { label: '未接入', tone: 'neutral' as const, detail: '演示会话尚未接入在线服务' }
      : supportState.phase === 'preparing'
      ? { label: '准备中', tone: 'warning' as const, detail: `${chatLabel}环境正在准备` }
      : connectionStatus === 'connected'
      ? { label: '在线', tone: 'success' as const, detail: '' }
      : connectionStatus === 'reconnecting'
      ? { label: '重连中', tone: 'warning' as const, detail: `正在重连${retryCount ? `（第 ${retryCount} 次）` : ''}` }
      : connectionStatus === 'connecting'
      ? { label: '连接中', tone: 'warning' as const, detail: `正在建立${chatLabel}连接` }
      : connectionStatus === 'error' || supportState.phase === 'error'
      ? { label: '连接失败', tone: 'error' as const, detail: supportState.error || `${chatLabel}连接失败` }
      : { label: '离线', tone: 'neutral' as const, detail: `${chatLabel}未建立` };

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
        <ChatLayout.Header
          className="flex h-16 border-b border-border bg-card px-3 sm:px-5"
          slotLeft={
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-sm font-semibold text-primary-foreground">
                {target.avatar}
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h2 className="m-0 truncate text-sm font-semibold">{target.name}</h2>
                  <Badge tone={connectionCopy.tone}>{connectionCopy.label}</Badge>
                </div>
                <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">{target.summary}</p>
              </div>
            </div>
          }
        />

        <ChatMessageList
          messages={messages}
          isRequesting={isRequesting}
          isLoadingMessages={isLoadingMessages}
          interactions={messageInteractions}
          onStop={onStop}
          onEditMessage={editMessage}
          onQuoteSelected={quoteSelectedMessage}
          onExplainSelected={explainSelectedMessage}
          resolveSender={(message) => resolveSingleSender(message, target, viewer, userAvatarUrl)}
          getMessageTime={getMessageTime}
          getMessageBlocks={getMessageBlocks}
        />

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

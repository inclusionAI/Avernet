import { Headphones, RefreshCw, Sparkles } from 'lucide-react';

import { Badge, Empty, Spin } from '@/components/ui';
import type { ConversationTarget, SupportChatState } from '@/services/workspace/workspaceModel';
import { formatChatTime } from '@/utils/format';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { Block, ChatBridge, ChatMessage, PanelAction, PanelHandle, TextBlock } from '@tc-chat/core';
import { Bubble } from '@tc-chat/ui/es/Bubble';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { aixUiPlugin, fileRefPlugin } from '@tc-chat/ui/es/MarkdownRender';
import type { CommandConfig, FileChipConfig, SenderRef, SubmitContext } from '@tc-chat/ui/es/Sender';
import { Sender, ToolbarButton } from '@tc-chat/ui/es/Sender';
import { SystemNotice } from '@tc-chat/ui/es/SystemNotice';
import { useEffect, type ReactNode, type RefObject } from 'react';

interface Props {
  target: ConversationTarget | null;
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

function getMessageBlocks(message: ChatMessage): Block[] {
  if (message.blocks?.length) return message.blocks;
  return message.content ? ([{ type: 'text', content: message.content }] as TextBlock[]) : [];
}

export function ChatPanel({
  target,
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
  // 把 bridge 的 inputRef.current 同步到 senderRef.current（native Sender ref），保证 aixcore 卡片
  // bridge.getInputRef().insert(text) 能填入主屏输入框（根因 5 修复）。
  useEffect(() => {
    if (inputRef) (inputRef as { current: SenderRef | null }).current = senderRef?.current ?? null;
  });

  if (!target) {
    return (
      <section className="flex min-w-0 flex-1 items-center justify-center bg-white">
        <Empty
          title="欢迎进入 TeamClaw 对话现场"
          description="这里承接你与用户、Bot 的即时协作沟通。请选择一个对话，开始当前交流。"
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
    onSend(content, context);
  };

  return (
    <section className="min-w-0 flex-1 bg-white">
      <ChatLayout className="h-full">
        <ChatLayout.Header
          slotLeft={
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[var(--color-primary)] text-sm font-semibold text-white">
                {target.avatar}
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h2 className="m-0 truncate text-sm font-semibold">{target.name}</h2>
                  <Badge tone={connectionCopy.tone}>{connectionCopy.label}</Badge>
                </div>
                <p className="m-0 mt-0.5 truncate text-xs text-[var(--color-muted)]">{target.summary}</p>
              </div>
            </div>
          }
        />

        {isLoadingMessages ? (
          <div className="flex min-h-0 flex-1 items-center justify-center" aria-label="加载会话消息">
            <Spin />
          </div>
        ) : (
          <ChatLayout.List
            className="px-6 py-4"
            messages={messages}
            computeItemKey={(message) => message.id}
            isStreaming={isRequesting}
            emptyPlaceholder="发送一条消息开始对话"
            renderItem={(message, index) => {
              if (message.role === 'system') {
                return <SystemNotice>{message.content}</SystemNotice>;
              }
              const isLastMessage = index === messages.length - 1;
              return (
                <Bubble
                  className={message.role === 'user' ? 'mb-3 pr-4' : 'mb-3'}
                  sender={{
                    role: message.role,
                    name: message.role === 'assistant' ? target.name : undefined,
                    // bot 回复补左侧气泡底色;user 右侧气泡沿用 SDK 默认色。
                    bubbleColor: message.role === 'assistant' ? 'var(--color-chat-bubble-bot)' : undefined,
                    avatar:
                      message.role === 'assistant' ? (
                        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--color-primary)] text-xs font-semibold text-white">
                          {target.avatar}
                        </span>
                      ) : undefined,
                  }}
                  timestamp={getMessageTime(message)}
                  blocks={getMessageBlocks(message)}
                  preset="openclaw"
                  markdown={{ preset: 'full', extensions: [aixUiPlugin, fileRefPlugin] }}
                  tool={{ defaultCollapsed: !(isLastMessage && isRequesting) }}
                  isStreaming={isLastMessage && isRequesting && message.role === 'assistant'}
                />
              );
            }}
          />
        )}

        {/* 单聊输入框用原生 <Sender>(forwardRef,暴露 SenderRef)替代 <ChatLayout.Sender>(普通函数组件,非 forwardRef,
            ref 恒 null)。ref={senderRef} 经 useChatBridge.setInputRef 注册到全局桥,使 aixcore 卡片
            bridge.getInputRef().insert(text) 填入主屏输入框(根因 5 修复,对齐 open-claw ChatInputArea)。 */}
        <div className="shrink-0 p-4">
          <Sender
            ref={senderRef as React.Ref<SenderRef>}
            className="mx-2"
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
                      <span className="flex items-center gap-1.5 text-xs text-[var(--color-muted)]">
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

import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import type { Block, ChatMessage, TextBlock } from '@tc-chat/core';
import { Bubble } from '@tc-chat/ui/es/Bubble';
import { aixUiPlugin, fileRefPlugin } from '@tc-chat/ui/es/MarkdownRender';

interface DebugMessageLike {
  role?: string;
  content?: string;
  blocks?: Block[];
}

/**
 * 调试对话与「对话协作」共用 OpenClaw 的结构化消息块：thinking、工具调用和
 * 正文增量都在 blocks 中，不能只读取最终 text content，否则推理期间会空白。
 */
export function getDebugMessageBlocks(message: DebugMessageLike): Block[] {
  if (message.blocks?.length) return message.blocks;
  return message.content ? ([{ type: 'text', content: message.content } as TextBlock] as Block[]) : [];
}

interface DebugAssistantMessageProps {
  message: ChatMessage;
  botName: string;
  avatarUrl: string;
  isStreaming: boolean;
}

/** 复用对话协作的 Bubble 渲染，让 thinking、工具调用与正文增量连续出现。 */
export function DebugAssistantMessage({ message, botName, avatarUrl, isStreaming }: DebugAssistantMessageProps) {
  return (
    <div
      className="group"
      role={isStreaming ? 'status' : undefined}
      aria-live={isStreaming ? 'polite' : undefined}
      aria-label={isStreaming ? `${botName} 正在回复` : undefined}
    >
      <Bubble
        className="message-bubble-compact [--aix-markdown-font-size:13px] [--aix-font-size-base:13px]"
        sender={{
          role: 'assistant',
          align: 'left',
          name: botName,
          avatar: <BotAvatar name={botName} avatarUrl={avatarUrl} className="size-8 rounded-full" />,
          maxWidth: '100%',
        }}
        blocks={getDebugMessageBlocks(message)}
        preset="openclaw"
        markdown={{ preset: 'full', extensions: [aixUiPlugin, fileRefPlugin] }}
        thinking={{ defaultExpanded: true }}
        tool={{ defaultCollapsed: false }}
        isStreaming={isStreaming}
      />
    </div>
  );
}

/** 尚未创建 assistant 占位消息时的兜底等待态，视觉与正常流式气泡完全一致。 */
export function DebugThinkingReply({ botName, avatarUrl }: Omit<DebugAssistantMessageProps, 'message' | 'isStreaming'>) {
  return (
    <div role="status" aria-live="polite" aria-label={`${botName} 正在思考`}>
      <Bubble
        className="message-bubble-compact [--aix-markdown-font-size:13px] [--aix-font-size-base:13px]"
        sender={{
          role: 'assistant',
          align: 'left',
          name: botName,
          avatar: <BotAvatar name={botName} avatarUrl={avatarUrl} className="size-8 rounded-full" />,
          maxWidth: '100%',
        }}
        blocks={[]}
        preset="openclaw"
        markdown={{ preset: 'full', extensions: [aixUiPlugin, fileRefPlugin] }}
        isStreaming
      />
    </div>
  );
}

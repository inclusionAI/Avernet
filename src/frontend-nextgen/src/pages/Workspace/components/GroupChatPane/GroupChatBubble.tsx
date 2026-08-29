import type { GroupView, ParticipantView } from '@/domain/collaboration';
import type { ChatMessage } from '@tc-chat/core';
import { Bubble } from '@tc-chat/ui/es/Bubble';
import { aixUiPlugin } from '@tc-chat/ui/es/MarkdownRender';
import { getMessageBlocks, getMessageTime, resolveSender } from './messageHelpers';
import { SystemMessageItem } from './SystemMessageItem';

export interface GroupChatBubbleProps {
  message: ChatMessage;
  isLastMessage: boolean;
  isRequesting: boolean;
  group: GroupView;
  participants: ParticipantView[];
}

export function GroupChatBubble({ message, isLastMessage, isRequesting, group, participants }: GroupChatBubbleProps) {
  if (message.role === 'system') {
    return <SystemMessageItem message={message} />;
  }
  const sender = resolveSender(message, group, participants);
  return (
    <Bubble
      className={message.role === 'user' ? 'mb-3 pr-4' : 'mb-3'}
      sender={{
        role: message.role,
        name: sender?.name,
        // bot 回复补左侧气泡底色(回复途中暂无名称时也保留底色,仅不显示名称);
        // user 右侧气泡沿用 SDK 默认色。
        bubbleColor: message.role === 'assistant' ? 'var(--color-chat-bubble-bot)' : undefined,
        avatar: sender?.avatar,
      }}
      timestamp={getMessageTime(message)}
      blocks={getMessageBlocks(message)}
      preset="openclaw"
      markdown={{ preset: 'full', extensions: [aixUiPlugin] }}
      tool={{ defaultCollapsed: !(isLastMessage && isRequesting) }}
      isStreaming={isLastMessage && isRequesting && message.role === 'assistant'}
    />
  );
}

/**
 * 等待 Bot 回复的「思考中」省略号气泡。
 *
 * 群聊中哪个 Bot 会回复要等 ws 消息到达才能确定,等待期无法预知回复者,
 * 因此只保留 bot 侧气泡底色与动态省略号,不展示具体名称/头像——
 * 否则会先显示某个猜测 Bot(如 Bot A)的名称,真实回复(如 Bot B)到达后
 * 名称突变成正确 Bot,产生闪烁(Bot A → Bot B)。真实回复气泡由
 * GroupChatBubble 经 resolveSender 解析出正确 Bot 名称后接管。
 */
export function ThinkingBubble() {
  return (
    <Bubble
      className="mb-3"
      sender={{
        role: 'assistant',
        bubbleColor: 'var(--color-chat-bubble-bot)',
      }}
      blocks={[{ type: 'text', content: '' }] as never}
      preset="openclaw"
      markdown="full"
      isStreaming
    />
  );
}

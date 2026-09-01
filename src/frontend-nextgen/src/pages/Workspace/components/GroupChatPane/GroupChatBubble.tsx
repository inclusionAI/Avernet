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
  /** 顶栏当前登录用户头像；用户消息优先复用此头像。 */
  userAvatarUrl?: string;
  /** 当前登录用户身份 ID，用于区分其他 human 成员。 */
  userIdentityId?: string | null;
}

export function GroupChatBubble({
  message,
  isLastMessage,
  isRequesting,
  group,
  participants,
  userAvatarUrl,
  userIdentityId,
}: GroupChatBubbleProps) {
  if (message.role === 'system') {
    return <SystemMessageItem message={message} />;
  }
  const sender = resolveSender(message, group, participants, userAvatarUrl, userIdentityId);
  return (
    <Bubble
      className="mb-3 [--aix-markdown-font-size:12px] [--aix-font-size-base:12px]"
      sender={{
        role: message.role,
        align: 'left',
        name: sender?.name,
        avatar: sender?.avatar,
        maxWidth: '48rem',
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
 * 群聊中哪个 Bot 会回复要等 ws 消息到达才能确定，等待期无法预知回复者，
 * 因此只保留动态省略号与占位头像，不展示具体名称——
 * 否则会先显示某个猜测 Bot(如 Bot A)的名称,真实回复(如 Bot B)到达后
 * 名称突变成正确 Bot，产生闪烁(Bot A → Bot B)。真实回复消息由
 * GroupChatBubble 经 resolveSender 解析出正确 Bot 名称后接管。
 */
export function ThinkingBubble() {
  return (
    <Bubble
      className="mb-3 [--aix-markdown-font-size:12px] [--aix-font-size-base:12px]"
      sender={{
        role: 'assistant',
        align: 'left',
        maxWidth: '48rem',
        avatar: <span className="h-8 w-8 shrink-0" aria-hidden="true" />,
      }}
      blocks={[{ type: 'text', content: '' }] as never}
      preset="openclaw"
      markdown="full"
      isStreaming
    />
  );
}

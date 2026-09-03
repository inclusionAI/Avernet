import { MessageCopyAction, MessageInteractionToolbar } from '@/components/Workspace/MessageInteractionToolbar';
import { MessageSenderLayout, MessageSenderMeta } from '@/components/Workspace/MessageSenderMeta';
import type { GroupView, ParticipantView } from '@/domain/collaboration';
import { getMessageText } from '@/pages/Workspace/hooks/useMessageInteractions';
import type { ChatMessage } from '@tc-chat/core';
import { Bubble } from '@tc-chat/ui/es/Bubble';
import { aixUiPlugin, fileRefPlugin } from '@tc-chat/ui/es/MarkdownRender';
import { getMessageBlocks, getMessageTime, resolveSender } from './messageHelpers';
import { SystemMessageItem } from './SystemMessageItem';

export interface GroupChatBubbleProps {
  message: ChatMessage;
  isLastMessage: boolean;
  isRequesting: boolean;
  group: GroupView;
  participants: ParticipantView[];
  sessionId?: string;
  /** 顶栏当前登录用户头像；用户消息优先复用此头像。 */
  userAvatarUrl?: string;
  /** 当前登录用户身份 ID，用于区分其他 human 成员。 */
  userIdentityId?: string | null;
  onCopy?: (text: string) => void | boolean | Promise<void | boolean>;
  onEdit?: () => void;
  isEditable?: boolean;
  onStop?: () => void;
}

export function GroupChatBubble({
  message,
  isLastMessage,
  isRequesting,
  group,
  participants,
  sessionId,
  userAvatarUrl,
  userIdentityId,
  onCopy,
  onEdit,
  isEditable,
  onStop,
}: GroupChatBubbleProps) {
  if (message.role === 'system') {
    return <SystemMessageItem message={message} />;
  }
  const sender = resolveSender(message, group, participants, userAvatarUrl, userIdentityId);
  const messageText = getMessageText(message);
  const messageActionsProps = {
    onCopy: () => onCopy?.(messageText),
    onEdit,
    isEditable: message.role === 'user' && isEditable && Boolean(messageText.trim()),
    isStreaming: isLastMessage && message.role === 'assistant' && (isRequesting || message.status === 'streaming'),
    onStop,
  };
  const messageActions = <MessageInteractionToolbar showCopy={false} {...messageActionsProps} />;

  return (
    <div className="group relative">
      <MessageSenderLayout
        avatar={sender?.avatar}
        align={message.role === 'user' ? 'right' : 'left'}
        meta={
          <MessageSenderMeta
            name={sender?.name ?? (message.role === 'user' ? '未命名成员' : '未命名 Bot')}
            time={getMessageTime(message)}
            align={message.role === 'user' ? 'right' : 'left'}
          />
        }
      >
        <Bubble
          className="message-bubble-compact [--aix-markdown-font-size:14px] [--aix-font-size-base:14px]"
          sender={{
            role: message.role,
            align: message.role === 'user' ? 'right' : 'left',
            name: undefined,
            bubbleColor: message.role === 'user' ? 'hsl(var(--primary) / 0.1)' : undefined,
            maxWidth: '48rem',
          }}
          timestamp={undefined}
          blocks={getMessageBlocks(message, sessionId)}
          preset="openclaw"
          markdown={{ preset: 'full', extensions: [aixUiPlugin, fileRefPlugin] }}
          tool={{ defaultCollapsed: !(isLastMessage && isRequesting) }}
          isStreaming={
            isLastMessage && message.role === 'assistant' && (isRequesting || message.status === 'streaming')
          }
          actions={message.role === 'assistant' ? messageActions : undefined}
        />
      </MessageSenderLayout>
      {onCopy || (message.role === 'user' && isEditable && onEdit) ? (
        <MessageCopyAction
          testId={`message-copy-action-${message.id}`}
          align={message.role === 'user' ? 'right' : 'left'}
          onCopy={() => onCopy?.(messageText)}
          onEdit={onEdit}
          isEditable={message.role === 'user' && isEditable && Boolean(messageText.trim())}
        />
      ) : null}
    </div>
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
export function ThinkingBubble({ label }: { label?: string }) {
  return (
    <Bubble
      className="message-bubble-compact [--aix-markdown-font-size:14px] [--aix-font-size-base:14px]"
      sender={{
        role: 'assistant',
        align: 'left',
        maxWidth: '48rem',
        avatar: <span className="h-8 w-8 shrink-0" aria-hidden="true" />,
      }}
      blocks={[{ type: 'text', content: label ?? '' }] as never}
      preset="openclaw"
      markdown="full"
      isStreaming
    />
  );
}

/**
 * MessageAvatar — 消息气泡头像（BotAvatar xs 尺寸的薄包装）
 *
 * 类型映射：
 *   user             → BotAvatar type="user"
 *   assistant        → BotAvatar type="assistant"
 *   assistant-expert → BotAvatar type="expert"
 *   bot-letter       → BotAvatar type="assistant"（群聊多 Bot，必须传 name）
 */

import BotAvatar from '@/components/BotAvatar';
import React from 'react';

export type MessageAvatarType =
  | 'user'
  | 'assistant'
  | 'assistant-expert'
  | 'bot-letter';

export interface MessageAvatarProps {
  type: MessageAvatarType;
  avatarUrl?: string | null;
  name?: string;
  botId?: string;
  className?: string;
}

const TYPE_MAP = {
  user: 'user',
  assistant: 'assistant',
  'assistant-expert': 'expert',
  'bot-letter': 'assistant',
} as const;

const MessageAvatar: React.FC<MessageAvatarProps> = ({
  type,
  avatarUrl,
  name,
  botId,
  className,
}) => (
  <BotAvatar
    type={TYPE_MAP[type]}
    size="xs"
    avatarUrl={avatarUrl}
    name={type === 'user' ? undefined : name}
    botId={type === 'user' ? undefined : botId}
    className={className}
  />
);

export default MessageAvatar;

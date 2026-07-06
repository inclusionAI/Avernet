import type { MessageAvatarType } from '@/components/MessageAvatar';
import type { ChatMessage } from '@aix-chat/core';
import React from 'react';

/** 消息发送者配置 */
export interface SenderConfig {
  role: 'user' | 'assistant' | 'other-user';
  avatar: React.ReactNode;
  name?: string;
  bubbleColor?: string;
  maxWidth?: string | number;
}

/** 消息列表公共 Props（aicoding 和 openclaw 共享） */
export interface MessageListBaseProps {
  /** 消息列表 */
  messages: ChatMessage[];
  /** AI 正在响应（展示 typing / thinking 气泡） */
  isTyping?: boolean;
  /** 列表顶部是否还有更多历史可加载 */
  hasMore?: boolean;
  /** 是否正在加载更多 */
  isLoadingMore?: boolean;
  /** 是否正在初次加载历史消息（chat.history），用于触发"消息加载中..."占位 */
  isLoadingHistory?: boolean;
  /** 滚到顶部触发加载更多 */
  onLoadMore?: () => void;
  /** 审批批准回调 */
  onApprove?: (approvalId: string) => void;
  /** 审批拒绝回调 */
  onReject?: (approvalId: string) => void;
  /** 划词追问回调 */
  onFollowUp?: (text: string) => void;
  /** 划词解释回调 */
  onExplain?: (text: string) => void;
  /** 划词工具栏 ID */
  selectionToolbarId?: string;
  /** 过滤消息的 sessionKey */
  sessionKey?: string;
  /** 空状态提示 */
  emptyPlaceholder?: string | React.ReactNode;
  /** 历史加载中文案 */
  historyLoadingPlaceholder?: string;
  /** assistant 消息头像类型（默认 'assistant'） */
  assistantAvatarType?: Extract<
    MessageAvatarType,
    'assistant' | 'assistant-expert'
  >;
  /** Bot 头像 URL（或 "emoji:🤖" 格式），优先显示 */
  botAvatarUrl?: string | null;
  /** Bot 名称，用于生成首字母头像 */
  botName?: string;
  /** Bot ID，用于哈希决定头像颜色 */
  botId?: string;
  /** 反转头像分配（他人会话） */
  reverseAvatar?: boolean;
  /** 创建者用户 ID（reverseAvatar=true 时用于显示 assistant 名称） */
  creatorUserId?: string | null;
  /** 自定义头像渲染（优先级高于默认头像） */
  renderAvatar?: (msg: ChatMessage) => React.ReactNode;
  /** 自定义 Sender 配置（优先级最高） */
  getSenderConfig?: (msg: ChatMessage) => Partial<SenderConfig>;
  /** 容器类名 */
  className?: string;
}

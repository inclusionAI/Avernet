/**
 * 会话消息检索 Service —— 历史消息面板双源封装（chat-header-panels，design D5）。
 *
 * 双源：后端检索接口（clawweb/网关，未确认存在）与本地已载入消息过滤。
 * 当前后端检索接口未确认，固定走本地源并显式返回 source='local'，
 * 面板据此明示检索范围（仅已载入消息）；接口就绪后仅在 resolveSearchSource()
 * 换源，面板组件与调用方签名不变（spec「面板组件不变」约束）。
 */
import {
  searchSessionMessages,
  type SessionMessageSearchQuery,
  type SessionMessageSearchResult,
} from '@/domain/conversation';
import type { ChatMessage } from '@tc-chat/core';

export type { SessionMessageSearchQuery, SessionMessageSearchResult } from '@/domain/conversation';

/** 检索数据源指示：面板据此呈现降级态说明（仅已载入消息）。 */
export type ConversationSearchSource = 'local';

export interface ConversationSearchOutcome {
  items: SessionMessageSearchResult[];
  source: ConversationSearchSource;
}

function resolveSearchSource(): ConversationSearchSource {
  // 后端检索接口待确认（Open Question Q2）；确认可用的环境返回 'remote'，此处固定本地。
  return 'local';
}

/** 检索当前会话消息：纯函数规则收口 domain（searchSessionMessages），Service 只做源选择与返回归一。 */
export function searchConversationMessages(
  messages: ChatMessage[],
  query: SessionMessageSearchQuery,
  now: number = Date.now(),
): ConversationSearchOutcome {
  return {
    items: searchSessionMessages(messages, query, now),
    source: resolveSearchSource(),
  };
}

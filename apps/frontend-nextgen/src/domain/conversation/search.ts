/**
 * 会话消息检索纯函数（chat-header-panels：历史消息面板）。
 *
 * 双源中「本地源」的规则核心：仅作用于当前会话已载入消息，规则与 UI 解耦，
 * 后端检索接口就绪后 Service 层换源、本函数保留为本地降级路径。
 */
import type { ChatMessage } from '@tc-chat/core';

/** 发送方筛选维度（local 源不含「平台」维度——后端索引能力，降级态隐藏）。 */
export type SessionMessageSenderFilter = 'user' | 'assistant' | 'system' | null;

export interface SessionMessageSearchQuery {
  /** 关键词；空串 = 不做关键词过滤。 */
  keyword: string;
  /** 发送方；null = 全部。 */
  sender: SessionMessageSenderFilter;
  /** 是否仅今天（近1天）。 */
  todayOnly: boolean;
}

export interface SessionMessageSearchResult {
  messageId: string;
  role: ChatMessage['role'];
  /** 命中片段（关键词前后各 ~16 字符的上下文摘要）。 */
  snippet: string;
  createdAt: number | null;
}

/**
 * 会话历史搜索面板模型（chat-header-panels：历史消息面板的搜索/筛选/结果装配）。
 *
 * 原定义在 chat 侧 useChatHeaderPanels（pages 裁剪面）；collab-form Task 17 上移至本纯模型家——
 * collab 保留链（useGroupChatHeaderPanels / GroupHistorySidebar / SessionHistorySearchPanel）
 * 与 chat 侧经同一来源消费，源切片裁掉 pages/Workspace/Chat 后仍可独立 typecheck。
 * 装配方负责 query 状态与本地过滤（searchSessionMessages），本类型只是消费面契约。
 */
export interface UseSessionHistorySearchModel {
  query: SessionMessageSearchQuery;
  setKeyword: (keyword: string) => void;
  setSender: (sender: SessionMessageSearchQuery['sender']) => void;
  setTodayOnly: (todayOnly: boolean) => void;
  /** 本地降级态指示：面板据此明示「仅已载入消息」。 */
  source: 'local';
  results: SessionMessageSearchResult[];
}

function isSameDay(timestamp: number, now: number): boolean {
  const a = new Date(timestamp);
  const b = new Date(now);
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** 提取命中片段：首个大小写不敏感命中位置前后各 ~16 字符，未命中整体截断。 */
export function extractMatchSnippet(content: string, keyword: string): string {
  const text = content.replace(/\s+/g, ' ').trim();
  if (!keyword) return text.slice(0, 36);
  const idx = text.toLowerCase().indexOf(keyword.toLowerCase());
  if (idx === -1) return text.slice(0, 36);
  const start = Math.max(0, idx - 16);
  const end = Math.min(text.length, idx + keyword.length + 20);
  return (start > 0 ? '…' : '') + text.slice(start, end) + (end < text.length ? '…' : '');
}

/** 本地检索：按 角色筛选 → 关键词（时序倒序）过滤已载入消息；纯函数无副作用。 */
export function searchSessionMessages(
  messages: ChatMessage[],
  query: SessionMessageSearchQuery,
  now: number = Date.now(),
): SessionMessageSearchResult[] {
  const keyword = query.keyword.trim().toLowerCase();
  const results: SessionMessageSearchResult[] = [];
  // 时序倒序：最新命中靠前（与面板浏览直觉一致）。
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message?.id) continue;
    if (query.sender && message.role !== query.sender) continue;
    if (query.todayOnly && (message.createdAt === undefined || !isSameDay(message.createdAt, now))) continue;
    if (keyword && !message.content.toLowerCase().includes(keyword)) continue;
    results.push({
      messageId: message.id,
      role: message.role,
      snippet: extractMatchSnippet(message.content, keyword),
      createdAt: message.createdAt ?? null,
    });
  }
  return results;
}

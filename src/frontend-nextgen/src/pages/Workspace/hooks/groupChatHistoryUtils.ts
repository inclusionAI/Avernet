import type { ChatMessage } from '@tc-chat/core';

/** 前置更早的历史消息并按 id 去重，保留升序旧→新排列；无新增则原样返回避免无谓 re-render。 */
export function prependUniqueMessages(prev: ChatMessage[], older: ChatMessage[]): ChatMessage[] {
  const ids = new Set(prev.map((message) => message.id));
  const fresh = older.filter((message) => !ids.has(message.id));
  return fresh.length > 0 ? [...fresh, ...prev] : prev;
}

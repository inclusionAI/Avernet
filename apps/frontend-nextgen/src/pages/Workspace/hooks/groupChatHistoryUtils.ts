import type { ChatMessage } from '@tc-chat/core';

/** 前置更早的历史消息并按 id 去重，保留升序旧→新排列；无新增则原样返回避免无谓 re-render。 */
export function prependUniqueMessages(prev: ChatMessage[], older: ChatMessage[]): ChatMessage[] {
  const ids = new Set(prev.map((message) => message.id));
  const fresh = older.filter((message) => !ids.has(message.id));
  return fresh.length > 0 ? [...fresh, ...prev] : prev;
}

/**
 * 过滤「渲染层不可见」的占位消息：assistant 且 pending 且无内容无 blocks 的消息气泡
 * （如运行中 run block 的初始占位）。消息区渲染与就绪判定（useGroupChatDisplayStatus）
 * 必须共用本过滤——两层若不同源，历史全为占位时会出现「顶栏已绿 + 骨架屏已撤 + 空态文案」
 * 的中间态（预览反馈实测），等 WS 推送占位获得内容后内容才浮现。
 */
export function filterVisibleGroupMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter((m) => !(m.role === 'assistant' && m.status === 'pending' && !m.content && !m.blocks?.length));
}

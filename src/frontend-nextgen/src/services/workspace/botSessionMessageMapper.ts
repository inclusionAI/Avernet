import type { BotMessageDto } from '@/services/backendApi/bots/privateBotSessionController';
import type { ChatMessage, MessageRole, TextBlock } from '@tc-chat/core';

function toRole(role: BotMessageDto['role']): MessageRole | null {
  if (role === 'user' || role === 'assistant' || role === 'system') return role;
  return null; // tool_use / tool_result 暂不渲染(YAGNI)
}

function parseTimestamp(raw: string): number {
  if (!raw) return NaN;
  return Date.parse(raw);
}

/** BotMessageDto[] → ChatMessage[]。入参来自后端「最新页在前、页内正序」,本函数按 gmt_create 升序排列
 *  为「旧→新」并完成 role/content 映射与空值过滤;无时间戳时保持原入参相对顺序(稳定排序)。 */
export function mapBotSessionMessages(items: BotMessageDto[]): ChatMessage[] {
  const indexed = items.map((m, index) => ({ m, index }));
  indexed.sort((a, b) => {
    const ta = parseTimestamp(a.m.gmt_create);
    const tb = parseTimestamp(b.m.gmt_create);
    const aValid = Number.isFinite(ta);
    const bValid = Number.isFinite(tb);
    if (aValid && bValid) return ta - tb;
    if (aValid !== bValid) return aValid ? -1 : 1;
    return a.index - b.index;
  });

  const out: ChatMessage[] = [];
  indexed.forEach(({ m, index }) => {
    const role = toRole(m.role);
    if (!role) return;
    const content = m.content ?? '';
    if (!content) return;
    const createdAt = m.gmt_create ? Date.parse(m.gmt_create) : undefined;
    out.push({
      id: m.message_id || `bot-history-${index}-${createdAt ?? 0}`,
      role,
      content,
      status: 'history',
      createdAt: Number.isFinite(createdAt) ? createdAt : undefined,
      blocks: [{ type: 'text', content }] as TextBlock[],
    });
  });
  return out;
}

// BCS 消息中首次出现需要 CDN 的 AixUI 组件时，才查询实际发送方 Bot 的资源。
import type { ChatMessage, TextBlock } from '@tc-chat/core';
import { extractTagAttr, resolveAixUi } from '@tc-chat/ui/es/utils/aixTags';
import MarkdownIt from 'markdown-it';

/** 协作群发送方 Bot 引用（owner 缺失时仍可发起无 owner 请求，按鉴权语义查到空）。 */
export interface SenderBotRef {
  /** 纯 botId（不含 owner 段）。 */
  botId: string;
  /** Bot 归属者工号（render-screens 接口按 owner 鉴权，非归属者不带 owner 查到空）。 */
  ownerId?: string;
}

/** BCS 系统消息的发送方 uuid（非真实 Bot，无副屏配置）。 */
export const BCS_SYSTEM_MESSAGE_BOT_UUID = 'bcs-system-message';

/** 发送方复合键（与 libraryCdnInjector 的 botCdnMaps 分桶键同构）。 */
export function senderBotKey(bot: SenderBotRef): string {
  return `${bot.botId}:${bot.ownerId ?? ''}`;
}

/** 解析单个 bot uuid（形如 `botId:entityId`）。非 Bot actor（人类/系统消息）返回 null。 */
function parseBotUuid(botUuid: string): SenderBotRef | null {
  const sepIndex = botUuid.indexOf(':');
  const botId = sepIndex >= 0 ? botUuid.slice(0, sepIndex) : botUuid;
  const ownerId = sepIndex >= 0 ? botUuid.slice(sepIndex + 1) : undefined;
  if (!botId || botId === BCS_SYSTEM_MESSAGE_BOT_UUID) return null;
  if (botId.startsWith('human_')) return null; // 人类 actor，非真实 Bot
  return { botId, ...(ownerId ? { ownerId } : {}) };
}

const markdown = new MarkdownIt({ html: true });

/** 复用 Markdown lexer 排除代码块/行内代码，再使用 SDK 的 AixUI 属性语义。 */
function needsBotCdn(content: string): boolean {
  if (!/<aixui\b/i.test(content)) return false;
  const lines = content.split(/\r?\n/);
  const tokens = markdown.parse(content, {});
  const fragments = tokens.flatMap((token) => {
    // SDK 的 AixUI block tokenizer 优先于缩进代码规则，允许开标签前存在任意空格。
    if (token.type === 'html_block') return [token.content];
    if (token.type === 'code_block' && /^ *<aixui\b/i.test(token.content)) {
      // MarkdownIt 已剥掉缩进，包括 SDK 不接受的 tab；回看原始行。
      const line = token.map ? lines[token.map[0]] ?? '' : '';
      const tagStart = line.search(/<aixui\b/i);
      if (tagStart >= 0 && !line.slice(0, tagStart).includes('\t')) return [token.content];
    }
    return (token.children ?? []).filter((child) => child.type === 'html_inline').map((child) => child.content);
  });
  for (const fragment of fragments) {
    const visible = fragment.replace(/<!--[\s\S]*?(?:-->|$)/g, '');
    for (const [, attrs] of visible.matchAll(/<AixUI\b((?:"[^"]*"|'[^']*'|[^'">])*)>/gi)) {
      // DOM 也接受无引号属性；兼容 SDK 的 data-* 镜像属性。
      const attr = (name: string) => extractTagAttr(attrs, name) ??
        attrs.match(new RegExp(`(?:^|\\s)(?:data-)?${name}\\s*=\\s*([^\\s'"=<>]+)`, 'i'))?.[1];
      const resolved = resolveAixUi({
        type: attr('type'), component: attr('component'), cardId: attr('cardId'),
        panel: attr('panel'), cdn: attr('cdn'),
      });
      // 内联业务组件也使用同一个 SDK CDN 查找入口，保留它的既有显示能力。
      if (resolved.source === 'component' && resolved.component && !resolved.cdn &&
          resolved.component.split('.')[0] !== 'taskPanel') return true;
    }
  }
  return false;
}

function messageNeedsBotCdn(message: ChatMessage): boolean {
  // 与 buildMessageBlocks 一致：已有渲染块优先，聚合 content 可能包含不会渲染的工具输出。
  if (message.blocks?.length) {
    return message.blocks.some((block) => block.type === 'text' && needsBotCdn((block as TextBlock).content));
  }
  return typeof message.content === 'string' && needsBotCdn(message.content);
}

/**
 * 归并需要 Bot CDN 的消息发送方，其他消息只提供归属回退的证据。
 *
 * - 优先明确发送方 `extra.senderId`，assistant 缺失时回退 SDK `extra.botUuid`（形如 `botId:entityId`
 *   或人类 actor `human_<userId>`）；
 * - uuid 自带 `:ownerId` 时 owner MUST 取 uuid 段——bot_id 不全局唯一（多 owner 的 default 同名），
 *   回退解析会命中别人的 default；
 * - uuid 缺 owner 段时回退：同 botId 已知 owner 唯一则采纳，否则按无 owner 请求（查到空可接受）；
 * - 跳过人类 actor 与 BCS 系统消息；
 * - 按 `${botId}:${ownerId}` 复合键去重，输出按 botId、ownerId 排序保证稳定性
 *   （同消息集合增量归并不改变既有次序）。
 */
export function collectSenderBots(messages: ChatMessage[]): SenderBotRef[] {
  const byKey = new Map<string, SenderBotRef>();
  const needed = new Set<string>();
  for (const message of messages) {
    if (message.role !== 'assistant') continue;
    const botUuid = message.extra?.senderId ?? message.extra?.botUuid;
    if (typeof botUuid !== 'string' || !botUuid) continue;
    const ref = parseBotUuid(botUuid);
    if (!ref) continue;
    const key = senderBotKey(ref);
    if (!byKey.has(key)) byKey.set(key, ref);
    if (messageNeedsBotCdn(message)) needed.add(key);
  }
  // owner 回退：uuid 缺 owner 时，若同 botId 已知 owner 唯一则采纳，避免把候选 Bot 拆成两桶。
  const knownOwnersByBot = new Map<string, Set<string>>();
  for (const bot of byKey.values()) {
    if (!bot.ownerId) continue;
    const owners = knownOwnersByBot.get(bot.botId) ?? new Set<string>();
    owners.add(bot.ownerId);
    knownOwnersByBot.set(bot.botId, owners);
  }
  const fallbacked = new Map<string, SenderBotRef>();
  for (const [key, bot] of byKey) {
    if (!needed.has(key)) continue;
    if (bot.ownerId) {
      fallbacked.set(key, bot);
      continue;
    }
    const owners = knownOwnersByBot.get(bot.botId);
    const uniqueOwner = owners && owners.size === 1 ? Array.from(owners)[0] : undefined;
    const resolved: SenderBotRef = uniqueOwner ? { botId: bot.botId, ownerId: uniqueOwner } : bot;
    fallbacked.set(senderBotKey(resolved), resolved);
  }
  return Array.from(fallbacked.values()).sort((a, b) => {
    const botCompare = a.botId.localeCompare(b.botId);
    return botCompare !== 0 ? botCompare : (a.ownerId ?? '').localeCompare(b.ownerId ?? '');
  });
}

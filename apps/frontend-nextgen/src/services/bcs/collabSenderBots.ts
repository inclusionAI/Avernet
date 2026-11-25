// 协作群消息发送方/参与者 Bot 集合归并（Spec: openspec/changes/collab-bot-cdn-preregister）。
//
// 副屏 <AixUI component="lib.Comp"> 的 CDN URL 来自「发送该消息的 Bot」的 render screens
// 配置（libraryCdnInjector Bot 级来源）。本文件从消息集合归并出发送方 Bot 去重集合，
// 供历史挂载前批量注册与 live 消息增量补注册消费；另从会话/群参与者归并 Bot 集合
// （collectParticipantBots），供 live 首条回复前预注册（回复渲染时同步查 map，晚注册不回填）。
import type { ChatMessage } from '@tc-chat/core';

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

/**
 * 会话/群参与者 Bot 集合（结构子集，SessionView.participants / GroupView.participants 同构）。
 * Bot 成员的 actorId 与消息发送方 botUuid 同构（形如 `botId:entityId`，见 groupChatAbortHelpers
 * 的互比），人类成员为 `human_<userId>`。
 */
export interface ParticipantBotSource {
  actorId: string;
  kind?: 'human' | 'bot';
}

/**
 * 归并参与者中的 Bot 集合，供 live 首条回复前预注册副屏 CDN。
 *
 * 用户发消息后 Bot 的实时回复渲染时同步查 CDN map，晚注册不回填——尚未在历史中
 * 发过言的会话成员 Bot（新会话/新成员/历史截断）MUST 靠参与者预注册补洞，
 * 否则其首条 live 回复的副屏报「缺少 CDN 地址」（老前端双源并集的动机）。
 * uuid 直接拆段，不做 owner 回退（参与者 uuid 权威；复合键去重交给 registerBotCdnList）。
 */
export function collectParticipantBots(participants: readonly ParticipantBotSource[]): SenderBotRef[] {
  const byKey = new Map<string, SenderBotRef>();
  for (const participant of participants) {
    if (participant.kind === 'human') continue;
    const ref = parseBotUuid(participant.actorId);
    if (!ref) continue;
    const key = senderBotKey(ref);
    if (!byKey.has(key)) byKey.set(key, ref);
  }
  return Array.from(byKey.values()).sort((a, b) => {
    const botCompare = a.botId.localeCompare(b.botId);
    return botCompare !== 0 ? botCompare : (a.ownerId ?? '').localeCompare(b.ownerId ?? '');
  });
}

/**
 * 归并消息集合中的发送方 Bot 集合。
 *
 * - 发送方取 `extra.botUuid`（groupMessageMapper 由 BCS DTO `sender` 平铺，形如 `botId:entityId`
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
  for (const message of messages) {
    const botUuid = message.extra?.botUuid;
    if (typeof botUuid !== 'string' || !botUuid) continue;
    const ref = parseBotUuid(botUuid);
    if (!ref) continue;
    const key = senderBotKey(ref);
    if (!byKey.has(key)) byKey.set(key, ref);
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

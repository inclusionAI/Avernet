import { Avatar, Style } from '@dicebear/core';
import bottts from '@dicebear/styles/bottts.json';

// Style 实例可跨头像复用(校验与解析只在构造时做一次),提为模块级缓存。
const botttsStyle = new Style(bottts);

export function generateBotAvatar(seed: string, index = 0): string {
  return new Avatar(botttsStyle, {
    seed: `${seed || 'bot'}-${index}`,
    size: 80,
    backgroundColor: ['b6e3f4', 'c0aede', 'd1d4f9', 'ffd5dc', 'ffdfbf'],
  }).toDataUri();
}

export function generateBotAvatarBatch(seed: string, batch: number): string[] {
  return Array.from({ length: 8 }, (_, index) => generateBotAvatar(seed, batch * 8 + index));
}

/** 与 Bot 管理共用固定种子的默认头像；不需要请求 owner-only 头像接口。 */
export function resolveBotAvatarUrl(avatarUrl: string | null | undefined, botId: string): string {
  if (avatarUrl?.trim()) return avatarUrl.trim();
  // 协作身份的 `:归属` 后缀不属于管理页 Bot ID，不能改变默认头像种子。
  const id = botId.trim();
  const separator = id.indexOf(':');
  return generateBotAvatar(separator < 0 ? id : id.slice(0, separator));
}

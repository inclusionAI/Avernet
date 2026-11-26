// 协作群 Bot 副屏 CDN 增量补注册（Spec: openspec/changes/collab-bot-cdn-preregister）。
//
// 历史挂载前的批量注册由 useManifestHistoryLoader 完成；本 hook 覆盖两类增量：
// - live 模式下新到达消息的发送方 Bot（如受邀 Bot 首次发言后的后续消息）；
// - 会话参与者 Bot 预注册——用户发消息后成员 Bot 的实时回复渲染时同步查 CDN map，
//   晚注册不回填，尚未发过言的成员 Bot（新会话/新成员）MUST 提前入桶，
//   否则其首条 live 回复的副屏报「缺少 CDN 地址」（对齐老前端 participants ∪ 发送方双源）。
// 两源合并按复合 key 去重 diff，仅对未入 botCdnMaps 桶的 Bot 触发 registerBotCdnList
// （桶缓存含失败负缓存，会话内同一 Bot 至多请求一次）。
import {
  collectParticipantBots,
  collectSenderBots,
  senderBotKey,
  type ParticipantBotSource,
  type SenderBotRef,
} from '@/services/bcs/collabSenderBots';
import { hasBotCdnBucket, registerBotCdnList } from '@/services/bcs/libraryCdnInjector';
import type { ChatMessage } from '@tc-chat/core';
import { useEffect, useMemo } from 'react';

export function useSenderBotCdnSync(
  sessionId: string | null,
  messages: ChatMessage[],
  participants?: readonly ParticipantBotSource[],
): void {
  const senderBots = useMemo(() => collectSenderBots(messages), [messages]);
  const participantBots = useMemo(() => collectParticipantBots(participants ?? []), [participants]);
  // 依赖 key：集合不变不重跑，避免流式 delta 每帧触发（对齐老前端 cdnBotsKey 手法）。
  const cdnBotsKey = useMemo(
    () => [...participantBots, ...senderBots].map(senderBotKey).join(','),
    [participantBots, senderBots],
  );

  useEffect(() => {
    if (!sessionId) return;
    // 两源合并按复合 key 去重（参与者源为先，owner 权威）。
    const byKey = new Map<string, SenderBotRef>([
      ...senderBots.map((bot) => [senderBotKey(bot), bot] as const),
      ...participantBots.map((bot) => [senderBotKey(bot), bot] as const),
    ]);
    const pending = Array.from(byKey.values()).filter((bot) => !hasBotCdnBucket(bot.botId, bot.ownerId));
    if (pending.length === 0) return;
    void registerBotCdnList(pending);
    // 集合由 cdnBotsKey 派生，key 不变时内容不变。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, cdnBotsKey]);
}

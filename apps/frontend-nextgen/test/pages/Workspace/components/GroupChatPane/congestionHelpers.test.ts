import type { DeliveryStatusView } from '@/domain/collaboration/types';
import {
  buildBotActivityItems,
  type BotActivityItem,
} from '@/pages/Workspace/components/GroupChatPane/congestionHelpers';
import { describe, expect, it } from '@jest/globals';

const participants = [
  { actorId: 'bot-a', kind: 'bot', name: '甲', role: 'member', mode: 'auto' },
  { actorId: 'bot-b', kind: 'bot', name: '乙', role: 'member', mode: 'auto' },
] as never;

function delivery(botId: string, id: string, preview?: string): DeliveryStatusView {
  return {
    delivery_id: id,
    message_id: `m-${id}`,
    target_bot_id: botId,
    flow_kind: 'group',
    kind: 'send',
    status: 'queued',
    state_version: 1,
    run_id: null,
    wait_reason: 'bot_capacity',
    admission_error: null,
    content_preview: preview ?? null,
  };
}

describe('buildBotActivityItems', () => {
  it('活跃 bot 与排队 bot 合并为同一 item（按 botId 归并）', () => {
    const items = buildBotActivityItems({
      activeRuns: [{ botId: 'bot-a', botName: '甲', runCount: 2, startedAt: 1_000 }],
      queuedDeliveries: [delivery('bot-a', 'd1', '排队内容')],
      processingDeliveries: [delivery('bot-a', 'd2')],
      participants,
    });
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      botId: 'bot-a',
      botName: '甲',
      active: { runCount: 2, startedAt: 1_000 },
    });
    expect(items[0].queued.map((d) => d.delivery_id)).toEqual(['d1']);
    expect(items[0].processing.map((d) => d.delivery_id)).toEqual(['d2']);
  });

  it('排序：活跃 bot 在前，仅排队/处理中的 bot 在后（各组内保持入序）', () => {
    const items = buildBotActivityItems({
      activeRuns: [{ botId: 'bot-b', botName: '乙', runCount: 1, startedAt: 2_000 }],
      queuedDeliveries: [delivery('bot-a', 'd1'), delivery('bot-c', 'd2')],
      processingDeliveries: [],
      participants,
    });
    expect(items.map((item) => item.botId)).toEqual(['bot-b', 'bot-a', 'bot-c']);
    expect(items[0].active).not.toBeNull();
    expect(items[1].active).toBeNull();
    expect(items[2].active).toBeNull();
  });

  it('仅活跃 bot：processing/queued 为空数组', () => {
    const items = buildBotActivityItems({
      activeRuns: [{ botId: 'bot-a', botName: '甲', runCount: 1, startedAt: 1_000 }],
      queuedDeliveries: [],
      processingDeliveries: [],
      participants,
    });
    expect(items).toEqual([
      { botId: 'bot-a', botName: '甲', active: { runCount: 1, startedAt: 1_000 }, processing: [], queued: [] },
    ]);
  });

  it('仅排队 bot：active 为 null；名称取 participants，查不到兜底 Bot', () => {
    const items = buildBotActivityItems({
      activeRuns: [],
      queuedDeliveries: [delivery('bot-b', 'd1'), delivery('bot-x', 'd2')],
      processingDeliveries: [],
      participants,
    });
    expect(items.map((item: BotActivityItem) => [item.botId, item.botName])).toEqual([
      ['bot-b', '乙'],
      ['bot-x', 'Bot'],
    ]);
    expect(items.every((item) => item.active === null)).toBe(true);
  });

  it('processing 与 queued 按 target_bot_id 各自归属', () => {
    const items = buildBotActivityItems({
      activeRuns: [],
      queuedDeliveries: [delivery('bot-a', 'q1'), delivery('bot-b', 'q2')],
      processingDeliveries: [delivery('bot-b', 'p1'), delivery('bot-c', 'p2')],
      participants,
    });
    const byBot = new Map(items.map((item) => [item.botId, item]));
    expect(byBot.get('bot-a')?.queued.map((d) => d.delivery_id)).toEqual(['q1']);
    expect(byBot.get('bot-a')?.processing).toEqual([]);
    expect(byBot.get('bot-b')?.queued.map((d) => d.delivery_id)).toEqual(['q2']);
    expect(byBot.get('bot-b')?.processing.map((d) => d.delivery_id)).toEqual(['p1']);
    expect(byBot.get('bot-c')?.queued).toEqual([]);
    expect(byBot.get('bot-c')?.processing.map((d) => d.delivery_id)).toEqual(['p2']);
  });

  it('空输入返回空数组', () => {
    expect(
      buildBotActivityItems({ activeRuns: [], queuedDeliveries: [], processingDeliveries: [], participants }),
    ).toEqual([]);
  });
});

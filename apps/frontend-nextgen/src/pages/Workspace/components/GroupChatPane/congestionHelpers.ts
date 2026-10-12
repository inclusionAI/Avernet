import type { DeliveryStatusView, DeliveryWaitReason, ParticipantView } from '@/domain/collaboration/types';

/** 按目标 bot 分组投递，返回带 bot 名称的分组列表。 */
export interface BotDeliveryGroup {
  botId: string;
  botName: string;
  deliveries: DeliveryStatusView[];
}

export function groupDeliveriesByBot(
  deliveries: DeliveryStatusView[],
  participants: ParticipantView[],
): BotDeliveryGroup[] {
  const nameMap = new Map(participants.filter((p) => p.kind === 'bot').map((p) => [p.actorId, p.name]));
  const groups = new Map<string, DeliveryStatusView[]>();
  for (const d of deliveries) {
    const list = groups.get(d.target_bot_id) ?? [];
    list.push(d);
    groups.set(d.target_bot_id, list);
  }
  return [...groups.entries()].map(([botId, items]) => ({
    botId,
    botName: nameMap.get(botId) ?? 'Bot',
    deliveries: items,
  }));
}

/** 活跃输出摘要（来自消息流 streaming 消息，见 GroupChatActiveRuns.collectActiveBotRuns）。 */
export interface ActiveRunSummary {
  botId: string;
  botName: string;
  runCount: number;
  startedAt?: number;
}

/**
 * 按 Bot 合并的活动模块视图模型：一个 bot 的输出中状态、终止操作
 * 与其排队/处理中投递收进同一个模块（替代旧的「处理中/排队中」全局分区）。
 */
export interface BotActivityItem {
  botId: string;
  botName: string;
  /** 活跃（streaming）run 摘要；无活跃输出为 null。 */
  active: { runCount: number; startedAt?: number } | null;
  /** 该 bot 处理中的投递（delivery 口径）。 */
  processing: DeliveryStatusView[];
  /** 该 bot 排队中的投递。 */
  queued: DeliveryStatusView[];
}

/**
 * 合并两路数据源为按 bot 自包含的活动列表：
 * 活跃 run（消息流）∪ 投递（queued/processing），按 botId 归并；
 * 排序为「活跃 bot 在前（保持入参顺序），仅有投递的 bot 在后（首次出现序）」。
 */
export function buildBotActivityItems(input: {
  activeRuns: ActiveRunSummary[];
  queuedDeliveries: DeliveryStatusView[];
  processingDeliveries: DeliveryStatusView[];
  participants: ParticipantView[];
}): BotActivityItem[] {
  const nameMap = new Map(input.participants.filter((p) => p.kind === 'bot').map((p) => [p.actorId, p.name]));
  const items = new Map<string, BotActivityItem>();
  const ensure = (botId: string): BotActivityItem => {
    let item = items.get(botId);
    if (!item) {
      item = { botId, botName: nameMap.get(botId) ?? 'Bot', active: null, processing: [], queued: [] };
      items.set(botId, item);
    }
    return item;
  };
  for (const run of input.activeRuns) {
    const item = ensure(run.botId);
    if (run.botName) item.botName = run.botName;
    item.active = { runCount: run.runCount, startedAt: run.startedAt };
  }
  for (const d of input.processingDeliveries) ensure(d.target_bot_id).processing.push(d);
  for (const d of input.queuedDeliveries) ensure(d.target_bot_id).queued.push(d);
  return [...items.values()];
}

/** 等待原因转中文显示文本。 */
export function humanizeWaitReason(reason: DeliveryWaitReason | null): string {
  switch (reason) {
    case 'prior_message_running':
      return '等待前一条消息完成';
    case 'bot_capacity':
      return 'Bot 处理名额已满';
    case 'rate_limited':
      return '触发频率限制';
    case 'bot_offline':
      return 'Bot 离线';
    case 'retry_backoff':
      return '重试退避中';
    case 'paused':
      return '已暂停';
    default:
      return '排队中';
  }
}

/** 计算投递的耗时（秒）。 */
export function getDeliveryElapsed(delivery: DeliveryStatusView, now: number): number | null {
  // 后端未提供 created_at，用 state_version 作为近似（越大越新）
  // 实际耗时需要从消息创建时间计算，这里暂时返回 null
  // TODO: 后端在 DeliveryStatusView 中补充 created_at 字段后实现真实耗时
  void delivery;
  void now;
  return null;
}

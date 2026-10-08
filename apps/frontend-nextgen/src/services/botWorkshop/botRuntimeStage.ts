import type { BotLifecycle, BotRuntimeStage } from '@/domain/botWorkshop';

/** 将工坊展示态转换为后端运行时 stage；未发布/异常态均回到可编辑的草稿运行时。 */
export function resolveBotRuntimeStage(lifecycle: BotLifecycle): BotRuntimeStage {
  if (lifecycle === 'prestable') return 'verify';
  if (lifecycle === 'running') return 'online';
  return 'draft';
}

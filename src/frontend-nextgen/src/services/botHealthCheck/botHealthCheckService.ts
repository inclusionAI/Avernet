import { getCapabilities } from '@/capabilities';
import type { BotHealthCapability, BotHealthCheckSummary, BotHealthCheckTarget } from '@/domain/botHealthCheck';
import {
  getHarnessDimHistory,
  getHarnessDimReport,
  startHarnessDiagnose,
  type HarnessDimHistoryResponseDto,
  type HarnessDimReportResponseDto,
} from '@/services/backendApi';
import type { BotActionAvailability, BotDomain } from '@/services/botWorkshop';
import { mapBotHealthSummary as mapBotHealthSummaryTransform } from './botHealthCheckSummary';

function getHealthCapability(): BotHealthCapability {
  return getCapabilities().getBotHealthCapability().value;
}

export function resolveBotHealthActionAvailability(bot: BotDomain, userId?: string): BotActionAvailability {
  if (bot.runtime.engine !== 'openclaw') {
    return { action: 'health-check', visible: false, enabled: false, disabledReason: '当前引擎不支持健康检查' };
  }
  if (!bot.runtime.visibleInOpenCore) {
    return { action: 'health-check', visible: false, enabled: false, disabledReason: '当前运行时不可见' };
  }
  if (!bot.harnessContext?.entityId) {
    return { action: 'health-check', visible: true, enabled: false, disabledReason: '缺少健康检查所需的实体信息' };
  }
  if (bot.lifecycle === 'offline') {
    return { action: 'health-check', visible: true, enabled: false, disabledReason: 'Bot 已下线或回收' };
  }
  if (!userId?.trim()) {
    return { action: 'health-check', visible: true, enabled: false, disabledReason: '缺少当前用户身份' };
  }
  return { action: 'health-check', visible: true, enabled: true };
}

export function toHealthCheckTarget(bot: BotDomain, userId?: string): BotHealthCheckTarget | undefined {
  const availability = resolveBotHealthActionAvailability(bot, userId);
  if (!availability.visible || !availability.enabled || !bot.harnessContext || !userId?.trim()) return undefined;
  return {
    botId: bot.id,
    userId: userId.trim(),
    botName: bot.name,
    engine: bot.runtime.engine,
    context: bot.harnessContext,
  };
}

export function mapBotHealthSummary(
  report: HarnessDimReportResponseDto,
  history: HarnessDimHistoryResponseDto,
  capability: BotHealthCapability = getHealthCapability(),
): BotHealthCheckSummary {
  return mapBotHealthSummaryTransform(report, history, capability);
}

export const botHealthCheckService = {
  getCapability: getHealthCapability,
  resolveAvailability: resolveBotHealthActionAvailability,
  toTarget: toHealthCheckTarget,
  mapSummary: mapBotHealthSummary,
  async load(target: BotHealthCheckTarget): Promise<BotHealthCheckSummary> {
    const [report, history] = await Promise.all([
      getHarnessDimReport({
        botId: target.botId,
        userId: target.userId,
        entityId: target.context.entityId,
        botPublishId: target.context.botPublishId,
      }),
      getHarnessDimHistory({
        botId: target.botId,
        userId: target.userId,
        entityId: target.context.entityId,
        botPublishId: target.context.botPublishId,
        page: 1,
        size: 20,
      }),
    ]);
    return mapBotHealthSummary(report, history);
  },
  async runDiagnose(target: BotHealthCheckTarget) {
    return startHarnessDiagnose(target.botId, target.userId, {
      entity_type: target.context.entityType,
      entity_id: target.context.entityId,
      scan_type: 'full',
      layer: 'L1',
      bot_publish_id: target.context.botPublishId,
    });
  },
};

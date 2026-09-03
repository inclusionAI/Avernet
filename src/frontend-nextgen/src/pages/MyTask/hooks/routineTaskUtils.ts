import type { ScheduledRoutineRecord, ScheduledRoutineRunRecord } from '@/services/scheduledTasks';

export const ALL_ROUTINE_BOT_VALUE = '__all__';

export interface RoutineBotTarget {
  botId: string;
  botName: string;
}

export function getRunSortTime(item: ScheduledRoutineRunRecord) {
  const value = item.actualTriggerAt ?? item.plannedTriggerAt ?? '';
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

export function getRoutineSortKey(item: ScheduledRoutineRecord) {
  return [item.botName || item.botId, item.name || item.id, item.id, item.runtimeStage ?? ''].join('::');
}

export function buildErrorMessage(errors: string[]) {
  if (errors.length === 0) return null;
  if (errors.length === 1) return errors[0];
  return `${errors[0]}（另有 ${errors.length - 1} 项失败）`;
}

/**
 * 行 key 三段式 botId::routineId::runtimeStage —— owner 聚合接口里同一 definition
 * 跨 draft/verify/online 会出多行，必须以 stage 区分。run/历史记录侧拿不到 stage，
 * 生成的是两段式 key，靠 isSameRoutineKey 的模糊匹配对齐。
 */
export function makeRoutineKey(botId: string, routineId: string, runtimeStage?: string) {
  return runtimeStage ? `${botId}::${routineId}::${runtimeStage}` : `${botId}::${routineId}`;
}

export function isSameRoutineKey(item: ScheduledRoutineRecord, routineKey: string | null) {
  if (!routineKey) return false;
  // 三段精确命中（带 stage 的点击选中）优先；两段模糊命中兜底（从历史记录跳转等无 stage 场景）。
  return (
    makeRoutineKey(item.botId, item.id, item.runtimeStage) === routineKey ||
    makeRoutineKey(item.botId, item.id) === routineKey
  );
}

function normalizeBotId(botId: string) {
  return botId.trim();
}

function uniqRoutineTargets(targets: RoutineBotTarget[]) {
  const seen = new Set<string>();
  return targets.filter((item) => {
    const key = normalizeBotId(item.botId);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function getSelectedTargets(selectedBotId: string, routineBots: RoutineBotTarget[]) {
  const normalizedSelectedBotId = normalizeBotId(selectedBotId);
  const uniqueTargets = uniqRoutineTargets(routineBots);
  if (!normalizedSelectedBotId || normalizedSelectedBotId === ALL_ROUTINE_BOT_VALUE) {
    return uniqueTargets;
  }
  return uniqueTargets.filter((item) => normalizeBotId(item.botId) === normalizedSelectedBotId);
}

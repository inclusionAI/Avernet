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
  return [item.botName || item.botId, item.name || item.id, item.id].join('::');
}

export function buildErrorMessage(errors: string[]) {
  if (errors.length === 0) return null;
  if (errors.length === 1) return errors[0];
  return `${errors[0]}（另有 ${errors.length - 1} 项失败）`;
}

export function makeRoutineKey(botId: string, routineId: string) {
  return `${botId}::${routineId}`;
}

export function isSameRoutineKey(item: ScheduledRoutineRecord, routineKey: string | null) {
  return Boolean(routineKey) && makeRoutineKey(item.botId, item.id) === routineKey;
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

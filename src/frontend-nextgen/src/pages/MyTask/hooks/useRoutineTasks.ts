import {
  fetchScheduledRoutineDetail,
  fetchScheduledRoutineRuns,
  fetchScheduledRoutines,
  type ScheduledRoutineRecord,
  type ScheduledRoutineRunRecord,
  triggerScheduledRoutine,
} from '@/services/scheduledTasks';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ALL_ROUTINE_BOT_VALUE,
  buildErrorMessage,
  getRoutineSortKey,
  getRunSortTime,
  getSelectedTargets,
  isSameRoutineKey,
  makeRoutineKey,
  type RoutineBotTarget,
} from './routineTaskUtils';

export { ALL_ROUTINE_BOT_VALUE, makeRoutineKey } from './routineTaskUtils';
export type { RoutineBotTarget } from './routineTaskUtils';

export function useRoutineTasks(
  selectedBotId: string,
  routineBots: RoutineBotTarget[],
  selectedRoutineKey: string | null,
  historyOpen: boolean,
  enabled = true,
) {
  const [routines, setRoutines] = useState<ScheduledRoutineRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selectedRoutineDetail, setSelectedRoutineDetail] = useState<ScheduledRoutineRecord | null>(null);
  const [selectedRoutineRuns, setSelectedRoutineRuns] = useState<ScheduledRoutineRunRecord[]>([]);
  const [selectedRoutineRunsLoading, setSelectedRoutineRunsLoading] = useState(false);
  const [selectedRoutineRunsError, setSelectedRoutineRunsError] = useState<string | null>(null);

  const [historyRuns, setHistoryRuns] = useState<ScheduledRoutineRunRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const normalizedSelectedBotId = useMemo(() => selectedBotId.trim(), [selectedBotId]);
  const routineTargets = useMemo(
    () => getSelectedTargets(normalizedSelectedBotId, routineBots),
    [normalizedSelectedBotId, routineBots],
  );

  const refreshRoutines = useCallback(async () => {
    if (!routineTargets.length) {
      setRoutines([]);
      setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const settled = await Promise.allSettled(routineTargets.map((target) => fetchScheduledRoutines(target.botId)));
      const items: ScheduledRoutineRecord[] = [];
      const errors: string[] = [];
      settled.forEach((result, index) => {
        const target = routineTargets[index];
        if (result.status === 'fulfilled') {
          items.push(...result.value);
        } else {
          errors.push(`Bot「${target?.botName ?? target?.botId ?? 'unknown'}」定时任务列表加载失败`);
        }
      });
      items.sort((a, b) => getRoutineSortKey(a).localeCompare(getRoutineSortKey(b)));
      setRoutines(items);
      setError(buildErrorMessage(errors));
    } catch (err) {
      const targetName = normalizedSelectedBotId === ALL_ROUTINE_BOT_VALUE ? '全部 Bot' : normalizedSelectedBotId;
      const message = err instanceof Error ? err.message : `${targetName} 定时任务列表加载失败`;
      setError(message);
      setRoutines([]);
    } finally {
      setLoading(false);
    }
  }, [normalizedSelectedBotId, routineTargets]);

  const refreshSelectedRoutineRuns = useCallback(async (routine: ScheduledRoutineRecord | null) => {
    if (!routine) {
      setSelectedRoutineDetail(null);
      setSelectedRoutineRuns([]);
      setSelectedRoutineRunsError(null);
      setSelectedRoutineRunsLoading(false);
      return;
    }
    setSelectedRoutineRunsLoading(true);
    setSelectedRoutineRunsError(null);
    try {
      const detail = await fetchScheduledRoutineDetail(routine.botId, routine.id).catch(() => routine);
      setSelectedRoutineDetail(detail);
      const list = await fetchScheduledRoutineRuns(routine.botId, routine.id, {}, detail);
      setSelectedRoutineRuns(list.slice().sort((a, b) => getRunSortTime(b) - getRunSortTime(a)));
    } catch (err) {
      const message = err instanceof Error ? err.message : '定时任务实例加载失败';
      setSelectedRoutineRunsError(message);
      setSelectedRoutineRuns([]);
    } finally {
      setSelectedRoutineRunsLoading(false);
    }
  }, []);

  const refreshHistoryRuns = useCallback(async (routineList: ScheduledRoutineRecord[]) => {
    if (!routineList.length) {
      setHistoryRuns([]);
      setHistoryError(null);
      setHistoryLoading(false);
      return;
    }
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const settled = await Promise.allSettled(
        routineList.map((item) => fetchScheduledRoutineRuns(item.botId, item.id, {}, item)),
      );
      const items: ScheduledRoutineRunRecord[] = [];
      const errors: string[] = [];
      settled.forEach((result, index) => {
        if (result.status === 'fulfilled') {
          items.push(...result.value);
        } else {
          errors.push(`Bot「${routineList[index]?.botId ?? 'unknown'}」历史任务加载失败`);
        }
      });
      setHistoryRuns(items.slice().sort((a, b) => getRunSortTime(b) - getRunSortTime(a)));
      setHistoryError(buildErrorMessage(errors));
    } catch (err) {
      const message = err instanceof Error ? err.message : '历史任务加载失败';
      setHistoryError(message);
      setHistoryRuns([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    setSelectedRoutineDetail(null);
    setSelectedRoutineRuns([]);
    setSelectedRoutineRunsLoading(false);
    setSelectedRoutineRunsError(null);
    setHistoryRuns([]);
    setHistoryLoading(false);
    setHistoryError(null);
  }, [normalizedSelectedBotId, enabled]);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      setError(null);
      return;
    }
    void refreshRoutines();
  }, [enabled, refreshRoutines]);

  useEffect(() => {
    if (!enabled) return;
    const selectedRoutine = routines.find((item) => isSameRoutineKey(item, selectedRoutineKey)) ?? null;
    void refreshSelectedRoutineRuns(selectedRoutine);
  }, [enabled, refreshSelectedRoutineRuns, routines, selectedRoutineKey]);

  useEffect(() => {
    if (!enabled || !historyOpen) {
      setHistoryRuns([]);
      setHistoryError(null);
      setHistoryLoading(false);
      return;
    }
    void refreshHistoryRuns(routines);
  }, [enabled, historyOpen, refreshHistoryRuns, routines]);

  const selectedRoutine = useMemo(
    () => selectedRoutineDetail ?? routines.find((item) => isSameRoutineKey(item, selectedRoutineKey)) ?? null,
    [routines, selectedRoutineDetail, selectedRoutineKey],
  );

  const historyRunsWithRoutineName = useMemo(
    () =>
      historyRuns.map((item) => ({
        ...item,
        routineName:
          item.routineName || routines.find((routine) => routine.id === item.routineId)?.name || item.routineId,
        botName:
          item.botName ||
          routines.find(
            (routine) => makeRoutineKey(routine.botId, routine.id) === makeRoutineKey(item.botId, item.routineId),
          )?.botName ||
          item.botId,
      })),
    [historyRuns, routines],
  );

  const runRoutine = useCallback(
    (routine: ScheduledRoutineRecord) => triggerScheduledRoutine(routine.botId, routine.id),
    [],
  );

  return {
    routines,
    loading,
    error,
    refreshRoutines,
    selectedRoutine,
    selectedRoutineRuns,
    selectedRoutineRunsLoading,
    selectedRoutineRunsError,
    historyRuns: historyRunsWithRoutineName,
    historyLoading,
    historyError,
    runRoutine,
  };
}

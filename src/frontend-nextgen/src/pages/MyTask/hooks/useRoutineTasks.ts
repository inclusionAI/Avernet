import {
  fetchOwnerScheduledRoutines,
  fetchScheduledRoutineDetail,
  fetchScheduledRoutineRuns,
  fetchScheduledRoutines,
  type ScheduledRoutineRecord,
  type ScheduledRoutineRunRecord,
  triggerScheduledRoutine,
} from '@/services/scheduledTasks';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  page: number,
  pageSize: number,
  selectedRoutineKey: string | null,
  historyOpen: boolean,
  enabled = true,
) {
  const [routines, setRoutines] = useState<ScheduledRoutineRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRoutineDetail, setSelectedRoutineDetail] = useState<ScheduledRoutineRecord | null>(null);
  const [selectedRoutineRuns, setSelectedRoutineRuns] = useState<ScheduledRoutineRunRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedRoutineRunsLoading, setSelectedRoutineRunsLoading] = useState(false);
  const [selectedRoutineRunsError, setSelectedRoutineRunsError] = useState<string | null>(null);
  const [historyRuns, setHistoryRuns] = useState<ScheduledRoutineRunRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const routinesRequestIdRef = useRef(0);
  const normalizedSelectedBotId = useMemo(() => selectedBotId.trim(), [selectedBotId]);
  const routineTargets = useMemo(
    () => getSelectedTargets(normalizedSelectedBotId, routineBots),
    [normalizedSelectedBotId, routineBots],
  );
  // 「全部」走 owner 聚合接口（含协作 Bot、跨 runtime stage、服务端全局分页）；
  // 单个 Bot 保留 per-bot 精确查询。
  const isAllBotsMode = !normalizedSelectedBotId || normalizedSelectedBotId === ALL_ROUTINE_BOT_VALUE;
  const refreshRoutines = useCallback(async () => {
    const requestId = ++routinesRequestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      if (isAllBotsMode) {
        const result = await fetchOwnerScheduledRoutines({ page, page_size: pageSize });
        const items = result.items.slice().sort((a, b) => getRoutineSortKey(a).localeCompare(getRoutineSortKey(b)));
        if (requestId !== routinesRequestIdRef.current) return;
        setRoutines(items);
        setTotal(result.total);
        return;
      }
      if (!routineTargets.length) {
        if (requestId !== routinesRequestIdRef.current) return;
        setRoutines([]);
        setTotal(0);
        return;
      }
      const settled = await Promise.allSettled(
        routineTargets.map((target) => fetchScheduledRoutines(target.botId, { page, page_size: pageSize })),
      );
      const items: ScheduledRoutineRecord[] = [];
      let total = 0;
      const errors: string[] = [];
      settled.forEach((result, index) => {
        const target = routineTargets[index];
        if (result.status === 'fulfilled') {
          items.push(...result.value.items);
          total += result.value.total;
        } else {
          errors.push(`Bot「${target?.botName ?? target?.botId ?? 'unknown'}」定时任务列表加载失败`);
        }
      });
      items.sort((a, b) => getRoutineSortKey(a).localeCompare(getRoutineSortKey(b)));
      if (requestId !== routinesRequestIdRef.current) return;
      setRoutines(items);
      setTotal(total);
      setError(buildErrorMessage(errors));
    } catch (err) {
      if (requestId !== routinesRequestIdRef.current) return;
      const targetName = isAllBotsMode ? '全部 Bot' : normalizedSelectedBotId;
      const message = err instanceof Error ? err.message : `${targetName} 定时任务列表加载失败`;
      setError(message);
      setRoutines([]);
      setTotal(0);
    } finally {
      if (requestId === routinesRequestIdRef.current) setLoading(false);
    }
  }, [isAllBotsMode, normalizedSelectedBotId, page, pageSize, routineTargets]);
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
    if (!enabled) {
      routinesRequestIdRef.current += 1;
      setRoutines([]);
      setTotal(0);
      setLoading(false);
      setError(null);
      return;
    }
    setSelectedRoutineDetail(null);
    setSelectedRoutineRuns([]);
    setSelectedRoutineRunsLoading(false);
    setSelectedRoutineRunsError(null);
    setHistoryRuns([]);
    setHistoryLoading(false);
    setHistoryError(null);
  }, [normalizedSelectedBotId, enabled]);
  useEffect(() => {
    if (!enabled) return;
    void refreshRoutines();
    return () => {
      routinesRequestIdRef.current += 1;
    };
  }, [enabled, refreshRoutines]);
  const selectedRoutineFromList = useMemo(
    () => routines.find((item) => isSameRoutineKey(item, selectedRoutineKey)) ?? null,
    [routines, selectedRoutineKey],
  );
  useEffect(() => {
    if (!enabled) return;
    void refreshSelectedRoutineRuns(selectedRoutineFromList);
  }, [enabled, refreshSelectedRoutineRuns, selectedRoutineFromList]);
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
    () =>
      selectedRoutineDetail && isSameRoutineKey(selectedRoutineDetail, selectedRoutineKey)
        ? selectedRoutineDetail
        : selectedRoutineFromList,
    [selectedRoutineDetail, selectedRoutineFromList, selectedRoutineKey],
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
    total,
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

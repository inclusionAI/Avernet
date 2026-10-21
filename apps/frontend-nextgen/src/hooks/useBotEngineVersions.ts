import {
  engineVersionUiState,
  supportsEngineVersion,
  type EngineVersionObservation,
  type EngineVersionTarget,
} from '@/domain/botEngineVersion';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import type { ContainerInstance } from '@/services/botWorkshop/botContainerService';
import {
  botEngineVersionService,
  type EngineVersionInspectionTarget,
} from '@/services/botWorkshop/botEngineVersionService';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

export type BotEngineVersionState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; observation: EngineVersionObservation; deviceUuid?: string; updateError?: string }
  | { kind: 'select_instance'; instances: ContainerInstance[] }
  | { kind: 'error'; message: string }
  | { kind: 'not_applicable' }
  | {
      kind: 'updating';
      observation: EngineVersionObservation;
      deviceUuid?: string;
      error?: string;
    };

interface UseBotEngineVersionsResult {
  states: Record<string, BotEngineVersionState | undefined>;
  inspect: (key: string) => Promise<void>;
  selectInstance: (key: string, deviceUuid: string) => Promise<void>;
  update: (key: string, onRestartSubmitted?: () => void) => Promise<boolean>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim() ? error.message : '引擎版本查询失败';
}

function canInspect(target: EngineVersionTarget): boolean {
  return target.enabled && supportsEngineVersion(target.engine)
    && botEngineVersionService.getRestartPermission(target.restartPermission).allowed;
}

function targetSignature(targets: EngineVersionTarget[]): string {
  return JSON.stringify(
    targets.map(({ key, botId, userId, ownerId, engine, enabled, restartPermission }) => [
      key,
      botId,
      userId,
      ownerId,
      engine,
      enabled,
      restartPermission,
    ]),
  );
}

function inspectionTarget(
  target: EngineVersionTarget,
  deviceUuid?: string,
  signal?: AbortSignal,
): EngineVersionInspectionTarget {
  return {
    botId: target.botId,
    userId: target.userId || '',
    ownerId: target.ownerId,
    stage: 'draft',
    deviceUuid,
    signal,
  };
}

export function useBotEngineVersions(targets: EngineVersionTarget[]): UseBotEngineVersionsResult {
  const signature = targetSignature(targets);
  const targetByKey = useMemo(() => new Map(targets.map((target) => [target.key, target])), [signature]);
  const [states, setStates] = useState<Record<string, BotEngineVersionState | undefined>>({});
  const activeRequests = useRef(new Map<string, AbortController>());
  const updateLocks = useRef(new Set<string>());
  const previousTargets = useRef(targetByKey);

  const inspect = useCallback(
    async (key: string, deviceUuid?: string) => {
      const target = targetByKey.get(key);
      if (!target || !canInspect(target)) {
        setStates((current) => ({ ...current, [key]: { kind: 'idle' } }));
        return;
      }

      activeRequests.current.get(key)?.abort();
      const controller = new AbortController();
      activeRequests.current.set(key, controller);
      setStates((current) => ({ ...current, [key]: { kind: 'loading' } }));
      try {
        const result = await botEngineVersionService.inspect(inspectionTarget(target, deviceUuid, controller.signal));
        if (controller.signal.aborted) return;
        if (result.kind === 'select_instance') {
          setStates((current) => ({ ...current, [key]: result }));
        } else if (engineVersionUiState(result.observation) === 'not_applicable') {
          setStates((current) => ({ ...current, [key]: { kind: 'not_applicable' } }));
        } else {
          setStates((current) => ({ ...current, [key]: result }));
        }
      } catch (error) {
        // Permissions can be revoked after inventory was loaded. Background
        // inspection must not turn that expected race into a page-wide error.
        const refusal = botEngineVersionService.getInspectionRefusal(error);
        if (refusal) {
          if (refusal.notificationKey) useErrorNotifyStore.getState().cancel(refusal.notificationKey);
          if (!controller.signal.aborted) setStates((current) => ({ ...current, [key]: { kind: 'idle' } }));
          return;
        }
        if (controller.signal.aborted) return;
        setStates((current) => ({ ...current, [key]: { kind: 'error', message: errorMessage(error) } }));
      } finally {
        if (activeRequests.current.get(key) === controller) activeRequests.current.delete(key);
      }
    },
    [targetByKey],
  );

  useEffect(() => {
    const continuingUpdates = new Set<string>();
    for (const key of updateLocks.current) {
      const before = previousTargets.current.get(key);
      const after = targetByKey.get(key);
      if (before && after && (!after.restartPermission.serviceBot || after.restartPermission.runtimeStage === 'draft') && before.botId === after.botId && before.userId === after.userId && before.ownerId === after.ownerId && before.engine === after.engine) {
        continuingUpdates.add(key);
      }
    }
    for (const [key, controller] of activeRequests.current) {
      if (!continuingUpdates.has(key)) {
        if (updateLocks.current.has(key)) {
          toast.error('当前 Bot 已不满足版本检查条件，已停止引擎版本确认，请刷新查看运行状态');
        }
        controller.abort();
        activeRequests.current.delete(key);
      }
    }
    setStates((current) => {
      const nextStates: Record<string, BotEngineVersionState | undefined> = {};
      for (const target of targetByKey.values()) {
        nextStates[target.key] = continuingUpdates.has(target.key)
          ? current[target.key]
          : canInspect(target) ? { kind: 'loading' } : { kind: 'idle' };
      }
      return nextStates;
    });
    previousTargets.current = targetByKey;
    for (const target of targetByKey.values()) {
      if (!continuingUpdates.has(target.key) && canInspect(target)) void inspect(target.key);
    }
  }, [signature, targetByKey, inspect]);

  useEffect(() => () => {
    for (const controller of activeRequests.current.values()) controller.abort();
    activeRequests.current.clear();
  }, []);

  const selectInstance = useCallback((key: string, deviceUuid: string) => inspect(key, deviceUuid), [inspect]);

  const update = useCallback(
    async (key: string, onRestartSubmitted?: () => void): Promise<boolean> => {
      const target = targetByKey.get(key);
      const state = states[key];
      const restartPermission = target
        ? botEngineVersionService.getRestartPermission(target.restartPermission)
        : undefined;
      if (
        !target ||
        !target.enabled ||
        !restartPermission?.allowed ||
        !state ||
        (state.kind !== 'ready' && state.kind !== 'updating') ||
        engineVersionUiState(state.observation) !== 'updatable' ||
        updateLocks.current.has(key)
      )
        return false;

      const deviceUuid = state.deviceUuid || state.observation.context.instance_id;
      if (!deviceUuid) {
        setStates((current) => ({ ...current, [key]: { kind: 'error', message: '请先选择一个引擎实例' } }));
        return false;
      }
      activeRequests.current.get(key)?.abort();
      const controller = new AbortController();
      activeRequests.current.set(key, controller);
      updateLocks.current.add(key);
      setStates((current) => ({ ...current, [key]: { kind: 'updating', observation: state.observation, deviceUuid } }));
      let submitted = false;
      try {
        const observation = await botEngineVersionService.restartAndConfirmUpdate(
          { ...inspectionTarget(target, deviceUuid, controller.signal), restartPermission: target.restartPermission },
          state.observation,
          () => {
            submitted = true;
            toast.success('重启请求已提交');
            onRestartSubmitted?.();
          },
        );
        if (controller.signal.aborted) return false;
        setStates((current) => ({
          ...current,
          [key]: { kind: 'ready', observation, deviceUuid: observation.context.instance_id },
        }));
        toast.success('引擎版本已更新');
        return true;
      } catch (error) {
        if (controller.signal.aborted) return false;
        if (submitted) toast.error(`重启请求已提交，但尚未确认引擎更新：${errorMessage(error)}`);
        setStates((current) => ({
          ...current,
          [key]: { kind: 'ready', observation: state.observation, deviceUuid, updateError: errorMessage(error) },
        }));
        return false;
      } finally {
        if (activeRequests.current.get(key) === controller) activeRequests.current.delete(key);
        updateLocks.current.delete(key);
      }
    },
    [states, targetByKey],
  );

  return { states, inspect, selectInstance, update };
}

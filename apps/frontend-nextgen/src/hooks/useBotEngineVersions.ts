import {
  engineVersionUiState,
  supportsEngineVersion,
  type EngineVersionObservation,
  type EngineVersionTarget,
} from '@/domain/botEngineVersion';
import type { ContainerInstance } from '@/services/botWorkshop/botContainerService';
import {
  botEngineVersionService,
  type EngineVersionInspectionTarget,
} from '@/services/botWorkshop/botEngineVersionService';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

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
  update: (key: string) => Promise<boolean>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim() ? error.message : '引擎版本查询失败';
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

  const inspect = useCallback(
    async (key: string, deviceUuid?: string) => {
      const target = targetByKey.get(key);
      if (!target || !target.enabled || !supportsEngineVersion(target.engine)) {
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
        if (controller.signal.aborted) return;
        setStates((current) => ({ ...current, [key]: { kind: 'error', message: errorMessage(error) } }));
      } finally {
        if (activeRequests.current.get(key) === controller) activeRequests.current.delete(key);
      }
    },
    [targetByKey],
  );

  useEffect(() => {
    const nextStates: Record<string, BotEngineVersionState | undefined> = {};
    for (const target of targetByKey.values()) {
      nextStates[target.key] =
        target.enabled && supportsEngineVersion(target.engine) ? { kind: 'loading' } : { kind: 'idle' };
    }
    setStates(nextStates);
    for (const controller of activeRequests.current.values()) controller.abort();
    activeRequests.current.clear();
    for (const target of targetByKey.values()) {
      if (target.enabled && supportsEngineVersion(target.engine)) void inspect(target.key);
    }
    return () => {
      for (const controller of activeRequests.current.values()) controller.abort();
      activeRequests.current.clear();
    };
  }, [signature, targetByKey, inspect]);

  const selectInstance = useCallback((key: string, deviceUuid: string) => inspect(key, deviceUuid), [inspect]);

  const update = useCallback(
    async (key: string): Promise<boolean> => {
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
      try {
        const observation = await botEngineVersionService.restartAndConfirmUpdate(
          { ...inspectionTarget(target, deviceUuid, controller.signal), restartPermission: target.restartPermission },
          state.observation,
        );
        if (controller.signal.aborted) return false;
        setStates((current) => ({
          ...current,
          [key]: { kind: 'ready', observation, deviceUuid: observation.context.instance_id },
        }));
        return true;
      } catch (error) {
        if (controller.signal.aborted) return false;
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

import {
  engineVersionNoUpdateMessage,
  engineVersionUiState,
  isEngineVersionUpdated,
  type EngineRestartPermissionInput,
  type EngineVersionObservation,
  type EngineVersionUpdateAvailability,
} from '@/domain/botEngineVersion';
import type { BotDomain } from '@/domain/botWorkshop';
import {
  getBotEngineStatus,
  getBotEngineVersion,
  getBotRestartStatus,
  restartBot,
} from '@/services/backendApi/bots/botController';
import type {
  BotEngineRuntimeRequestOptions,
  BotEngineVersionDto,
} from '@/services/backendApi/bots/botEngineVersionDtos';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { isEnvelopeFailure } from '@/services/backendApi/types';
import { botContainerService, type ContainerInstance } from './botContainerService';
import { resolveBotRuntimeStage } from './botRuntimeStage';

export interface EngineVersionInspectionTarget extends BotEngineRuntimeRequestOptions {
  botId: string;
}

export interface EngineVersionMutationTarget extends EngineVersionInspectionTarget {
  restartPermission: EngineRestartPermissionInput;
}

export type EngineVersionInspection =
  | { kind: 'ready'; observation: EngineVersionObservation; deviceUuid?: string }
  | { kind: 'select_instance'; instances: ContainerInstance[] };

export interface EngineRestartPermission {
  allowed: boolean;
  disabledReason?: string;
}

type EngineVersionAvailabilityState =
  | { kind: 'idle' | 'loading' | 'select_instance' | 'not_applicable' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; observation: EngineVersionObservation; updateError?: string }
  | { kind: 'updating'; observation: EngineVersionObservation };

const RESTART_TIMEOUT_MS = 90_000;
const RESTART_POLL_INTERVAL_MS = 1_500;

function failureMessage(value: unknown, fallback: string): string {
  return value instanceof Error && value.message.trim() ? value.message : fallback;
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

function retryableTransportFailure(error: unknown): boolean {
  if (!(error instanceof BackendRequestError) || error.status === undefined) return true;
  return error.status === 408 || error.status === 425 || error.status === 429 || error.status >= 500;
}

function toEngineVersionObservation(dto: BotEngineVersionDto): EngineVersionObservation {
  return {
    detection: {
      status: dto.detection.status,
      ...(dto.detection.reason ? { reason: dto.detection.reason } : {}),
    },
    ...(dto.current ? { current: { ...dto.current } } : {}),
    release_target: { ...dto.release_target },
    comparison: dto.comparison,
    restart_hint: dto.restart_hint,
    ...(dto.restart_reason ? { restart_reason: dto.restart_reason } : {}),
    context: { ...dto.context },
  };
}

function getRestartPermission(input: EngineRestartPermissionInput): EngineRestartPermission {
  if (input.lockedByOther) {
    return { allowed: false, disabledReason: 'Bot 正由其他协作者编辑' };
  }
  if (input.disabledActions?.restart) {
    return { allowed: false, disabledReason: input.disabledActions.restart };
  }
  if (!input.actions.includes('restart')) {
    return { allowed: false, disabledReason: '当前账号没有 Bot 重启权限' };
  }
  if (input.serviceBot && input.runtimeStage !== 'draft') {
    return { allowed: false, disabledReason: '服务 Bot 仅支持在草稿阶段更新引擎版本。' };
  }
  return { allowed: true };
}

function getUpdateAvailability(
  permissionInput: EngineRestartPermissionInput,
  state?: EngineVersionAvailabilityState,
): EngineVersionUpdateAvailability {
  const permission = getRestartPermission(permissionInput);
  if (!permission.allowed) return { enabled: false, disabledReason: permission.disabledReason };
  if (state?.kind === 'updating') {
    return { enabled: false, disabledReason: '正在更新并确认引擎状态' };
  }
  if (state?.kind === 'loading') {
    return { enabled: false, disabledReason: '正在读取引擎版本' };
  }
  if (state?.kind === 'ready') {
    if (engineVersionUiState(state.observation) === 'updatable') return { enabled: true };
    if (engineVersionUiState(state.observation) === 'no_update') {
      return { enabled: false, disabledReason: engineVersionNoUpdateMessage(state.observation) };
    }
    return { enabled: false, disabledReason: state.updateError || '当前版本状态不可确认' };
  }
  if (state?.kind === 'error') return { enabled: false, disabledReason: state.message };
  if (state?.kind === 'select_instance') {
    return { enabled: false, disabledReason: '请选择一个引擎实例后查看版本' };
  }
  return { enabled: false, disabledReason: '当前版本状态不可确认' };
}

export const botEngineVersionService = {
  getRestartPermissionInput(bot: BotDomain): EngineRestartPermissionInput {
    return {
      actions: bot.actions,
      disabledActions: bot.disabledActions,
      lockedByOther: bot.lock?.status === 'other',
      serviceBot: bot.serviceMode === 'service',
      runtimeStage: resolveBotRuntimeStage(bot.lifecycle),
    };
  },

  getRestartPermission,
  getUpdateAvailability,

  async inspect(target: EngineVersionInspectionTarget): Promise<EngineVersionInspection> {
    const userId = target.userId.trim();
    if (!userId) throw new Error('登录身份未就绪，无法读取引擎版本');
    const ownerId = target.ownerId?.trim();
    if (!ownerId) throw new Error('Bot 实体归属不可用，无法读取引擎版本');
    const requestTarget: BotEngineRuntimeRequestOptions = {
      userId,
      ownerId,
      stage: 'draft',
      deviceUuid: target.deviceUuid,
      signal: target.signal,
    };

    try {
      const response = await getBotEngineVersion(target.botId, requestTarget);
      if (isEnvelopeFailure(response) || !response.data) {
        throw new Error(response.message || '引擎版本信息不可用');
      }
      const observation = toEngineVersionObservation(response.data);
      return {
        kind: 'ready',
        observation,
        deviceUuid: target.deviceUuid || observation.context.instance_id || undefined,
      };
    } catch (error) {
      if (target.deviceUuid || !(error instanceof BackendRequestError) || error.status !== 409) throw error;
      // The Backend requests an explicit device_uuid for a multi-instance Bot.
      // Enumerate choices, but never pick one on the user's behalf.
      const snapshot = await botContainerService.list(target.botId, userId, target.ownerId);
      if (snapshot.instances.length > 1) {
        return { kind: 'select_instance', instances: snapshot.instances };
      }
      throw new Error(failureMessage(error, '请选择一个引擎实例后重试'));
    }
  },

  async restartAndConfirmUpdate(
    target: EngineVersionMutationTarget,
    current: EngineVersionObservation,
  ): Promise<EngineVersionObservation> {
    const permission = getRestartPermission(target.restartPermission);
    if (!permission.allowed) throw new Error(permission.disabledReason || '当前账号没有 Bot 重启权限');
    if (engineVersionUiState(current) !== 'updatable') {
      throw new Error('当前实例没有可执行的引擎更新');
    }
    const ownerId = target.ownerId?.trim();
    if (!ownerId) throw new Error('Bot 实体归属不可用，无法更新引擎版本');
    const deviceUuid = target.deviceUuid || current.context.instance_id;
    if (!deviceUuid) throw new Error('请先选择一个引擎实例');
    const requestTarget: BotEngineRuntimeRequestOptions = {
      userId: target.userId,
      ownerId,
      stage: 'draft',
      deviceUuid,
      signal: target.signal,
    };

    // The restart endpoint acts on the Bot, not an arbitrary selected device.
    // Require the canonical draft to agree before starting a replacement.
    const canonicalTarget = { ...requestTarget, deviceUuid: undefined };
    const before = await getBotEngineVersion(target.botId, canonicalTarget);
    if (isEnvelopeFailure(before) || !before.data || before.data.context.instance_id !== deviceUuid) {
      throw new Error('当前草稿实例已变化，请刷新版本信息后重试');
    }
    if (engineVersionUiState(toEngineVersionObservation(before.data)) !== 'updatable') {
      throw new Error('当前实例没有可执行的引擎更新');
    }
    if (target.signal?.aborted) throw target.signal.reason ?? new Error('引擎更新已取消');
    const restart = await restartBot(target.botId, {
      userId: target.userId,
      ownerId,
      signal: target.signal,
    });
    if (isEnvelopeFailure(restart)) throw new Error(restart.message || 'Bot 重启请求失败');

    const deadline = Date.now() + RESTART_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (target.signal?.aborted) throw target.signal.reason ?? new Error('引擎更新已取消');
      let botStatus;
      try {
        botStatus = await getBotRestartStatus(target.botId, canonicalTarget);
      } catch (error) {
        if (!retryableTransportFailure(error)) throw error;
        await sleep(RESTART_POLL_INTERVAL_MS);
        continue;
      }
      if (isEnvelopeFailure(botStatus)) throw new Error(botStatus.message || 'Bot 重启状态查询失败');
      if (botStatus.data?.status === 'FAILED') throw new Error('Bot 重启失败，请稍后重试');
      if (!botStatus.data?.is_ready) {
        await sleep(RESTART_POLL_INTERVAL_MS);
        continue;
      }
      let statusResponse;
      try {
        statusResponse = await getBotEngineStatus(target.botId, canonicalTarget);
      } catch (error) {
        if (!retryableTransportFailure(error)) throw error;
        await sleep(RESTART_POLL_INTERVAL_MS);
        continue;
      }
      if (isEnvelopeFailure(statusResponse)) {
        throw new Error(statusResponse.message || '引擎运行状态查询失败');
      }

      if (statusResponse.data?.running) {
        let versionResponse;
        try {
          versionResponse = await getBotEngineVersion(target.botId, canonicalTarget);
        } catch (error) {
          if (!retryableTransportFailure(error)) throw error;
          await sleep(RESTART_POLL_INTERVAL_MS);
          continue;
        }
        if (isEnvelopeFailure(versionResponse)) {
          throw new Error(versionResponse.message || '引擎版本回读失败');
        }
        if (versionResponse.data) {
          const observation = toEngineVersionObservation(versionResponse.data);
          if (isEngineVersionUpdated(observation)) return observation;
        }
      }
      await sleep(RESTART_POLL_INTERVAL_MS);
    }
    throw new Error('无法确认引擎更新已完成，请稍后重试');
  },
};

import type { EngineVersionObservation } from '@/domain/botEngineVersion';
import * as botController from '@/services/backendApi/bots/botController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import * as botContainerService from '@/services/botWorkshop/botContainerService';
import { botEngineVersionService } from '@/services/botWorkshop/botEngineVersionService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { afterEach, beforeEach, describe, expect, jest, test } from '@jest/globals';

jest.mock('@/services/backendApi/bots/botController');
jest.mock('@/services/botWorkshop/botContainerService');

const observation: EngineVersionObservation = {
  detection: { status: 'known' },
  current: { release_id: 'REL-1', raw_version: '1.2.0' },
  release_target: { status: 'available', release_id: 'REL-2' },
  comparison: 'older',
  restart_hint: 'recommended',
  context: { bot_id: 'bot-1', stage: 'draft', checked_at: '2026-10-20T00:00:00Z', instance_id: 'device-1' },
};

beforeEach(() => {
  jest.mocked(botController.getBotEngineVersion).mockReset();
  jest.mocked(botController.getBotEngineStatus).mockReset();
  jest.mocked(botController.restartBotEngine).mockReset();
  jest.mocked(botContainerService.botContainerService.list).mockReset();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('botEngineVersionService.inspect', () => {
  test('queries one explicitly selected draft instance with caller and owner identity', async () => {
    jest.mocked(botController.getBotEngineVersion).mockResolvedValue({
      code: 200000,
      data: observation,
    });

    const result = await botEngineVersionService.inspect({
      botId: 'bot-1',
      userId: 'caller-1',
      ownerId: 'owner-1',
      deviceUuid: 'device-1',
    });

    expect(botController.getBotEngineVersion).toHaveBeenCalledWith('bot-1', {
      userId: 'caller-1',
      ownerId: 'owner-1',
      stage: 'draft',
      deviceUuid: 'device-1',
    });
    expect(result).toEqual({ kind: 'ready', observation, deviceUuid: 'device-1' });
  });

  test('fails closed when the canonical owner entity is missing', async () => {
    await expect(botEngineVersionService.inspect({ botId: 'bot-1', userId: 'caller-1', ownerId: '' })).rejects.toThrow(
      'Bot 实体归属不可用',
    );
    expect(botController.getBotEngineVersion).not.toHaveBeenCalled();
  });

  test('asks the user to choose among multiple instances and never selects the first automatically', async () => {
    jest.mocked(botController.getBotEngineVersion).mockRejectedValue(
      new BackendRequestError('Select an existing workspace instance', {
        status: 409,
        apiPath: '/openapi/v1/bots/bot-1/engine-version',
      }),
    );
    jest.mocked(botContainerService.botContainerService.list).mockResolvedValue({
      botId: 'bot-1',
      summary: { total: 2, healthy: 2, abnormal: 0, restarting: 0, unknown: 0 },
      instances: [
        { id: 'device-a', status: 'healthy', engine: 'openclaw' },
        { id: 'device-b', status: 'healthy', engine: 'openclaw' },
      ],
    });

    const result = await botEngineVersionService.inspect({ botId: 'bot-1', userId: 'caller-1', ownerId: 'owner-1' });

    expect(result).toEqual({
      kind: 'select_instance',
      instances: [
        { id: 'device-a', status: 'healthy', engine: 'openclaw' },
        { id: 'device-b', status: 'healthy', engine: 'openclaw' },
      ],
    });
    expect(botController.getBotEngineVersion).toHaveBeenCalledTimes(1);
  });
});

describe('botEngineVersionService.restartAndConfirmUpdate', () => {
  const target = {
    botId: 'bot-1',
    userId: 'caller-1',
    ownerId: 'owner-1',
    deviceUuid: 'device-1',
    restartPermission: { actions: ['engine_restart'] },
  };

  test('does not restart when the selected instance has no Backend update recommendation', async () => {
    await expect(
      botEngineVersionService.restartAndConfirmUpdate(target, {
        ...observation,
        comparison: 'same',
        restart_hint: 'none',
      }),
    ).rejects.toThrow('当前实例没有可执行的引擎更新');

    expect(botController.restartBotEngine).not.toHaveBeenCalled();
  });

  test('does not restart without explicit engine-restart permission', async () => {
    await expect(
      botEngineVersionService.restartAndConfirmUpdate({ ...target, restartPermission: { actions: [] } }, observation),
    ).rejects.toThrow('当前账号没有引擎重启权限');
    expect(botController.restartBotEngine).not.toHaveBeenCalled();
  });

  test('waits for the selected engine to run and re-reads that same device before success', async () => {
    jest.useFakeTimers();
    jest.mocked(botController.restartBotEngine).mockResolvedValue({
      code: 200000,
      data: { bot_id: 'bot-1', status: 'restarting' },
    });
    jest
      .mocked(botController.getBotEngineStatus)
      .mockResolvedValueOnce({ code: 200000, data: { engine: 'openclaw', active_connections: 0, running: false } })
      .mockResolvedValueOnce({ code: 200000, data: { engine: 'openclaw', active_connections: 0, running: true } })
      .mockResolvedValueOnce({ code: 200000, data: { engine: 'openclaw', active_connections: 1, running: true } });
    jest
      .mocked(botController.getBotEngineVersion)
      .mockResolvedValueOnce({ code: 200000, data: observation })
      .mockResolvedValueOnce({ code: 200000, data: { ...observation, comparison: 'same', restart_hint: 'none' } });

    const result = botEngineVersionService.restartAndConfirmUpdate(target, observation);
    await jest.advanceTimersByTimeAsync(4_500);
    await expect(result).resolves.toMatchObject({ comparison: 'same', context: { instance_id: 'device-1' } });

    expect(botController.restartBotEngine).toHaveBeenCalledWith('bot-1', {
      userId: 'caller-1',
      ownerId: 'owner-1',
      stage: 'draft',
      deviceUuid: 'device-1',
    });
    expect(botController.getBotEngineStatus).toHaveBeenCalledTimes(3);
    expect(botController.getBotEngineStatus).toHaveBeenCalledWith('bot-1', {
      userId: 'caller-1',
      ownerId: 'owner-1',
      stage: 'draft',
      deviceUuid: 'device-1',
    });
    expect(botController.getBotEngineVersion).toHaveBeenNthCalledWith(2, 'bot-1', {
      userId: 'caller-1',
      ownerId: 'owner-1',
      stage: 'draft',
      deviceUuid: 'device-1',
    });
    jest.useRealTimers();
  });

  test('does not call an in-flight restart or stale version read a completed update', async () => {
    jest.useFakeTimers();
    jest.mocked(botController.restartBotEngine).mockResolvedValue({
      code: 200000,
      data: { bot_id: 'bot-1', status: 'restarting' },
    });
    jest.mocked(botController.getBotEngineStatus).mockResolvedValue({
      code: 200000,
      data: { engine: 'openclaw', active_connections: 0, running: false },
    });

    const result = botEngineVersionService.restartAndConfirmUpdate(target, observation);
    const rejection = result.catch((error: unknown) => error);
    await jest.advanceTimersByTimeAsync(90_000);
    expect(await rejection).toMatchObject({ message: '无法确认引擎更新已完成，请稍后重试' });

    expect(botController.restartBotEngine).toHaveBeenCalledTimes(1);
    expect(botController.getBotEngineVersion).not.toHaveBeenCalled();
    jest.useRealTimers();
  });
});

describe('botEngineVersionService.getUpdateAvailability', () => {
  const bot = mapBotDto({
    bot_id: 'bot-1',
    bot_name: 'Openclaw Bot',
    engine: 'openclaw',
    status: 'ACTIVE',
    actions: ['engine_restart'],
  }).item;

  test('reports permission and collaboration-lock reasons before version state', () => {
    const restricted = { ...bot, actions: [], disabledActions: { engine_restart: '需要先申请权限' } };
    expect(botEngineVersionService.getUpdateAvailability(restricted, { kind: 'loading' })).toEqual({
      enabled: false,
      disabledReason: '需要先申请权限',
    });

    const locked = { ...bot, lock: { status: 'other' as const } };
    expect(
      botEngineVersionService.getUpdateAvailability(botEngineVersionService.getRestartPermissionInput(locked), {
        kind: 'loading',
      }),
    ).toEqual({
      enabled: false,
      disabledReason: 'Bot 正由其他协作者编辑',
    });
  });

  test('returns enabled only for a permitted updatable observation', () => {
    expect(botEngineVersionService.getUpdateAvailability(bot, { kind: 'ready', observation })).toEqual({
      enabled: true,
    });
    expect(botEngineVersionService.getUpdateAvailability(bot, { kind: 'loading' })).toEqual({
      enabled: false,
      disabledReason: '正在读取引擎版本',
    });
    expect(
      botEngineVersionService.getUpdateAvailability(bot, {
        kind: 'ready',
        observation: { ...observation, comparison: 'same', restart_hint: 'none' },
      }),
    ).toEqual({ enabled: false, disabledReason: '当前实例已是最新版本。' });
  });

  test('blocks service Bot mutation outside draft stage', () => {
    expect(
      botEngineVersionService.getUpdateAvailability(
        { actions: ['engine_restart'], serviceBot: true, runtimeStage: 'online' },
        { kind: 'ready', observation },
      ),
    ).toEqual({ enabled: false, disabledReason: '服务 Bot 仅支持在草稿阶段更新引擎版本。' });
  });

  test('maps list lifecycle through the shared runtime-stage resolver', () => {
    const offlineService = { ...bot, serviceMode: 'service' as const, lifecycle: 'offline' as const };
    expect(botEngineVersionService.getRestartPermissionInput(offlineService).runtimeStage).toBe('draft');
    expect(
      botEngineVersionService.getRestartPermission(botEngineVersionService.getRestartPermissionInput(offlineService))
        .allowed,
    ).toBe(true);
  });
});

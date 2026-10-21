/** @jest-environment jsdom */

import type { EngineVersionTarget } from '@/domain/botEngineVersion';
import { useBotEngineVersions } from '@/hooks/useBotEngineVersions';
import * as botController from '@/services/backendApi/bots/botController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import * as botContainerService from '@/services/botWorkshop/botContainerService';
import { botEngineVersionService } from '@/services/botWorkshop/botEngineVersionService';
import { beforeEach, describe, expect, jest, test } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('sonner', () => {
  const { jest: mockJest } = require('@jest/globals');
  return { toast: { success: mockJest.fn(), error: mockJest.fn() } };
});

jest.mock('@/services/backendApi/bots/botController');
jest.mock('@/services/botWorkshop/botContainerService');

const target: EngineVersionTarget = {
  key: 'bot-1:owner-1',
  botId: 'bot-1',
  userId: 'caller-1',
  ownerId: 'owner-1',
  engine: 'openclaw',
  enabled: true,
  restartPermission: { actions: ['restart'] },
};

const version = {
  detection: { status: 'known' as const },
  current: { release_id: 'REL-1', raw_version: '1.2.0' },
  release_target: { status: 'available' as const, release_id: 'REL-2' },
  comparison: 'older' as const,
  restart_hint: 'recommended' as const,
  context: { bot_id: 'bot-1', stage: 'draft', checked_at: '2026-10-20T00:00:00Z', instance_id: 'device-1' },
};

beforeEach(() => {
  jest.mocked(toast.error).mockClear();
  jest.mocked(toast.success).mockClear();
  jest.mocked(botController.getBotEngineVersion).mockReset();
  jest.mocked(botController.getBotEngineStatus).mockReset();
  jest.mocked(botController.restartBotEngine).mockReset();
  jest.mocked(botContainerService.botContainerService.list).mockReset();
});

describe('useBotEngineVersions', () => {
  test('loads an enabled OpenClaw Bot and preserves the Backend observation', async () => {
    jest.mocked(botController.getBotEngineVersion).mockResolvedValue({ code: 200000, data: version });

    const { result } = renderHook(() => useBotEngineVersions([target]));

    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('ready'));
    expect(result.current.states[target.key]).toMatchObject({
      kind: 'ready',
      observation: version,
      deviceUuid: 'device-1',
    });
    expect(botController.getBotEngineVersion).toHaveBeenCalledWith('bot-1', {
      userId: 'caller-1',
      ownerId: 'owner-1',
      stage: 'draft',
      deviceUuid: undefined,
      signal: expect.any(AbortSignal),
    });
  });

  test('does not request when the caller marks a chat as out of scope', async () => {
    const { result } = renderHook(() => useBotEngineVersions([{ ...target, enabled: false }]));

    expect(result.current.states[target.key]?.kind).toBe('idle');
    expect(botController.getBotEngineVersion).not.toHaveBeenCalled();
  });

  test('requires an explicit instance choice when the Backend reports a multi-instance Bot', async () => {
    jest
      .mocked(botController.getBotEngineVersion)
      .mockRejectedValueOnce(
        new BackendRequestError('Select an existing workspace instance', {
          status: 409,
          apiPath: '/openapi/v1/bots/bot-1/engine-version',
        }),
      )
      .mockResolvedValueOnce({ code: 200000, data: version });
    jest.mocked(botContainerService.botContainerService.list).mockResolvedValue({
      botId: 'bot-1',
      summary: { total: 2, healthy: 2, abnormal: 0, restarting: 0, unknown: 0 },
      instances: [
        { id: 'device-a', status: 'healthy', engine: 'openclaw' },
        { id: 'device-b', status: 'healthy', engine: 'openclaw' },
      ],
    });

    const { result } = renderHook(() => useBotEngineVersions([target]));
    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('select_instance'));
    expect(botController.getBotEngineVersion).toHaveBeenCalledTimes(1);

    await act(async () => result.current.selectInstance(target.key, 'device-b'));

    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('ready'));
    expect(botController.getBotEngineVersion).toHaveBeenLastCalledWith('bot-1', {
      userId: 'caller-1',
      ownerId: 'owner-1',
      stage: 'draft',
      deviceUuid: 'device-b',
      signal: expect.any(AbortSignal),
    });
  });

  test('rechecks when the selected session target changes', async () => {
    jest.mocked(botController.getBotEngineVersion).mockResolvedValue({ code: 200000, data: version });
    const { result, rerender } = renderHook(
      ({ currentTarget }: { currentTarget: EngineVersionTarget }) => useBotEngineVersions([currentTarget]),
      { initialProps: { currentTarget: target } },
    );

    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('ready'));
    const nextTarget = { ...target, key: 'bot-1:owner-1:session-2' };
    rerender({ currentTarget: nextTarget });
    await waitFor(() => expect(result.current.states[nextTarget.key]?.kind).toBe('ready'));

    expect(botController.getBotEngineVersion).toHaveBeenCalledTimes(2);
  });

  test('keeps the replacement device identity after a successful Bot restart', async () => {
    jest.mocked(botController.getBotEngineVersion).mockResolvedValue({ code: 200000, data: version });
    const restart = jest.spyOn(botEngineVersionService, 'restartAndConfirmUpdate').mockResolvedValue({
      ...version,
      comparison: 'same',
      restart_hint: 'none',
      context: { ...version.context, instance_id: 'replacement-2' },
    });
    const { result } = renderHook(() => useBotEngineVersions([target]));
    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('ready'));
    await act(async () => {
      await result.current.update(target.key);
    });
    expect(result.current.states[target.key]).toMatchObject({ kind: 'ready', deviceUuid: 'replacement-2' });
    restart.mockRestore();
  });

  test('continues the accepted restart across pending/list refresh but cancels a removed target', async () => {
    jest.mocked(botController.getBotEngineVersion).mockResolvedValue({ code: 200000, data: version });
    let finish: (value: typeof version) => void = () => undefined;
    const restart = jest.spyOn(botEngineVersionService, 'restartAndConfirmUpdate').mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const { result, rerender } = renderHook(({ targets }: { targets: EngineVersionTarget[] }) => useBotEngineVersions(targets), { initialProps: { targets: [target] } });
    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('ready'));
    let pending!: Promise<boolean>;
    act(() => { pending = result.current.update(target.key); });
    const signal = restart.mock.calls[0][0].signal;
    rerender({ targets: [{ ...target, enabled: false, restartPermission: { actions: [] } }] });
    expect(signal?.aborted).toBe(false);
    expect(result.current.states[target.key]?.kind).toBe('updating');
    await act(async () => { finish({ ...version, context: { ...version.context, instance_id: 'new-device' } }); await pending; });
    expect(result.current.states[target.key]).toMatchObject({ kind: 'ready', deviceUuid: 'new-device' });
    restart.mockRestore();
  });

  test.each([['removed', []], ['changed Bot', [{ ...target, botId: 'another-bot' }]]])('cancels verification when target is %s', async (_name, nextTargets) => {
    jest.mocked(botController.getBotEngineVersion).mockResolvedValue({ code: 200000, data: version });
    let finish: (value: typeof version) => void = () => undefined;
    const restart = jest.spyOn(botEngineVersionService, 'restartAndConfirmUpdate').mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const { result, rerender } = renderHook(({ targets }: { targets: EngineVersionTarget[] }) => useBotEngineVersions(targets), { initialProps: { targets: [target] } });
    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('ready'));
    let pending!: Promise<boolean>;
    act(() => { pending = result.current.update(target.key); });
    const signal = restart.mock.calls[0][0].signal;
    rerender({ targets: nextTargets });
    expect(signal?.aborted).toBe(true);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('已停止引擎版本确认'));
    await act(async () => { finish(version); await pending; });
    expect(result.current.states[target.key]?.kind).not.toBe('updating');
    restart.mockRestore();
  });

  test('releases the confirmation on submission, blocks duplicates and reports verification failure without resubmitting', async () => {
    jest.mocked(botController.getBotEngineVersion).mockResolvedValue({ code: 200000, data: version });
    let accept: () => void = () => undefined;
    let rejectVerification: (error: Error) => void = () => undefined;
    const restart = jest.spyOn(botEngineVersionService, 'restartAndConfirmUpdate').mockImplementation((...args: Parameters<typeof botEngineVersionService.restartAndConfirmUpdate>) => {
      const onSubmitted = args[2];
      accept = () => onSubmitted?.();
      return new Promise((_resolve, reject) => { rejectVerification = reject; });
    });
    const { result } = renderHook(() => useBotEngineVersions([target]));
    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('ready'));
    const onSubmitted = jest.fn();
    let pending!: Promise<boolean>;
    act(() => { pending = result.current.update(target.key, onSubmitted); });
    expect(onSubmitted).not.toHaveBeenCalled();
    act(() => { accept(); });
    expect(onSubmitted).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalledWith('重启请求已提交');
    await act(async () => { expect(await result.current.update(target.key)).toBe(false); });
    expect(restart).toHaveBeenCalledTimes(1);
    await act(async () => { rejectVerification(new Error('version not confirmed')); await pending; });
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('version not confirmed'));
    expect(result.current.states[target.key]).toMatchObject({ kind: 'ready', updateError: 'version not confirmed' });
    expect(onSubmitted).toHaveBeenCalledTimes(1);
    restart.mockRestore();
  });

  test('requires explicit engine-restart permission before invoking update', async () => {
    jest.mocked(botController.getBotEngineVersion).mockResolvedValue({ code: 200000, data: version });
    const restartAndConfirm = jest.spyOn(botEngineVersionService, 'restartAndConfirmUpdate');
    restartAndConfirm.mockResolvedValue({
      ...version,
      comparison: 'same',
      restart_hint: 'none',
    });
    const unauthorizedTarget = { ...target, restartPermission: { actions: [] } };
    const { result } = renderHook(() => useBotEngineVersions([unauthorizedTarget]));

    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('ready'));
    let updated = true;
    await act(async () => {
      updated = await result.current.update(target.key);
    });

    expect(updated).toBe(false);
    expect(restartAndConfirm).not.toHaveBeenCalled();
    restartAndConfirm.mockRestore();
  });
});


test('stops service version verification when the card leaves draft', async () => {
  jest.mocked(botController.getBotEngineVersion).mockResolvedValue({ code: 200000, data: version });
  const service: EngineVersionTarget = { ...target, restartPermission: { actions: ['restart'], serviceBot: true, runtimeStage: 'draft' } };
  let finish: (value: typeof version) => void = () => undefined;
  const restart = jest.spyOn(botEngineVersionService, 'restartAndConfirmUpdate').mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  try {
    const { result, rerender } = renderHook(({ targets }: { targets: EngineVersionTarget[] }) => useBotEngineVersions(targets), { initialProps: { targets: [service] } });
    await waitFor(() => expect(result.current.states[target.key]?.kind).toBe('ready'));
    let pending!: Promise<boolean>;
    act(() => { pending = result.current.update(target.key); });
    const signal = restart.mock.calls[0][0].signal;
    rerender({ targets: [{ ...service, enabled: false, restartPermission: { ...service.restartPermission, runtimeStage: undefined } }] });
    expect(signal?.aborted).toBe(true);
    expect(result.current.states[target.key]?.kind).toBe('idle');
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('已停止引擎版本确认'));
    await act(async () => { finish(version); await pending; });
    expect(botController.getBotEngineVersion).toHaveBeenCalledTimes(1);
  } finally {
    restart.mockRestore();
  }
});

/** @jest-environment jsdom */

import BotEngineVersionDialogs from '@/components/BotWorkshop/BotEngineVersionDialogs';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
import { beforeEach, describe, expect, jest, test } from '@jest/globals';
import { act, fireEvent, render, screen } from '@testing-library/react';

const observation = {
  detection: { status: 'known' as const },
  current: { raw_version: '1.2.0' },
  release_target: { status: 'available' as const, release_id: 'REL-2' },
  comparison: 'older' as const,
  restart_hint: 'recommended' as const,
  context: { bot_id: 'bot-1', stage: 'draft', checked_at: '2026-10-20T00:00:00Z' },
};

const defaults = {
  botName: '测试 Bot',
  state: { kind: 'ready', observation } as BotEngineVersionState,
  infoOpen: true,
  updateOpen: false,
  canRestart: true,
  onCloseInfo: jest.fn(),
  onCloseUpdate: jest.fn(),
  onSelectInstance: jest.fn(),
  onRequestUpdate: jest.fn(),
  onConfirmUpdate: jest.fn<() => Promise<boolean>>().mockResolvedValue(true),
};

describe('BotEngineVersionDialogs', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('shows the observed current and target versions without invented release metadata', () => {
    render(<BotEngineVersionDialogs {...defaults} />);

    expect(screen.getByText('当前版本')).toBeTruthy();
    expect(screen.getByText('1.2.0')).toBeTruthy();
    expect(screen.getByText('目标版本')).toBeTruthy();
    expect(screen.getByText('REL-2')).toBeTruthy();
    expect(screen.queryByText(/critical|严重/)).toBeNull();
    expect(screen.queryByText(/release notes|更新说明/)).toBeNull();
    expect((screen.getByRole('button', { name: '更新引擎版本' }) as HTMLButtonElement).disabled).toBe(false);
  });

  test('requires an explicit instance choice', () => {
    const state: BotEngineVersionState = {
      kind: 'select_instance',
      instances: [
        { id: 'device-a', status: 'healthy', engine: 'openclaw' },
        { id: 'device-b', status: 'healthy', engine: 'openclaw' },
      ],
    };
    render(<BotEngineVersionDialogs {...defaults} state={state} />);

    expect(screen.getByText('请选择引擎实例')).toBeTruthy();
    expect(screen.getByRole('button', { name: /device-a/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /device-b/ })).toBeTruthy();
    expect(defaults.onSelectInstance).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /device-b/ }));
    expect(defaults.onSelectInstance).toHaveBeenCalledWith('device-b');
  });
  test('keeps the dialog open when no restart request was accepted', async () => {
    const onConfirmUpdate = jest.fn<() => Promise<boolean>>().mockResolvedValue(false);
    render(<BotEngineVersionDialogs {...defaults} infoOpen={false} updateOpen onConfirmUpdate={onConfirmUpdate} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '更新并重启 Bot' })); });
    expect(defaults.onCloseUpdate).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '取消' })).toBeTruthy();
  });

  test('closes on restart submission without waiting for version verification', async () => {
    let submitted: () => void = () => undefined;
    let finish: (result: boolean) => void = () => undefined;
    const onConfirmUpdate = jest.fn((onSubmitted: () => void) => {
      submitted = onSubmitted;
      return new Promise<boolean>((resolve) => { finish = resolve; });
    });
    render(<BotEngineVersionDialogs {...defaults} infoOpen={false} updateOpen onConfirmUpdate={onConfirmUpdate} />);
    fireEvent.click(screen.getByRole('button', { name: '更新并重启 Bot' }));
    expect(defaults.onCloseUpdate).not.toHaveBeenCalled();
    await act(async () => { submitted(); });
    expect(defaults.onCloseUpdate).toHaveBeenCalledTimes(1);
    await act(async () => { finish(true); });
    expect(defaults.onCloseUpdate).toHaveBeenCalledTimes(1);
  });

});

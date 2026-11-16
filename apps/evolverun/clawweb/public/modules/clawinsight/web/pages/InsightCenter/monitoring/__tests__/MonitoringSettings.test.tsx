import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MonitoringSettings } from '../MonitoringSettings';
const api = vi.hoisted(() => ({ options: vi.fn(), configure: vi.fn() }));
vi.mock('../../../../api/monitoring', () => ({ monitoringApi: api, monitoringErrorText: () => '请求失败' }));
const bot = { botRef: 'ref', botId: 'default', botName: '我的 Bot', ownerId: '001', env: 'prod',
  configuration: { diagnosisEnabled: true, recoveryEnabled: false, version: 7 } };
beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  api.options.mockResolvedValue({ items: [bot], nextCursor: 'next' });
  api.configure.mockResolvedValue({ updated: 1, failed: [] });
});
it('shows mixed state and writes the visible joint switch with a version, preserving notification settings on the server', async () => {
  const changed = vi.fn();
  render(<MonitoringSettings onChanged={changed} onClose={vi.fn()} />);
  expect(await screen.findByText('部分开启')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('switch', { name: '我的 Bot 诊断监控' }));
  await waitFor(() => expect(api.configure).toHaveBeenCalledWith({ botRef: 'ref', enabled: true, version: 7 }));
  expect(changed).toHaveBeenCalledOnce();
});
it('uses the server whole-roster operation from any search/page and displays partial failure', async () => {
  api.configure.mockResolvedValue({ updated: 60, failed: ['stale'] });
  render(<MonitoringSettings onChanged={vi.fn()} onClose={vi.fn()} />);
  await screen.findByText('部分开启');
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() => expect(api.options.mock.lastCall?.[3]).toBe('next'));
  fireEvent.click(screen.getByRole('button', { name: '一键全关' }));
  await waitFor(() => expect(api.configure).toHaveBeenCalledWith({ all: true, enabled: false }));
  expect(await screen.findByText('已更新 60 个 Bot，1 个未成功，请刷新后重试。')).toBeInTheDocument();
});

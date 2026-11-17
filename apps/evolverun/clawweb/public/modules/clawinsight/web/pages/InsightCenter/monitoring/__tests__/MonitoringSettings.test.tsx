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
it('shows the platform contacts and disables only the service Bot switch', async () => {
  api.options.mockResolvedValue({ items: [bot, { ...bot, botRef: 'service', botName: '服务助手', isServiceBot: true }], nextCursor: null });
  render(<MonitoringSettings onChanged={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByText(/请联系山宗、安远、沐荛/)).toBeInTheDocument();
  expect(await screen.findByText('服务 Bot 请联系平台')).toBeInTheDocument();
  const service = screen.getByRole('switch', { name: '服务助手 诊断监控' });
  expect(service).toBeDisabled();
  expect(screen.getByRole('switch', { name: '我的 Bot 诊断监控' })).toBeEnabled();
  fireEvent.click(service);
  expect(api.configure).not.toHaveBeenCalled();
});
it('shows only the joint switch state and writes both settings with a version', async () => {
  const changed = vi.fn();
  render(<MonitoringSettings onChanged={changed} onClose={vi.fn()} />);
  expect(await screen.findByText('已关闭')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('switch', { name: '我的 Bot 诊断监控' }));
  await waitFor(() => expect(api.configure).toHaveBeenCalledWith({ botRef: 'ref', enabled: true, version: 7 }));
  expect(changed).toHaveBeenCalledOnce();
});
it('uses the server whole-roster operation from any search/page and displays partial failure', async () => {
  api.configure.mockResolvedValue({ updated: 60, failed: ['stale'] });
  render(<MonitoringSettings onChanged={vi.fn()} onClose={vi.fn()} />);
  await screen.findByText('已关闭');
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() => expect(api.options.mock.lastCall?.[3]).toBe('next'));
  fireEvent.click(screen.getByRole('button', { name: '一键全关' }));
  await waitFor(() => expect(api.configure).toHaveBeenCalledWith({ all: true, enabled: false }));
  expect(await screen.findByText('已更新 60 个 Bot，1 个未成功，请刷新后重试。')).toBeInTheDocument();
});
it('keeps a save conflict visible after refreshing the latest Bot configuration', async () => {
  api.configure.mockRejectedValue({ status: 409 });
  render(<MonitoringSettings onChanged={vi.fn()} onClose={vi.fn()} />);
  await screen.findByText('已关闭');
  fireEvent.click(screen.getByRole('switch', { name: '我的 Bot 诊断监控' }));
  await waitFor(() => expect(api.options).toHaveBeenCalledTimes(2));
  expect(await screen.findByText('已关闭')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('配置已被更新，请刷新后重试。');
});

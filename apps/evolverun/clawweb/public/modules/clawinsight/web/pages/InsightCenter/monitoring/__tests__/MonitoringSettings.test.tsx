import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MonitoringSettings } from '../MonitoringSettings';
const api = vi.hoisted(() => ({ options: vi.fn(), configure: vi.fn() }));
vi.mock('../../../../api/monitoring', () => ({ monitoringApi: api, monitoringErrorText: () => '请求失败' }));
const bot = { botRef: 'ref', botId: 'default', botName: '我的 Bot', ownerId: '001', env: 'prod', activeEngine: 'openclaw', botType: 'personal',
  configuration: { diagnosisEnabled: true, recoveryEnabled: false, version: 7 } };
beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  api.options.mockResolvedValue({ items: [bot], nextCursor: 'next' });
  api.configure.mockResolvedValue({ updated: 1, failed: [] });
});
it('shows reasons and gray switches for every unsupported Bot, including enabled settings', async () => {
  const blocked = [
    { ...bot, botRef: 'service', botName: '服务助手', isServiceBot: true, botType: 'service', configurationDisabledReason: '服务 Bot 请联系平台评估开启' },
    { ...bot, botRef: 'desktop', botName: '桌面助手', botType: 'desktop', configurationDisabledReason: '桌面 Bot 暂不支持' },
    { ...bot, botRef: 'other', botName: '其他引擎助手', activeEngine: 'claude_code', configurationDisabledReason: '当前引擎暂不支持' },
  ].map(item => ({ ...item, configuration: { diagnosisEnabled: true, recoveryEnabled: true, version: 1 } }));
  api.options.mockResolvedValue({ items: [bot, { ...bot, botRef: 'te', botName: 'TE 助手', activeEngine: 'teclaw' }, ...blocked], nextCursor: null });
  render(<MonitoringSettings onChanged={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByText(/请联系山宗、安远、沐荛/)).toBeInTheDocument();
  expect(await screen.findByText('服务 Bot 请联系平台评估开启')).toBeInTheDocument();
  expect(screen.getByText(/仅支持 OpenClaw、TE 引擎，桌面 Bot 暂不支持/)).toBeInTheDocument();
  for (const item of blocked) {
    const toggle = screen.getByRole('switch', { name: `${item.botName} 诊断监控` });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(toggle.closest('li')).toHaveClass('settings-disabled-bot');
    expect(screen.getByText(item.configurationDisabledReason)).toBeInTheDocument();
    fireEvent.click(toggle);
  }
  expect(screen.getByRole('switch', { name: '我的 Bot 诊断监控' })).toBeEnabled();
  expect(screen.getByRole('switch', { name: 'TE 助手 诊断监控' })).toBeEnabled();
  expect(screen.getByText('TE', { exact: true })).toBeInTheDocument();
  expect(screen.getAllByText('OpenClaw', { exact: true })).toHaveLength(3);
  expect(screen.getByText('claude_code', { exact: true })).toBeInTheDocument();
  expect(screen.getByText('服务 Bot', { exact: true })).toBeInTheDocument();
  expect(screen.getByText('桌面 Bot', { exact: true })).toBeInTheDocument();
  expect(api.configure).not.toHaveBeenCalled();
});
it('shows the host practice link only when configured', async () => {
  const props = { onChanged: vi.fn(), onClose: vi.fn() };
  const { rerender } = render(<MonitoringSettings {...props} />);
  await screen.findByText('已关闭');
  const name = '查看诊断自愈实践案例';
  expect(screen.queryByRole('link', { name })).not.toBeInTheDocument();

  const practiceUrl = 'https://example.com/recovery#practice';
  rerender(<MonitoringSettings {...props} practiceUrl={practiceUrl} />);
  const link = screen.getByRole('link', { name });
  expect(link).toHaveAttribute('href', practiceUrl);
  expect(link).toHaveAttribute('target', '_blank');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
});
it('shows only the joint switch state and writes both settings with a version', async () => {
  const changed = vi.fn();
  render(<MonitoringSettings onChanged={changed} onClose={vi.fn()} />);
  expect(await screen.findByText('已关闭')).toBeInTheDocument();
  expect(api.options.mock.lastCall?.[5]).toBe('prod');
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

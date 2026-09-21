import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MonitoringBotSelector } from '../MonitoringBotSelector';
import { MonitoringEnrollment } from '../MonitoringEnrollment';
import type { BotOption } from '../../../../types/monitoring';
const api = vi.hoisted(() => ({ options: vi.fn(), enroll: vi.fn() }));
vi.mock('../../../../api/monitoring', async original => ({ ...await original<object>(), monitoringApi: api }));
const bot = (ownerId: string, enrolled = false): BotOption => ({ botRef: `ref-${ownerId}`, botId: 'default', ownerId, env: 'test', botName: `Bot ${ownerId}`,
  enrollmentState: enrolled ? 'ENROLLED' : 'NOT_ENROLLED', capabilities: { canView: true, canRequestEnrollment: !enrolled },
  monitoring: enrolled ? { status: 'PAUSED', checkedAt: '', lastSuccessfulCheckAt: null, diagnosedSessionCount: 2, diagnosisCount: 3, alertCount: 1, unidentifiedSessionDiagnosisCount: 1 } : null });
const defaults = { selected: null, isAdmin: false, startDate: '2026-09-01', endDate: '2026-09-10', revision: 0, onSelect: vi.fn(), onUnavailable: vi.fn() };
const open = () => fireEvent.click(screen.getByRole('button', { name: /^监控 Bot/ }));
beforeEach(() => {
  vi.resetAllMocks();
  api.options.mockResolvedValue({ items: [bot('001', true), bot('002')], nextCursor: null });
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function(this: HTMLDialogElement) { this.open = false; } });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
describe('bounded searchable Bot selector', () => {
  it('hides candidates until opened; uses uniform avatars, three-state badges and no enrollment actions', async () => {
    const { container } = render(<MonitoringBotSelector {...defaults} />);
    await waitFor(() => expect(defaults.onSelect).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('Bot 001')).not.toBeInTheDocument();
    open();
    expect(screen.getByPlaceholderText('搜索 Bot 名称或 Bot ID')).toHaveFocus();
    await screen.findByText('Bot 002');
    expect(screen.getAllByText('default')).toHaveLength(2);
    expect(screen.getByText('已暂停')).toBeInTheDocument(); expect(screen.getByText('未监控')).toBeInTheDocument();
    expect(container.querySelectorAll('.bot-card .bot-glyph')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: '加入监控' })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Bot 范围' })).not.toBeInTheDocument();
    const first = screen.getByRole('button', { name: /^Bot 001/ });
    fireEvent.keyDown(screen.getByLabelText('搜索 Bot'), { key: 'ArrowDown' }); expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(screen.getByRole('button', { name: /^Bot 002/ })).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('button', { name: /^监控 Bot/ })).toHaveFocus();
  });
  it('visibly distinguishes the same owner/default Bot in different environments', async () => {
    api.options.mockResolvedValue({ items: [bot('001'), { ...bot('001'), botRef: 'prod-ref', env: 'prod' }], nextCursor: null });
    render(<MonitoringBotSelector {...defaults} />); await waitFor(() => expect(defaults.onSelect).toHaveBeenCalled()); open();
    expect(await screen.findByLabelText('环境 test')).toBeInTheDocument();
    expect(screen.getByLabelText('环境 prod')).toBeInTheDocument();
  });
  it('does not automatically select another owner when an administrator with no own Bots changes scope', async () => {
    api.options.mockImplementation(scope => Promise.resolve({ items: scope === 'mine' ? [] : [bot('002')], nextCursor: null }));
    render(<MonitoringBotSelector {...defaults} isAdmin />);
    await screen.findByText('未找到可查看的 Bot。'); open();
    fireEvent.click(screen.getByRole('button', { name: '全部 Bot' }));
    await screen.findByText('Bot 002'); expect(defaults.onSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /^Bot 002/ }));
    expect(defaults.onSelect).toHaveBeenCalledWith(bot('002'));
  });
  it('debounces search, hides stale candidates and ignores an aborted late response', async () => {
    vi.useFakeTimers(); render(<MonitoringBotSelector {...defaults} isAdmin />); await act(async () => {}); open(); await act(async () => {});
    let finish!: (page: unknown) => void;
    api.options.mockImplementation((_scope, q) => q === 'old' ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({ items: [bot(q)], nextCursor: null }));
    fireEvent.change(screen.getByLabelText('搜索 Bot'), { target: { value: 'old' } });
    expect(screen.queryByText('Bot 001')).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    const signal = api.options.mock.lastCall?.[4];
    fireEvent.change(screen.getByLabelText('搜索 Bot'), { target: { value: 'new' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(signal.aborted).toBe(true);
    await act(async () => finish({ items: [bot('old')], nextCursor: null }));
    expect(screen.getByText('Bot new')).toBeInTheDocument(); expect(screen.queryByText('Bot old')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('搜索 Bot 名称、Bot ID 或工号')).toBeInTheDocument();
  });
  it('waits for Chinese IME composition to finish before searching', async () => {
    vi.useFakeTimers(); render(<MonitoringBotSelector {...defaults} />); await act(async () => {}); open(); await act(async () => {});
    api.options.mockClear();
    const input = screen.getByLabelText('搜索 Bot');
    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: '助' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(api.options).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: '助手' } });
    fireEvent.compositionEnd(input);
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(api.options).toHaveBeenCalledTimes(1);
    expect(api.options.mock.lastCall?.[1]).toBe('助手');
  });
  it('explicitly loads more, resets cursors on scope/date changes and clears candidates on permission loss', async () => {
    api.options.mockImplementation((_scope, _q, _dates, cursor) => Promise.resolve({ items: [bot(cursor ? '002' : '001')], nextCursor: cursor ? null : 'cursor-1' }));
    const mounted = render(<MonitoringBotSelector {...defaults} isAdmin />); await waitFor(() => expect(defaults.onSelect).toHaveBeenCalled()); open();
    fireEvent.click(await screen.findByRole('button', { name: '加载更多 Bot' }));
    await screen.findByText('Bot 002'); expect(screen.getByText('Bot 001')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '全部 Bot' }));
    await waitFor(() => expect(api.options.mock.lastCall?.slice(0,4)).toEqual(['all', '', { startDate: defaults.startDate, endDate: defaults.endDate }, null]));
    fireEvent.click(await screen.findByRole('button', { name: '加载更多 Bot' })); await screen.findByText('Bot 002');
    mounted.rerender(<MonitoringBotSelector {...defaults} isAdmin endDate="2026-09-11" />);
    await waitFor(() => expect(api.options.mock.lastCall?.[3]).toBeNull());
    api.options.mockRejectedValue({ status: 403 });
    fireEvent.click(await screen.findByRole('button', { name: '加载更多 Bot' }));
    await waitFor(() => expect(defaults.onUnavailable).toHaveBeenCalledWith({ status: 403 }));
    expect(screen.queryByText('Bot 001')).not.toBeInTheDocument();
  });
});
describe('enrollment placeholder', () => {
  it('shows user-facing notice only for the documented 501, and returns focus on dismissal', async () => {
    const refresh = vi.fn();
    api.enroll.mockRejectedValue({ status: 501, body: JSON.stringify({ error: { code: 'MONITORING_ENROLLMENT_NOT_IMPLEMENTED' } }) });
    render(<MonitoringEnrollment bot={bot('001')} onRefresh={refresh} />);
    fireEvent.click(screen.getByRole('button', { name: '加入监控' }));
    await screen.findByRole('dialog');
    expect(screen.getByText('加入监控功能正在开发中，敬请期待。')).toBeInTheDocument();
    expect(screen.queryByText(/接口|写入|切换 Bot/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '知道了' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '加入监控' })).toHaveFocus(); expect(refresh).not.toHaveBeenCalled();
    expect(api.enroll.mock.lastCall?.[0]).toBe('ref-001');
  });
  it.each(['resolve', 'reject'] as const)('ignores a late %s after the selected panel unmounts', async outcome => {
    let resolve!: () => void, reject!: (reason: unknown) => void;
    api.enroll.mockImplementation(() => new Promise<void>((yes, no) => { resolve = yes; reject = no; }));
    const refresh = vi.fn();
    const mounted = render(<MonitoringEnrollment bot={bot('001')} onRefresh={refresh} />);
    fireEvent.click(screen.getByRole('button', { name: '加入监控' }));
    const signal = api.enroll.mock.lastCall?.[1];
    mounted.unmount(); expect(signal.aborted).toBe(true);
    await act(async () => outcome === 'resolve' ? resolve() : reject({ status: 409, body: JSON.stringify({ error: { code: 'MONITORING_ALREADY_ENROLLED' } }) }));
    expect(refresh).not.toHaveBeenCalled();
  });
  it.each([{ status: 503 }, { status: 501, body: '{}' }, new Error('offline')])('does not disguise a request failure as a coming-soon notice', async failure => {
    api.enroll.mockRejectedValue(failure); render(<MonitoringEnrollment bot={bot('001')} onRefresh={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '加入监控' }));
    await screen.findByRole('alert'); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

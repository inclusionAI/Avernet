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
    expect(screen.getByText('我的 Bot')).toBeInTheDocument();
    expect(screen.getByText('诊断 Bot')).toBeInTheDocument();
    expect(screen.queryByText('诊断范围')).not.toBeInTheDocument();
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
  it('keeps an explicitly loaded page when the initial unchanged-search debounce would expire', async () => {
    vi.useFakeTimers();
    api.options.mockImplementation((_scope, _q, _dates, cursor) => Promise.resolve({
      items: [bot(cursor ? '002' : '001')], nextCursor: cursor ? null : 'cursor-1',
    }));
    render(<MonitoringBotSelector {...defaults} isAdmin />);
    await act(async () => {});
    open(); await act(async () => {});
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '加载更多 Bot' })); });
    expect(screen.getByText('Bot 002')).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(screen.getByText('Bot 002')).toBeInTheDocument();
    expect(screen.getByText('Bot 001')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '加载更多 Bot' })).not.toBeInTheDocument();
  });
  it('explicitly loads more, resets cursors on scope/date changes and clears candidates on permission loss', async () => {
    const loadMore = async () => {
      const button = await screen.findByRole('button', { name: '加载更多 Bot' });
      await waitFor(() => expect(button).toBeEnabled());
      await act(async () => { fireEvent.click(button); });
    };
    let finishRefresh!: () => void;
    const firstPage = { items: [bot('001')], nextCursor: 'cursor-1' };
    api.options.mockImplementation((_scope, _q, _dates, cursor) => Promise.resolve({ items: [bot(cursor ? '002' : '001')], nextCursor: cursor ? null : 'cursor-1' }));
    api.options.mockResolvedValueOnce(firstPage).mockImplementationOnce(() => new Promise(resolve => {
      finishRefresh = () => resolve(firstPage);
    }));
    const mounted = render(<MonitoringBotSelector {...defaults} isAdmin />); await waitFor(() => expect(defaults.onSelect).toHaveBeenCalled()); open();
    expect(screen.getByRole('button', { name: '加载更多 Bot' })).toBeDisabled();
    const pendingLoad = loadMore();
    await act(async () => {});
    await act(async () => finishRefresh());
    await pendingLoad;
    await screen.findByText('Bot 002'); expect(screen.getByText('Bot 001')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '全部 Bot' }));
    await waitFor(() => expect(api.options.mock.lastCall?.slice(0,4)).toEqual(['all', '', { startDate: defaults.startDate, endDate: defaults.endDate }, null]));
    await loadMore(); await screen.findByText('Bot 002');
    mounted.rerender(<MonitoringBotSelector {...defaults} isAdmin endDate="2026-09-11" />);
    await waitFor(() => expect(api.options.mock.lastCall?.[3]).toBeNull());
    api.options.mockRejectedValue({ status: 403 });
    await loadMore();
    await waitFor(() => expect(defaults.onUnavailable).toHaveBeenCalledWith({ status: 403 }));
    expect(screen.queryByText('Bot 001')).not.toBeInTheDocument();
  });
});
describe('unmonitored Bot contact information', () => {
  it.each([true, false])('is display-only when canRequestEnrollment=%s', canRequestEnrollment => {
    render(<MonitoringEnrollment bot={{ ...bot('001'), capabilities: { canView: true, canRequestEnrollment } }} />);
    expect(screen.getByRole('region', { name: '未监控 Bot' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '此 Bot 尚未纳入监控范围。' })).toBeInTheDocument();
    expect(screen.getByText('如有需要请联系@沐荛添加监控 Bot。')).toBeInTheDocument();
    expect(screen.getByText('Bot 001 · default · 001')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByText(/正在开发|暂不支持|敬请期待|加入监控后/)).not.toBeInTheDocument();
    expect(api.enroll).not.toHaveBeenCalled();
  });
  it('updates identity without enrollment requests or timers, including after unmount', async () => {
    vi.useFakeTimers();
    const mounted = render(<MonitoringEnrollment bot={bot('001')} />);
    mounted.rerender(<MonitoringEnrollment bot={bot('002')} />);
    expect(screen.queryByText('Bot 001 · default · 001')).not.toBeInTheDocument();
    expect(screen.getByText('Bot 002 · default · 002')).toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
    mounted.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(api.enroll).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});

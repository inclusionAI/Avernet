import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MonitoringPanel from '../MonitoringPanel';
import { beijingDateRange } from '../MonitoringControls';
import type { BotOption, DiagnosisItem, DiagnosisPage } from '../../../../types/monitoring';

const api = vi.hoisted(() => ({ options: vi.fn(), targetStatus: vi.fn(), targetDiagnoses: vi.fn(), enroll: vi.fn() }));
vi.mock('../../../../api/monitoring', async importOriginal => ({ ...await importOriginal<object>(), monitoringApi: api }));
const option = (botId: string): BotOption => ({ botRef: botId, botId, botName: botId, ownerId: '001', env: 'test',
  enrollmentState: 'ENROLLED', capabilities: { canView: true, canRequestEnrollment: false },
  monitoring: { status: 'HEALTHY', checkedAt: '2026-09-09T09:00:00Z', lastSuccessfulCheckAt: '2026-09-09T09:00:00Z',
    diagnosisCount: 30, alertCount: 10, diagnosedSessionCount: 20, unidentifiedSessionDiagnosisCount: 0 } });
const item: DiagnosisItem = {
  diagnosisId: 'diag-1', botId: 'bot-te', decision: 'ALERT', occurredAt: '2026-09-09T09:00:00Z', diagnosedAt: '2026-09-09T09:01:00Z',
  sessionKey: null, sessionId: 'session-1', traceId: 'trace-1', tcFaultLabel: 'TC.MCP.DATA', confidence: .86,
  businessProblemCategory: '外部服务异常', businessProblemSubtype: '数据获取失败',
  systemDiagnosis: '<script>alert(1)</script>', businessDiagnosis: '未获取到结果', handlerName: null, humanIntervention: true,
};
const page = (botId = 'bot-te'): DiagnosisPage => ({ botId, page: 1, pageSize: 20, total: 30, totalPages: 2,
  problemTypes: [{ category: '外部服务异常', subtypes: ['数据获取失败', '请求超时'] }, { category: '任务执行异常', subtypes: ['执行路径缺失'] }],
  counts: { all: 30, alert: 10, pass: 10, unresolved: 10 }, items: [{ ...item, botId }] });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  api.options.mockResolvedValue({ items: [option('bot-te'), option('bot-oc')], nextCursor: null });
  api.targetStatus.mockImplementation(async botId => option(botId));
  api.targetDiagnoses.mockImplementation(async (botId, query) => ({ ...page(botId), page: query.page, pageSize: query.pageSize }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('MonitoringPanel', () => {
  it('preserves the full TC label text and tooltip when CSS truncates the summary', async () => {
    const label = 'TC.TOOL.EXTERNAL_SERVICE.RESPONSE_TIMEOUT_WITH_EXTENDED_LABEL';
    api.targetDiagnoses.mockResolvedValue({ ...page(), items: [{ ...item, tcFaultLabel: label }] });
    render(<MonitoringPanel />);
    const text = await screen.findByText(label);
    expect(text).toHaveClass('summary-tc');
    expect(text).toHaveAttribute('title', label);
    expect(text.closest('button')).toHaveAttribute('aria-expanded', 'false');
    expect(text.parentElement?.querySelector('.record-time')).toHaveTextContent('2026-09-09');
  });

  it('applies type filters atomically, resets page, cancels drafts and resets filters', async () => {
    render(<MonitoringPanel />);
    await screen.findByText('监控正常');
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1].page).toBe(2));
    await screen.findByRole('button', { name: /外部服务异常/ });
    fireEvent.click(screen.getByRole('button', { name: '筛选问题类型' }));
    expect(screen.getByLabelText('业务问题子类型')).toBeDisabled();
    const calls = api.targetDiagnoses.mock.calls.length;
    fireEvent.change(screen.getByLabelText('业务问题类型'), { target: { value: '外部服务异常' } });
    fireEvent.change(screen.getByLabelText('业务问题子类型'), { target: { value: '数据获取失败' } });
    expect(api.targetDiagnoses).toHaveBeenCalledTimes(calls);
    fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ businessProblemCategory: '外部服务异常', businessProblemSubtype: '数据获取失败', page: 1 }));
    const trigger = screen.getByRole('button', { name: /筛选问题类型/ });
    expect(trigger).toHaveTextContent('2');
    fireEvent.click(trigger);
    fireEvent.change(screen.getByLabelText('业务问题类型'), { target: { value: '任务执行异常' } });
    expect(screen.getByLabelText('业务问题子类型')).toHaveValue('');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(trigger).toHaveFocus();
    fireEvent.click(trigger);
    expect(screen.getByLabelText('业务问题类型')).toHaveValue('外部服务异常');
    fireEvent.click(screen.getByRole('button', { name: '重置' }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ businessProblemCategory: '', businessProblemSubtype: '', page: 1, ...beijingDateRange(1) }));
  });

  it("initializes each mount with today's Beijing calendar day and 20 rows, including across midnight", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-31T15:59:59Z'));
    const first = render(<MonitoringPanel />);
    await act(async () => {});
    expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ startDate: '2026-08-31', endDate: '2026-08-31', page: 1, pageSize: 20 });
    expect(screen.getByLabelText('每页条数')).toHaveValue('20');
    first.unmount();
    vi.setSystemTime(new Date('2026-08-31T16:00:01Z'));
    render(<MonitoringPanel />);
    await act(async () => {});
    expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ startDate: '2026-09-01', endDate: '2026-09-01', pageSize: 20 });
    fireEvent.click(screen.getByRole('button', { name: '选择会话时间范围' }));
    fireEvent.click(screen.getByRole('button', { name: '全部时间' }));
    await act(async () => {});
    expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ startDate: '', endDate: '', pageSize: 20 });
  });
  it('loads discovered IDs, expands diagnosis safely, and omits unsupported actions', async () => {
    const { container } = render(<MonitoringPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /外部服务异常/ }));
    expect(screen.getByText('是否人工干预').nextElementSibling).toHaveTextContent('是');
    expect(screen.getByText('诊断置信度').nextElementSibling).toHaveTextContent('86.0%');
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('监控正常')).toBeInTheDocument();
    expect(screen.queryByText(/通知状态|添加 Bot|数据源|已通知钉钉/)).not.toBeInTheDocument();
  });
  it('paginates and resets page on filter, size, or bot changes', async () => {
    render(<MonitoringPanel />);
    await screen.findByRole('button', { name: /外部服务异常/ });
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1].page).toBe(2));
    await screen.findByRole('button', { name: '告警 10' });
    fireEvent.click(screen.getByRole('button', { name: '告警 10' }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ decision: 'ALERT', page: 1 }));
    fireEvent.change(screen.getByLabelText('每页条数'), { target: { value: '20' } });
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1].pageSize).toBe(20));
    fireEvent.click(screen.getByRole('button', { name: '监控 Bot bot-te' }));
    fireEvent.click(await screen.findByRole('button', { name: /^bot-oc.*监控中/ }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[0]).toBe('bot-oc'));
    await act(async () => {});
  });
  it('applies dates atomically, rejects reversed drafts and cancels without requests', async () => {
    render(<MonitoringPanel />);
    await screen.findByText('监控正常');
    fireEvent.click(screen.getByRole('button', { name: '选择会话时间范围' }));
    const calls = api.targetDiagnoses.mock.calls.length;
    fireEvent.input(screen.getByLabelText('开始日期'), { target: { value: '2026-09-10' } });
    fireEvent.input(screen.getByLabelText('结束日期'), { target: { value: '2026-09-09' } });
    expect(screen.getByText(/开始日期不能晚于/)).toBeInTheDocument();
    expect(api.targetDiagnoses).toHaveBeenCalledTimes(calls);
    expect(screen.getByRole('button', { name: '应用' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(api.targetDiagnoses).toHaveBeenCalledTimes(calls);
    fireEvent.click(screen.getByRole('button', { name: '选择会话时间范围' }));
    expect(screen.getByLabelText('开始日期')).toHaveValue(beijingDateRange(1).startDate);
    fireEvent.input(screen.getByLabelText('开始日期'), { target: { value: '2026-09-01' } });
    fireEvent.input(screen.getByLabelText('结束日期'), { target: { value: '2026-09-10' } });
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ startDate: '2026-09-01', endDate: '2026-09-10', page: 1 }));
    expect(screen.queryByLabelText('开始日期')).not.toBeInTheDocument();
  });
  it('ignores late responses for the previous bot even if transport ignores abort', async () => {
    const old = deferred<DiagnosisPage>();
    api.targetDiagnoses.mockImplementation(botId => botId === 'bot-te' ? old.promise : Promise.resolve({ ...page(botId), items: [{ ...item, diagnosisId: 'oc', traceId: 'trace-new' }] }));
    render(<MonitoringPanel />);
    await screen.findByText('监控正常');
    fireEvent.click(screen.getByRole('button', { name: '监控 Bot bot-te' }));
    fireEvent.click(await screen.findByRole('button', { name: /^bot-oc.*监控中/ }));
    await screen.findByText('trace-new');
    await act(async () => old.resolve(page()));
    expect(screen.queryByText('trace-1')).not.toBeInTheDocument();
    expect(screen.getByText('trace-new')).toBeInTheDocument();
  });
  it('retains expanded records on refresh failure and marks status as not updated', async () => {
    render(<MonitoringPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /外部服务异常/ }));
    api.targetStatus.mockRejectedValue({ status: 503 }); api.targetDiagnoses.mockRejectedValue({ status: 503 });
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await screen.findByText('状态未更新');
    expect(screen.queryByText('监控正常')).not.toBeInTheDocument();
    expect(screen.getByText(/当前保留上次结果/)).toBeInTheDocument();
    expect(screen.getByText('是否人工干预')).toBeInTheDocument();
    expect(screen.queryByText('没有符合当前条件的诊断记录。')).not.toBeInTheDocument();
  });
  it('distinguishes an empty bot list from service failure', async () => {
    api.options.mockRejectedValueOnce({ status: 503 });
    render(<MonitoringPanel />);
    await screen.findByText('监控服务暂不可用，请稍后重试。');
    expect(screen.queryByText('未找到可查看的 Bot。')).not.toBeInTheDocument();
    api.options.mockResolvedValue({ items: [] });
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await screen.findByText('未找到可查看的 Bot。');
    expect(api.targetDiagnoses).not.toHaveBeenCalled();
  });
  it('discovers bots when opening and on manual refresh without losing selection', async () => {
    render(<MonitoringPanel />);
    await screen.findByText('监控正常');
    api.options.mockResolvedValue({ items: [option('bot-new'), option('bot-te')], nextCursor: null });
    fireEvent.click(screen.getByRole('button', { name: '监控 Bot bot-te' }));
    fireEvent.click(await screen.findByRole('button', { name: /^bot-new.*监控中/ }));
    await waitFor(() => expect(api.targetStatus.mock.lastCall?.[0]).toBe('bot-new'));
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(api.options).toHaveBeenCalledTimes(3));
    expect(screen.getByRole('button', { name: '监控 Bot bot-new' })).toBeInTheDocument();
  });
  it('discovers the first bot when an initially empty list is refreshed', async () => {
    api.options.mockResolvedValueOnce({ items: [], nextCursor: null });
    render(<MonitoringPanel />);
    await screen.findByText('未找到可查看的 Bot。');
    expect(api.targetStatus).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(api.targetStatus.mock.lastCall?.[0]).toBe('bot-te'));
  });
  it('polls only while visible and stops after unmount; keyword is debounced', async () => {
    vi.useFakeTimers();
    const mounted = render(<MonitoringPanel />);
    await act(async () => {});
    expect(api.targetDiagnoses).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(api.targetDiagnoses).toHaveBeenCalledTimes(2);
    fireEvent.change(screen.getByLabelText('搜索诊断记录'), { target: { value: ' trace ' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(api.targetDiagnoses.mock.lastCall?.[1].keyword).toBe('trace');
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    fireEvent(document, new Event('visibilitychange'));
    const n = api.targetDiagnoses.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(api.targetDiagnoses).toHaveBeenCalledTimes(n);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await act(async () => fireEvent(document, new Event('visibilitychange')));
    expect(api.targetDiagnoses).toHaveBeenCalledTimes(n + 1);
    mounted.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(api.targetDiagnoses).toHaveBeenCalledTimes(n + 1);
  });
  it('uses Beijing calendar dates for presets across a UTC day and month boundary', () => {
    const now = new Date('2026-08-31T17:00:00Z');
    expect(beijingDateRange(1, now)).toEqual({ startDate: '2026-09-01', endDate: '2026-09-01' });
    expect(beijingDateRange(7, now)).toEqual({ startDate: '2026-08-26', endDate: '2026-09-01' });
  });
  it('supports quick ranges and clearing an empty filtered result', async () => {
    api.targetDiagnoses.mockResolvedValue({ ...page(), items: [], total: 0, totalPages: 0 });
    render(<MonitoringPanel />);
    await screen.findByText('没有符合当前条件的诊断记录。');
    fireEvent.click(screen.getByRole('button', { name: '选择会话时间范围' }));
    fireEvent.click(screen.getByRole('button', { name: '近 7 天' }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject(beijingDateRange(7)));
    fireEvent.click(await screen.findByRole('button', { name: '清空筛选条件' }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ ...beijingDateRange(1), keyword: '', decision: 'ALL', page: 1 }));
  });
  it('closes menus with Escape and restores trigger focus', async () => {
    render(<MonitoringPanel />);
    await screen.findByText('监控正常');
    const button = screen.getByRole('button', { name: '监控 Bot bot-te' });
    fireEvent.click(button);
    expect(await screen.findByRole('button', { name: /^bot-oc.*监控中/ })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('button', { name: /^bot-oc.*监控中/ })).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });
  it('keeps the collapsed row concise and handles clipboard failure explicitly', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
    render(<MonitoringPanel />);
    const record = await screen.findByRole('button', { name: /外部服务异常/ });
    expect(record).toHaveTextContent('TC.MCP.DATA');
    expect(record).toHaveTextContent('2026-09-09');
    expect(record).not.toHaveTextContent('人工干预：是');
    expect(record).not.toHaveTextContent('告警');
    fireEvent.click(record);
    fireEvent.click(screen.getByRole('button', { name: '复制Trace ID' }));
    await screen.findByText('未能访问剪贴板，请选择并复制上方标识。');
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('trace-1');
  });

  it('jumps beyond five pages using server pagination and hides old rows while loading', async () => {
    const pending = deferred<DiagnosisPage>();
    api.targetDiagnoses.mockImplementation((botId, query) => query.page === 18 ? pending.promise : Promise.resolve({ ...page(botId), total: 400, totalPages: 20, page: query.page }));
    render(<MonitoringPanel />);
    await screen.findByText('1 / 20 页');
    fireEvent.change(screen.getByLabelText('跳转页码'), { target: { value: '18' } });
    expect(api.targetDiagnoses).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '跳转' }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ page: 18, pageSize: 20 }));
    expect(screen.queryByText('trace-1')).not.toBeInTheDocument();
    expect(screen.queryByText('18 / 20 页')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '末页' })).toBeDisabled();
    await act(async () => pending.resolve({ ...page(), page: 18, total: 400, totalPages: 20 }));
    expect(screen.getByText('18 / 20 页')).toBeInTheDocument();
    expect(screen.getByText('第 341–341 条，共 400 条')).toBeInTheDocument();
  });
  it('corrects a shrinking dataset at most once, including across later polling', async () => {
    vi.useFakeTimers();
    api.targetDiagnoses.mockImplementation(async (botId, query) => ({ ...page(botId), page: query.page, total: 400, totalPages: 20 }));
    render(<MonitoringPanel />); await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: '末页' })); await act(async () => {});
    expect(screen.getByText('20 / 20 页')).toBeInTheDocument();
    api.targetDiagnoses.mockImplementation(async (botId, query) => ({ ...page(botId), page: query.page, items: [], total: query.page === 20 ? 60 : 20, totalPages: query.page === 20 ? 3 : 1 }));
    api.targetDiagnoses.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(api.targetDiagnoses.mock.calls.map(call => call[1].page)).toEqual([20, 3]);
    expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(api.targetDiagnoses.mock.calls.map(call => call[1].page)).toEqual([20, 3, 3]);
    fireEvent.click(screen.getByRole('button', { name: '返回首页' })); await act(async () => {});
    expect(api.targetDiagnoses.mock.lastCall?.[1].page).toBe(1);
  });
  it('normalizes an emptied later page to internal page one and shows zero of zero', async () => {
    api.targetDiagnoses.mockImplementation(async (botId, query) => ({ ...page(botId), page: query.page }));
    render(<MonitoringPanel />); await screen.findByText('1 / 2 页');
    fireEvent.click(screen.getByRole('button', { name: '末页' })); await screen.findByText('2 / 2 页');
    api.targetDiagnoses.mockImplementation(async (botId, query) => ({ ...page(botId), page: query.page, items: [], total: 0, totalPages: 0 }));
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await screen.findByText('0 / 0 页');
    expect(api.targetDiagnoses.mock.calls.slice(-2).map(call => call[1].page)).toEqual([2, 1]);
    expect(screen.getByLabelText('跳转页码')).toBeDisabled();
  });
  it('retains current page, expanded rows, jump draft and focus during background polling', async () => {
    vi.useFakeTimers();
    api.targetDiagnoses.mockImplementation(async (botId, query) => ({ ...page(botId), page: query.page }));
    render(<MonitoringPanel />); await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: '末页' })); await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: /外部服务异常/ }));
    const input = screen.getByLabelText('跳转页码'); input.focus(); fireEvent.change(input, { target: { value: '1' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(api.targetDiagnoses.mock.lastCall?.[1].page).toBe(2);
    expect(screen.getByText('是否人工干预')).toBeInTheDocument();
    expect(input).toHaveFocus(); expect(input).toHaveValue('1');
  });
  it('resets to page one after size/target changes but preserves the chosen page size', async () => {
    render(<MonitoringPanel />); await screen.findByText('1 / 2 页');
    fireEvent.click(screen.getByRole('button', { name: '末页' })); await screen.findByText('2 / 2 页');
    fireEvent.change(screen.getByLabelText('每页条数'), { target: { value: '50' } });
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ page: 1, pageSize: 50 }));
    fireEvent.click(screen.getByRole('button', { name: '监控 Bot bot-te' }));
    fireEvent.click(await screen.findByRole('button', { name: /^bot-oc.*监控中/ }));
    await waitFor(() => expect(api.targetDiagnoses.mock.lastCall?.[0]).toBe('bot-oc'));
    expect(api.targetDiagnoses.mock.lastCall?.[1]).toMatchObject({ page: 1, pageSize: 50 });
  });
  it('does not display zero results or the previous page when a new page request fails', async () => {
    render(<MonitoringPanel />); await screen.findByText('1 / 2 页');
    api.targetDiagnoses.mockRejectedValue({ status: 503 });
    fireEvent.click(screen.getByRole('button', { name: '末页' }));
    await screen.findByText(/监控服务暂不可用/);
    expect(screen.queryByText('trace-1')).not.toBeInTheDocument();
    expect(screen.queryByText('共 0 条')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('clears selected identity, expanded records and summaries immediately after a denied selector refresh', async () => {
    render(<MonitoringPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /外部服务异常/ }));
    api.options.mockRejectedValue({ status: 401 });
    fireEvent.click(screen.getByRole('button', { name: '监控 Bot bot-te' }));
    await waitFor(() => expect(screen.queryByText('是否人工干预')).not.toBeInTheDocument());
    expect(screen.queryByText('trace-1')).not.toBeInTheDocument();
    expect(screen.queryByText('监控正常')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '监控 Bot bot-te' })).not.toBeInTheDocument();
    expect(screen.getAllByText('登录状态已失效，请重新登录后刷新。').length).toBeGreaterThan(0);
  });
  it('never requests diagnosis history for an unmonitored directory Bot', async () => {
    api.targetStatus.mockResolvedValue({ ...option('bot-te'), enrollmentState: 'NOT_ENROLLED', monitoring: null });
    render(<MonitoringPanel />);
    await screen.findByRole('button', { name: '加入监控' });
    expect(api.targetDiagnoses).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('诊断记录分页')).not.toBeInTheDocument();
  });

  it('removes cached history if enrollment disappears between status and diagnosis requests', async () => {
    vi.useFakeTimers();
    render(<MonitoringPanel />); await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: /外部服务异常/ }));
    api.targetDiagnoses.mockRejectedValue({ status: 409, body: JSON.stringify({ error: { code: 'MONITORING_NOT_ENROLLED' } }) });
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByRole('button', { name: '加入监控' })).toBeInTheDocument();
    expect(screen.queryByText('是否人工干预')).not.toBeInTheDocument();
    expect(screen.queryByText('trace-1')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('诊断记录分页')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

});

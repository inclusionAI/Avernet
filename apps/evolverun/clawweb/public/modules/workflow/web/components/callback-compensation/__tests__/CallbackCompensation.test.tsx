import type { CallbackCompensationBatch } from '../../../models/callbackCompensationTypes';
import { callbackCompensationStorageKey } from '../../../models/callbackCompensation';
import { useCallbackCompensation } from '../../../hooks/callback-compensation/useCallbackCompensation';
import { CallbackCompensation } from '../CallbackCompensation';
import { callbackCompensationService } from '../../../api/callback-compensation';
import { vi, it, expect, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import {
  callbackCompensationContext as baseContext,
  callbackCompensationBatch as batch,
  callbackCompensationCapabilities as capabilities,
  callbackCompensationDeferred as deferred,
  callbackCompensationPreview as preview,
} from './fixtures';

const identity = vi.hoisted(() => ({ value: { actorId: 'alice', scopeKey: 'space-a', generation: 1 } as { actorId: string; scopeKey: string; generation: number } | null }));
vi.mock('../../../hooks/callback-compensation/useCallbackCompensationIdentity', () => ({
  useCallbackCompensationIdentity: () => identity.value,
}));
vi.mock('../../../api/callback-compensation', () => ({
  callbackCompensationService: {
    capabilities: vi.fn(),
    preview: vi.fn(),
    create: vi.fn(),
    batch: vi.fn(),
    stop: vi.fn(),
  },
}));
const service = callbackCompensationService as import('vitest').Mocked<typeof callbackCompensationService>;
let serial = 0;
let context = baseContext;
beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  context = { ...baseContext, scopeKey: `space-${++serial}` };
  identity.value = { actorId: context.userId, scopeKey: context.scopeKey, generation: serial };
  service.capabilities.mockResolvedValue(capabilities);
  service.preview.mockResolvedValue(preview);
  service.create.mockResolvedValue(batch);
  service.batch.mockResolvedValue(batch);
  service.stop.mockResolvedValue({
    ...batch,
    status: 'stopped',
    items: [{ ...batch.items[0], state: 'skipped' }],
    counts: { skipped: 1 },
  });
});
afterEach(() => vi.useRealTimers());
async function open() {
  fireEvent.click(await screen.findByRole('button', { name: '成功回调补偿' }));
  return screen.findByRole('dialog', { name: '成功回调补偿' });
}
async function scan() {
  await open();
  fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
  await screen.findByText('run-eligible');
}
async function confirm() {
  await scan();
  fireEvent.click(screen.getByRole('checkbox', { name: '选择 run-eligible' }));
  fireEvent.click(screen.getByRole('button', { name: '下一步：确认补偿' }));
  fireEvent.change(screen.getByRole('textbox', { name: '补偿原因' }), {
    target: { value: '人工核实后只补发通知消息' },
  });
  fireEvent.click(screen.getByRole('checkbox', { name: /只补发通知/ }));
}

it('checks real capabilities before exposing the entry; 403 hides details, 503 is unavailable, no selected workflow makes no request', async () => {
  service.capabilities.mockRejectedValueOnce(Object.assign(new Error('do-not-leak-secret'), { status: 403 }));
  const view = render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await waitFor(() => expect(service.capabilities).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument();
  expect(screen.queryByText(/do-not-leak-secret/)).not.toBeInTheDocument();
  service.capabilities.mockRejectedValueOnce(Object.assign(new Error('do-not-leak-secret'), { status: 503 }));
  view.rerender(<CallbackCompensation workflowId="other" />);
  expect(await screen.findByText('补偿服务不可用')).toBeInTheDocument();
  expect(screen.queryByText(/do-not-leak-secret/)).not.toBeInTheDocument();
  view.rerender(<CallbackCompensation workflowId="" />);
  expect(screen.queryByText('补偿服务不可用')).not.toBeInTheDocument();
  expect(service.capabilities).toHaveBeenCalledTimes(2);
});

it('defaults to an absolute local 24h range with explicit timezone; displays only sanitized server preview and eligible selection', async () => {
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await scan();
  expect(service.preview).toHaveBeenCalledWith(
    { workflowId: 'wf/a', environment: 'PRE' },
    expect.objectContaining({ configId: 'cfg', from: expect.any(String), to: expect.any(String) }),
    expect.any(AbortSignal),
  );
  const range = service.preview.mock.calls[0][1];
  expect(Date.parse(range.to!) - Date.parse(range.from!)).toBe(86400000);
  expect(screen.getByText(/时区：/)).toBeInTheDocument();
  expect(screen.getByText(/绝对范围：/)).toBeInTheDocument();
  for (const id of ['delivered', 'review', 'ineligible'])
    expect(screen.getByRole('checkbox', { name: `选择 run-${id}` })).toBeDisabled();
  expect(screen.getByRole('button', { name: '下一步：确认补偿' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: '选择 run-eligible' }));
  expect(screen.getByRole('button', { name: '下一步：确认补偿' })).toBeEnabled();
  fireEvent.click(screen.getByText('查看脱敏 payload', { selector: 'summary' }));
  expect(screen.getByText(/"redacted": true/)).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: /URL|header|secret/i })).not.toBeInTheDocument();
});

it('clears preview when changing range, validates 100 run IDs and rejects duplicate in-flight previews', async () => {
  const pending = deferred<typeof preview>();
  service.preview.mockReturnValueOnce(pending.promise);
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await open();
  fireEvent.click(screen.getByRole('button', { name: '按运行 ID' }));
  fireEvent.change(screen.getByRole('textbox', { name: '运行 IDs（可选）' }), {
    target: { value: Array.from({ length: 101 }, (_, i) => `r${i}`).join('\n') },
  });
  fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('100');
  expect(service.preview).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('textbox', { name: '运行 IDs（可选）' }), { target: { value: 'r1,r2\nr1' } });
  const button = screen.getByRole('button', { name: '生成预览' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(service.preview).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(preview));
  await screen.findByText('run-eligible');
  fireEvent.click(screen.getByRole('button', { name: '修改范围' }));
  expect(screen.queryByText('run-eligible')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '运行 IDs（可选）' })).toHaveValue('r1,r2\nr1');
  expect(service.preview.mock.calls[0][1].flowIds).toEqual(['r1', 'r2']);
});

it('allows preview but cannot select or execute while server execution/idempotency prerequisites are blocked', async () => {
  service.capabilities.mockResolvedValue({
    ...capabilities,
    executionEnabled: false,
    blockedReasons: ['TRUSTED_EVIDENCE_PROVIDER_MISSING'],
  });
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await scan();
  expect(screen.getByText('未配置可信幂等证据提供方')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '选择 run-eligible' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '下一步：确认补偿' })).toBeDisabled();
  expect(service.create).not.toHaveBeenCalled();
});

it('requires a second explicit confirmation of environment/workflow/receiver/count, valid reason and notice-only acknowledgement', async () => {
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await scan();
  fireEvent.click(screen.getByRole('checkbox', { name: '选择 run-eligible' }));
  fireEvent.click(screen.getByRole('button', { name: '下一步：确认补偿' }));
  const summary = screen.getByRole('region', { name: '二次确认摘要' });
  for (const value of ['PRE', 'wf/a', '通知接收者', 'https://receiver.example', '1'])
    expect(within(summary).getByText(value)).toBeInTheDocument();
  const execute = screen.getByRole('button', { name: '确认创建补偿批次' });
  expect(execute).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox', { name: '补偿原因' }), { target: { value: '过短' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /只补发通知/ }));
  expect(execute).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox', { name: '补偿原因' }), { target: { value: 'a'.repeat(501) } });
  expect(execute).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox', { name: '补偿原因' }), {
    target: { value: '人工核实后只补发通知消息' },
  });
  fireEvent.click(execute);
  fireEvent.click(execute);
  await screen.findByText('batch/a');
  expect(service.create).toHaveBeenCalledTimes(1);
  expect(service.create.mock.calls[0][1]).toEqual(
    expect.objectContaining({
      previewId: 'preview',
      itemIds: ['eligible'],
      reason: '人工核实后只补发通知消息',
      requestKey: expect.any(String),
    }),
  );
  expect(Object.values(sessionStorage)).toEqual(['batch/a']);
});

it('preserves unknown create outcome and frozen requestKey/body through close/reopen and retry, without re-previewing', async () => {
  service.create.mockRejectedValueOnce(new TypeError('timeout-sensitive-detail'));
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await confirm();
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  await screen.findByText(/创建结果尚未确认/);
  expect(screen.queryByText(/timeout-sensitive-detail/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '生成预览' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新建补偿' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '关闭补偿窗口' }));
  await open();
  fireEvent.click(screen.getByRole('button', { name: '使用同一请求重试' }));
  await screen.findByText('batch/a');
  expect(service.create).toHaveBeenCalledTimes(2);
  expect(service.create.mock.calls[1][1]).toEqual(service.create.mock.calls[0][1]);
  expect(service.preview).toHaveBeenCalledTimes(1);
});

it('reopens/restores only a scoped saved batch after real capability and batch authorization, never posts another create', async () => {
  const key = callbackCompensationStorageKey({ ...context, environment: 'PRE' });
  sessionStorage.setItem(key, 'batch/a');
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await open();
  await screen.findByText('batch/a');
  expect(service.batch).toHaveBeenCalledWith(
    { workflowId: 'wf/a', environment: 'PRE' },
    'batch/a',
    expect.any(AbortSignal),
  );
  fireEvent.click(screen.getByRole('button', { name: '关闭补偿窗口' }));
  await open();
  await screen.findByText('batch/a');
  expect(service.create).not.toHaveBeenCalled();
  expect(service.preview).not.toHaveBeenCalled();
  expect(Object.keys(sessionStorage)).toEqual([key]);
});

it.each(['workflowId', 'userId', 'scopeKey'] as const)(
  'aborts and ignores a late preview on %s switch and clears dialog/evidence',
  async (field) => {
    const late = deferred<typeof preview>();
    service.preview.mockReturnValueOnce(late.promise);
    const view = render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
    await open();
    fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
    const signal = service.preview.mock.calls[0][2]!;
    const changed = { ...context, [field]: 'other' };
    identity.value = { actorId: changed.userId, scopeKey: changed.scopeKey, generation: serial + 1 };
    service.capabilities.mockResolvedValue({ ...capabilities, workflowId: changed.workflowId });
    view.rerender(<CallbackCompensation workflowId={changed.workflowId} />);
    expect(signal.aborted).toBe(true);
    await act(async () => late.resolve(preview));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await open();
    expect(screen.queryByText('run-eligible')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成预览' })).toBeInTheDocument();
  },
);

it('clears current state when the native identity generation changes even for the same actor', async () => {
  const view = render(<CallbackCompensation workflowId={context.workflowId} />);
  await scan();
  identity.value = { ...identity.value!, generation: serial + 1 };
  view.rerender(<CallbackCompensation workflowId={context.workflowId} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await open();
  expect(screen.queryByText('run-eligible')).not.toBeInTheDocument();
});

it('polls and stops only queued work; received means HTTP receipt, unknown has no resend action', async () => {
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await confirm();
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  await screen.findByText('batch/a');
  fireEvent.click(screen.getByRole('button', { name: '停止未发送项' }));
  await waitFor(() => expect(service.stop).toHaveBeenCalledTimes(1));
  await screen.findByText('已停止');
  expect(screen.getByRole('button', { name: '停止未发送项' })).toBeDisabled();
});

it('polls actual terminal results and exposes audit pending without claiming business success or allowing unknown resends', async () => {
  const completed = {
    ...batch,
    status: 'completed' as const,
    items: [
      { ...batch.items[0], state: 'received' as const, httpStatus: 202, auditPending: true },
      {
        itemId: 'uncertain',
        flowId: 'run-unknown',
        state: 'unknown' as const,
        reason: '网络结果不确定',
        auditPending: false,
      },
    ],
    counts: { received: 1, unknown: 1 },
  };
  service.batch.mockResolvedValue(completed);
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await confirm();
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  await screen.findByText('batch/a');
  fireEvent.click(screen.getByRole('button', { name: '刷新批次' }));
  expect(await screen.findByText('HTTP 已接收')).toBeInTheDocument();
  expect(screen.getByText('审计待落库')).toBeInTheDocument();
  expect(screen.getByText('结果未知（不可重发）')).toBeInTheDocument();
  expect(screen.getByText(/不代表业务成功/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /重新发送|重发 unknown/ })).not.toBeInTheDocument();
});

it('polls after the interval and stops polling a settled batch', async () => {
  const settled = {
    ...batch,
    status: 'completed' as const,
    items: [{ ...batch.items[0], state: 'received' as const, httpStatus: 200 }],
    counts: { received: 1 },
  };
  service.batch.mockResolvedValue(settled);
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await confirm();
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.getByText('batch/a')).toBeInTheDocument();
  expect(service.batch).not.toHaveBeenCalled();
  await act(async () => {
    vi.advanceTimersByTime(2500);
  });
  expect(service.batch).toHaveBeenCalledTimes(1);
  expect(screen.getByText('HTTP 已接收')).toBeInTheDocument();
  await act(async () => {
    vi.advanceTimersByTime(15000);
  });
  expect(service.batch).toHaveBeenCalledTimes(1);
});

it('expires a preview in confirmation without sending a create', async () => {
  service.preview.mockResolvedValue({ ...preview, expiresAt: new Date(Date.now() + 60000).toISOString() });
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await open();
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
  await act(async () => { await Promise.resolve(); });
  fireEvent.click(screen.getByRole('checkbox', { name: '选择 run-eligible' }));
  fireEvent.click(screen.getByRole('button', { name: '下一步：确认补偿' }));
  fireEvent.change(screen.getByRole('textbox', { name: '补偿原因' }), { target: { value: '人工核实后只补发通知消息' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /只补发通知/ }));
  expect(screen.getByRole('button', { name: '确认创建补偿批次' })).toBeEnabled();
  await act(async () => {
    vi.advanceTimersByTime(61000);
  });
  expect(screen.getByRole('button', { name: '确认创建补偿批次' })).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('预览已过期');
  expect(service.create).not.toHaveBeenCalled();
});

it('rejects a saved batch on 403 without revealing backend details or relying on sessionStorage as authorization', async () => {
  sessionStorage.setItem(callbackCompensationStorageKey({ ...context, environment: 'PRE' }), 'batch/a');
  service.batch.mockRejectedValueOnce(Object.assign(new Error('do-not-leak-batch-secret'), { status: 403 }));
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await open();
  await waitFor(() => expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument());
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText(/do-not-leak-batch-secret/)).not.toBeInTheDocument();
  expect(service.create).not.toHaveBeenCalled();
});

it('aborts a late capabilities response and never reveals the old receiver to a new user', async () => {
  const late = deferred<typeof capabilities>();
  service.capabilities
    .mockReturnValueOnce(late.promise)
    .mockRejectedValueOnce(Object.assign(new Error('forbidden'), { status: 403 }));
  const view = render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  const signal = service.capabilities.mock.calls[0][1]!;
  identity.value = { actorId: 'other', scopeKey: context.scopeKey, generation: serial + 1 };
  view.rerender(<CallbackCompensation workflowId={context.workflowId} />);
  expect(signal.aborted).toBe(true);
  await act(async () => late.resolve(capabilities));
  expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument();
  expect(screen.queryByText('通知接收者')).not.toBeInTheDocument();
});

it('ignores a late create after switching workflow; switching back retries the same frozen request without requalification', async () => {
  const late = deferred<typeof batch>();
  service.create.mockReturnValueOnce(late.promise);
  const view = render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await confirm();
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  const input = service.create.mock.calls[0][1];
  const signal = service.create.mock.calls[0][2]!;
  service.capabilities.mockResolvedValueOnce({ ...capabilities, workflowId: 'other' });
  view.rerender(<CallbackCompensation workflowId="other" />);
  expect(signal.aborted).toBe(true);
  await act(async () => late.resolve(batch));
  expect(sessionStorage.length).toBe(0);
  await open();
  expect(screen.queryByText('batch/a')).not.toBeInTheDocument();
  expect(screen.queryByText('run-eligible')).not.toBeInTheDocument();
  view.rerender(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await open();
  expect(screen.getByText('创建结果尚未确认')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '使用同一请求重试' }));
  await screen.findByText('batch/a');
  expect(service.create.mock.calls[1][1]).toEqual(input);
  expect(service.preview).toHaveBeenCalledTimes(1);
});

it('does not let a stale poll undo stop or leak a batch after identity switch', async () => {
  const late = deferred<typeof batch>();
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await confirm();
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  await screen.findByText('batch/a');
  service.batch.mockReturnValueOnce(late.promise);
  fireEvent.click(screen.getByRole('button', { name: '刷新批次' }));
  const signal = service.batch.mock.calls[0][2]!;
  fireEvent.click(screen.getByRole('button', { name: '停止未发送项' }));
  expect(signal.aborted).toBe(true);
  await screen.findByText('已停止');
  await act(async () => late.resolve(batch));
  expect(screen.getByText('已停止')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '停止未发送项' })).toBeDisabled();
});

it('scans flow IDs only with no time range, and maps a service code without rendering its body', async () => {
  service.preview.mockRejectedValueOnce(
    Object.assign(new Error('secret=never'), { data: { code: 'INVALID_FLOW_SELECTION', message: 'secret=never' } }),
  );
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await open();
  fireEvent.click(screen.getByRole('button', { name: '按运行 ID' }));
  expect(screen.getByLabelText('开始时间')).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox', { name: '运行 IDs（可选）' }), { target: { value: 'r1,r2\nr1' } });
  fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
  await screen.findByText('请选择 1–100 个不重复的有效运行 ID。');
  expect(screen.queryByText('secret=never', { exact: false })).not.toBeInTheDocument();
  expect(service.preview.mock.calls[0][1]).toEqual({ configId: 'cfg', flowIds: ['r1', 'r2'] });
  fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
  await screen.findByText('run-eligible');
  expect(screen.getByText(/运行 ID 范围：2 个/)).toBeInTheDocument();
  expect(screen.queryByText(/绝对范围：/)).not.toBeInTheDocument();
});

it('clears sensitive dialog state when native identity becomes unavailable, without rechecking capabilities', async () => {
  const view = render(<CallbackCompensation workflowId={context.workflowId} />);
  await scan();
  const requests = service.capabilities.mock.calls.length;
  identity.value = null;
  view.rerender(<CallbackCompensation workflowId={context.workflowId} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument();
  expect(service.capabilities).toHaveBeenCalledTimes(requests);
});

it('blocks preview reentrancy before React commits the busy render', async () => {
  const pending = deferred<typeof preview>();
  service.preview.mockReturnValue(pending.promise);
  const { result } = renderHook(() => useCallbackCompensation(context, capabilities, vi.fn()));
  act(() => {
    result.current.generatePreview();
    result.current.generatePreview();
  });
  expect(service.preview).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(preview));
});

it('keeps polling already-sending items after a stop until their actual receipt/unknown result settles', async () => {
  const stopped = {
    ...batch,
    status: 'stopped' as const,
    items: [{ ...batch.items[0], state: 'sending' as const }],
    counts: { sending: 1 },
  };
  service.stop.mockResolvedValue(stopped);
  service.batch.mockResolvedValue({
    ...stopped,
    items: [{ ...batch.items[0], state: 'received' as const, httpStatus: 202 }],
    counts: { received: 1 },
  });
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await confirm();
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  await screen.findByText('batch/a');
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: '停止未发送项' }));
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.getByText('已停止')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '停止未发送项' })).toBeDisabled();
  await act(async () => {
    vi.advanceTimersByTime(2500);
  });
  expect(service.batch).toHaveBeenCalledTimes(1);
  expect(screen.getByText('HTTP 已接收')).toBeInTheDocument();
});

it('keeps completed A until an explicit new compensation, then previews and confirms B with a fresh request key', async () => {
  const completed = {
    ...batch,
    status: 'completed' as const,
    items: [{ ...batch.items[0], state: 'received' as const, httpStatus: 202 }],
    counts: { received: 1 },
  };
  service.create.mockResolvedValueOnce(completed).mockResolvedValueOnce({ ...batch, batchId: 'batch/b' });
  service.batch.mockResolvedValue(completed);
  const key = callbackCompensationStorageKey({ ...context, environment: 'PRE' });
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await confirm();
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  await screen.findByText('batch/a');
  expect(screen.getByRole('button', { name: '新建补偿' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '关闭补偿窗口' }));
  await open();
  expect(screen.getByText('batch/a')).toBeInTheDocument();
  expect(sessionStorage.getItem(key)).toBe('batch/a');
  fireEvent.click(screen.getByRole('button', { name: '新建补偿' }));
  expect(sessionStorage.getItem(key)).toBeNull();
  expect(screen.queryByText('batch/a')).not.toBeInTheDocument();
  expect(screen.queryByText('run-eligible')).not.toBeInTheDocument();
  expect(service.preview).toHaveBeenCalledTimes(1);
  expect(service.create).toHaveBeenCalledTimes(1);
  service.preview.mockResolvedValueOnce({ ...preview, previewId: 'preview-b' });
  fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
  await screen.findByText('run-eligible');
  expect(screen.getByRole('checkbox', { name: '选择 run-eligible' })).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: '选择 run-eligible' }));
  fireEvent.click(screen.getByRole('button', { name: '下一步：确认补偿' }));
  expect(screen.getByRole('textbox', { name: '补偿原因' })).toHaveValue('');
  expect(screen.getByRole('checkbox', { name: /只补发通知/ })).not.toBeChecked();
  fireEvent.change(screen.getByRole('textbox', { name: '补偿原因' }), {
    target: { value: '再次核实新的通知补偿范围' },
  });
  fireEvent.click(screen.getByRole('checkbox', { name: /只补发通知/ }));
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  await screen.findByText('batch/b');
  expect(service.preview).toHaveBeenCalledTimes(2);
  expect(service.create).toHaveBeenCalledTimes(2);
  expect(service.create.mock.calls[1][1].previewId).toBe('preview-b');
  expect(service.create.mock.calls[1][1].requestKey).not.toBe(service.create.mock.calls[0][1].requestKey);
  expect(sessionStorage.getItem(key)).toBe('batch/b');
  expect(service.stop).not.toHaveBeenCalled();
});

it('lets a page-refresh restoration of terminal A explicitly begin a new real preview, but not before authorization resolves', async () => {
  const restored = deferred<CallbackCompensationBatch>();
  const key = callbackCompensationStorageKey({ ...context, environment: 'PRE' });
  sessionStorage.setItem(key, 'batch/a');
  service.batch.mockReturnValueOnce(restored.promise);
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await open();
  expect(screen.getByRole('button', { name: '新建补偿' })).toBeDisabled();
  await act(async () => restored.resolve({
    ...batch,
    status: 'stopped',
    items: [{ ...batch.items[0], state: 'skipped' }],
    counts: { skipped: 1 },
  }));
  expect(screen.getByRole('button', { name: '新建补偿' })).toBeEnabled();
  expect(service.capabilities).toHaveBeenCalledTimes(1);
  expect(service.batch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '新建补偿' }));
  expect(sessionStorage.getItem(key)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
  await screen.findByText('run-eligible');
  expect(service.preview).toHaveBeenCalledTimes(1);
  expect(service.create).not.toHaveBeenCalled();
});

it('does not reset a batch with queued/sending work or pending audit, or while a refresh is in flight', async () => {
  const key = callbackCompensationStorageKey({ ...context, environment: 'PRE' });
  sessionStorage.setItem(key, 'batch/a');
  render(<CallbackCompensation workflowId={context.workflowId} title={context.title} />);
  await open();
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新批次' })).toBeEnabled());
  expect(screen.getByRole('button', { name: '新建补偿' })).toBeDisabled();
  for (const item of [
    { ...batch.items[0], state: 'sending' as const },
    { ...batch.items[0], state: 'received' as const, auditPending: true },
  ]) {
    service.batch.mockResolvedValueOnce({ ...batch, status: 'stopped', items: [item] });
    fireEvent.click(screen.getByRole('button', { name: '刷新批次' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新批次' })).toBeEnabled());
    expect(screen.getByRole('button', { name: '新建补偿' })).toBeDisabled();
  }
  const settled = { ...batch, status: 'completed' as const, items: [{ ...batch.items[0], state: 'received' as const }] };
  service.batch.mockResolvedValueOnce(settled);
  fireEvent.click(screen.getByRole('button', { name: '刷新批次' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '新建补偿' })).toBeEnabled());
  const late = deferred<typeof settled>();
  service.batch.mockReturnValueOnce(late.promise);
  fireEvent.click(screen.getByRole('button', { name: '刷新批次' }));
  expect(screen.getByRole('button', { name: '新建补偿' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '新建补偿' }));
  expect(sessionStorage.getItem(key)).toBe('batch/a');
  await act(async () => late.resolve(settled));
  expect(screen.getByRole('button', { name: '新建补偿' })).toBeEnabled();
  expect(service.preview).not.toHaveBeenCalled();
});

it('refuses a new-compensation reset of an unknown create and keeps its frozen body and key', async () => {
  service.create.mockRejectedValueOnce(new TypeError('timeout'));
  const { result } = renderHook(() => useCallbackCompensation(context, capabilities, vi.fn()));
  await act(async () => result.current.generatePreview());
  act(() => result.current.select('eligible', true));
  act(() => result.current.confirm());
  act(() => {
    result.current.setReason('人工核实后只补发通知消息');
    result.current.setAcknowledged(true);
  });
  await act(async () => result.current.execute());
  const frozen = service.create.mock.calls[0][1];
  expect(result.current.pending).toEqual(frozen);
  act(() => {
    result.current.startNew();
    result.current.updateRange({ runIds: 'new-run' });
    result.current.generatePreview();
  });
  expect(result.current.pending).toEqual(frozen);
  expect(result.current.runIds).toBe('');
  expect(service.preview).toHaveBeenCalledTimes(1);
  await act(async () => result.current.execute());
  expect(service.create.mock.calls[1][1]).toEqual(frozen);
});

it('periodically revokes capabilities and closes sensitive state without leaking an error body', async () => {
  const view = render(<CallbackCompensation workflowId={context.workflowId} />);
  await scan();
  fireEvent.click(screen.getByRole('button', { name: '关闭补偿窗口' }));
  view.unmount();
  vi.useFakeTimers();
  const fresh = render(<CallbackCompensation workflowId={context.workflowId} />);
  await act(async () => { await Promise.resolve(); });
  fireEvent.click(screen.getByRole('button', { name: '成功回调补偿' }));
  service.capabilities.mockRejectedValue(Object.assign(new Error('secret-capability'), { status: 403 }));
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument();
  expect(screen.queryByText(/secret-capability/)).not.toBeInTheDocument();
  fresh.unmount();
});

it('remounts on a capabilities environment change and ignores a late old-environment create', async () => {
  const late = deferred<typeof batch>();
  service.create.mockReturnValueOnce(late.promise);
  render(<CallbackCompensation workflowId={context.workflowId} />);
  await confirm();
  fireEvent.click(screen.getByRole('button', { name: '确认创建补偿批次' }));
  const signal = service.create.mock.calls[0][2]!;
  // The capability poll is a native server-bound check, not a browser environment flag.
  service.capabilities.mockResolvedValue({ ...capabilities, environment: 'PROD' });
  await waitFor(() => expect(signal.aborted).toBe(true), { timeout: 6500 });
  await act(async () => late.resolve(batch));
  expect(sessionStorage.length).toBe(0);
  await open();
  expect(screen.queryByText('batch/a')).not.toBeInTheDocument();
  expect(screen.queryByText('创建结果尚未确认')).not.toBeInTheDocument();
  expect(screen.getByText('选择补偿范围')).toBeInTheDocument();
});

it('denies revoked preview permission without leaving any prior receiver/payload visible', async () => {
  service.preview.mockRejectedValueOnce(Object.assign(new Error('secret-revocation'), { status: 403 }));
  render(<CallbackCompensation workflowId={context.workflowId} />);
  await open();
  fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.queryByText(/通知接收者|secret-revocation/)).not.toBeInTheDocument();
});

it('fences an in-flight capability read when a later mutation revokes permission', async () => {
  const late = deferred<typeof capabilities>();
  render(<CallbackCompensation workflowId={context.workflowId} />);
  await open();
  service.capabilities.mockReturnValueOnce(late.promise);
  vi.useFakeTimers();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  const signal = service.capabilities.mock.calls.at(-1)![1]!;
  service.preview.mockRejectedValueOnce(Object.assign(new Error('private-revoked-body'), { status: 403 }));
  fireEvent.click(screen.getByRole('button', { name: '生成预览' }));
  await act(async () => { await Promise.resolve(); });
  expect(signal.aborted).toBe(true);
  await act(async () => late.resolve(capabilities));
  expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('通知接收者')).not.toBeInTheDocument();
});

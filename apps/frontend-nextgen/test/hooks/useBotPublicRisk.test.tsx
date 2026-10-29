/** @jest-environment jsdom */
// useBotPublicRisk：client_request_id 幂等生命周期（同意图复用 / REJECT 后换新）+ 轮询启停 + 4xx 终止。
import { useBotPublicRisk } from '@/hooks/useBotPublicRisk';
import {
  createBotPublicRiskCheck,
  getCurrentBotPublicRiskCheck,
} from '@/services/backendApi/collaboration/collaborationBotPublicRiskController';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/backendApi/collaboration/collaborationBotPublicRiskController', () => ({
  createBotPublicRiskCheck: jest.fn(),
  getCurrentBotPublicRiskCheck: jest.fn(),
}));
jest.mock('@/components/ui/notify', () => ({
  notifyError: jest.fn(),
  notifySuccess: jest.fn(),
}));

const mockCreate = createBotPublicRiskCheck as jest.Mock;
const mockCurrent = getCurrentBotPublicRiskCheck as jest.Mock;

const rec = (phase: string, extra: Record<string, unknown> = {}) => ({
  risk_check_id: 'rk-1',
  phase,
  security_status: null,
  detail_url: null,
  auto_poll_paused: false,
  auto_poll_until: null,
  next_action_at: null,
  target: { public_scope: 'user', visibility: 'PUBLIC', view_depts: [] },
  error_reason: null,
  invalidation_reason: null,
  approval_puid: null,
  ...extra,
});
const ok = (data: unknown, code = 202000) => ({ code, message: 'OK', data });

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
});

afterEach(() => {
  act(() => {
    jest.runOnlyPendingTimers();
  });
  jest.useRealTimers();
});

it('reuses the same client_request_id for the same intent across retries', async () => {
  mockCreate.mockRejectedValueOnce(new Error('network'));
  mockCreate.mockResolvedValueOnce(ok(rec('PREPARING')));
  const { result } = renderHook(() => useBotPublicRisk({ botId: 'b', userId: 'u' }));

  const intent = { visibility: 'public' as const, view_depts: [] };
  await act(async () => {
    const created = await result.current.create(intent);
    expect(created).toBeNull();
  });
  await act(async () => {
    await result.current.create(intent);
  });

  const ids = mockCreate.mock.calls.map((call) => call[2].client_request_id);
  expect(ids[0]).toBe(ids[1]);
  expect(result.current.uiState.kind).toBe('checking');
});

it('regenerates the id when re-initiating the same intent after a REJECT conclusion', async () => {
  mockCreate.mockResolvedValueOnce(ok(rec('PREPARING')));
  const { result } = renderHook(() => useBotPublicRisk({ botId: 'b', userId: 'u' }));

  const intent = { visibility: 'public' as const, view_depts: [] };
  await act(async () => {
    await result.current.create(intent);
  });
  mockCurrent.mockResolvedValueOnce(ok(rec('CHECKING', { security_status: 'REJECT' }), 200000));
  await act(async () => {
    await result.current.queryCurrent();
  });
  expect(result.current.uiState.kind).toBe('failed');

  mockCreate.mockResolvedValueOnce(ok(rec('PREPARING', { risk_check_id: 'rk-2' })));
  await act(async () => {
    await result.current.create(intent);
  });
  const ids = mockCreate.mock.calls.map((call) => call[2].client_request_id);
  expect(ids[0]).not.toBe(ids[1]);
});

it('regenerates the id when re-initiating the same intent after an approval rejection (CLOSED)', async () => {
  // 审批 DISAGREE/CANCEL 的终态是 phase='CLOSED'（security_status 保持 PASS），
  // 同 ID 同意图重发会被后端幂等重放返回该死记录而不建新单，必须换新 ID。
  mockCreate.mockResolvedValueOnce(ok(rec('PREPARING')));
  const { result } = renderHook(() => useBotPublicRisk({ botId: 'b', userId: 'u' }));

  const intent = { visibility: 'public' as const, view_depts: [] };
  await act(async () => {
    await result.current.create(intent);
  });
  mockCurrent.mockResolvedValueOnce(ok(rec('CLOSED', { security_status: 'PASS' }), 200000));
  await act(async () => {
    await result.current.queryCurrent();
  });
  expect(result.current.uiState.kind).toBe('idle');

  mockCreate.mockResolvedValueOnce(ok(rec('PREPARING', { risk_check_id: 'rk-2' })));
  await act(async () => {
    await result.current.create(intent);
  });
  const ids = mockCreate.mock.calls.map((call) => call[2].client_request_id);
  expect(ids[0]).not.toBe(ids[1]);
});

it('invalidates the request id and poll-fatal state when the editor session reopens', async () => {
  mockCreate.mockResolvedValueOnce(ok(rec('PREPARING')));
  type SessionProps = { botId: string; userId: string; enabled: boolean };
  const initialProps: SessionProps = { botId: 'b', userId: 'u', enabled: true };
  const { result, rerender } = renderHook((props: SessionProps) => useBotPublicRisk(props), {
    initialProps,
  });

  const intent = { visibility: 'public' as const, view_depts: [] };
  await act(async () => {
    await result.current.create(intent);
  });
  const firstId = mockCreate.mock.calls[0][2].client_request_id;

  // 编辑器关闭再打开（enabled false→true）：上一轮申请标识作废，同意图重发换新 ID
  rerender({ ...initialProps, enabled: false });
  rerender(initialProps);
  mockCreate.mockResolvedValueOnce(ok(rec('PREPARING', { risk_check_id: 'rk-2' })));
  await act(async () => {
    await result.current.create(intent);
  });
  expect(mockCreate.mock.calls[1][2].client_request_id).not.toBe(firstId);
});

it('tolerates data:null on current queries and stays idle', async () => {
  mockCurrent.mockResolvedValueOnce(ok(null, 200000));
  const { result } = renderHook(() => useBotPublicRisk({ botId: 'b', userId: 'u' }));

  await act(async () => {
    await result.current.queryCurrent();
  });

  expect(result.current.currentRecord).toBeNull();
  expect(result.current.uiState.kind).toBe('idle');
});

it('polls while checking and stops on unmount', async () => {
  mockCurrent.mockResolvedValue(ok(rec('CHECKING')));
  const { result, unmount } = renderHook(() => useBotPublicRisk({ botId: 'b', userId: 'u' }));

  await act(async () => {
    await result.current.queryCurrent();
  });
  mockCurrent.mockClear();
  await act(async () => {
    jest.advanceTimersByTime(15_000);
  });
  await act(async () => {
    jest.advanceTimersByTime(15_000);
  });
  expect(mockCurrent).toHaveBeenCalledTimes(2);

  unmount();
  mockCurrent.mockClear();
  await act(async () => {
    jest.advanceTimersByTime(45_000);
  });
  expect(mockCurrent).not.toHaveBeenCalled();
});

it('stops polling after a 4xx response and surfaces a notification', async () => {
  mockCurrent.mockResolvedValueOnce(ok(rec('CHECKING')));
  const { result } = renderHook(() => useBotPublicRisk({ botId: 'b', userId: 'u' }));

  await act(async () => {
    await result.current.queryCurrent();
  });

  mockCurrent.mockRejectedValue(Object.assign(new Error('forbidden'), { response: { status: 403 } }));
  await act(async () => {
    jest.advanceTimersByTime(15_000);
  });

  const { notifyError } = jest.requireMock('@/components/ui/notify') as { notifyError: jest.Mock };
  expect(notifyError).toHaveBeenCalled();
  mockCurrent.mockClear();
  await act(async () => {
    jest.advanceTimersByTime(45_000);
  });
  expect(mockCurrent).not.toHaveBeenCalled();
});

it('stops polling once the record reaches the approval phase', async () => {
  mockCurrent.mockResolvedValueOnce(ok(rec('APPROVING', { security_status: 'PASS' })));
  const { result } = renderHook(() => useBotPublicRisk({ botId: 'b', userId: 'u' }));

  await act(async () => {
    await result.current.queryCurrent();
  });
  expect(result.current.uiState.kind).toBe('approving');

  mockCurrent.mockClear();
  await act(async () => {
    jest.advanceTimersByTime(45_000);
  });
  expect(mockCurrent).not.toHaveBeenCalled();
});

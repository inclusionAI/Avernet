/** @jest-environment jsdom */
import { useBotCollaborationRepair } from '@/hooks/useBotCollaborationRepair';
import { botCollaborationRepairService } from '@/services/bots/botCollaborationRepairService';
import type { CollaborationRepairReport } from '@/services/bots/botCollaborationRepairService';
import { safeReportError } from '@/utils/errorToast';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/bots/botCollaborationRepairService', () => ({
  botCollaborationRepairService: { repair: jest.fn(), pending: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), warning: jest.fn(), error: jest.fn() } }));
jest.mock('@/utils/errorToast', () => ({ safeReportError: jest.fn() }));

const repair = jest.mocked(botCollaborationRepairService.repair);
const pending = jest.mocked(botCollaborationRepairService.pending);

const successReport: CollaborationRepairReport = {
  bot_id: 'b1',
  status: 'succeeded',
  items: [
    { component: 'ownership', status: 'succeeded', error_code: null },
    { component: 'profile', status: 'succeeded', error_code: null },
    { component: 'managers', status: 'succeeded', error_code: null },
  ],
};

const reportWith = (status: CollaborationRepairReport['status']): CollaborationRepairReport => ({
  ...successReport,
  status,
  items: successReport.items.map((item) => ({ ...item })),
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  jest.clearAllMocks();
  pending.mockReturnValue(undefined);
});

test('double click produces one request', async () => {
  let resolve!: (value: CollaborationRepairReport) => void;
  repair.mockImplementation(() => new Promise((r) => { resolve = r; }));
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));
  let first!: Promise<void>;
  act(() => { first = result.current.run(); void result.current.run(); });
  expect(botCollaborationRepairService.repair).toHaveBeenCalledTimes(1);
  expect(result.current.loading).toBe(true);
  await act(async () => { resolve(successReport); await first; });
  expect(result.current.report?.status).toBe('succeeded');
  expect(refresh).toHaveBeenCalledTimes(1);
});

test('succeeded 提示成功并对非 failed 报告只刷新一次', async () => {
  repair.mockResolvedValue(reportWith('succeeded'));
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));

  await act(async () => { await result.current.run(); });

  expect(toast.success).toHaveBeenCalledTimes(1);
  expect(toast.warning).not.toHaveBeenCalled();
  expect(toast.error).not.toHaveBeenCalled();
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(result.current.report?.status).toBe('succeeded');
  expect(result.current.loading).toBe(false);
  expect(result.current.error).toBeUndefined();
  expect(result.current.refreshError).toBeUndefined();
});

test('partial_failed 提示警告但仍刷新配置', async () => {
  repair.mockResolvedValue(reportWith('partial_failed'));
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));

  await act(async () => { await result.current.run(); });

  expect(toast.warning).toHaveBeenCalledTimes(1);
  expect(toast.success).not.toHaveBeenCalled();
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(result.current.report?.status).toBe('partial_failed');
});

test('failed 提示错误且不刷新配置', async () => {
  repair.mockResolvedValue(reportWith('failed'));
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));

  await act(async () => { await result.current.run(); });

  expect(toast.error).toHaveBeenCalledTimes(1);
  expect(toast.success).not.toHaveBeenCalled();
  expect(refresh).not.toHaveBeenCalled();
  expect(result.current.report?.status).toBe('failed');
});

test('未知整体状态不弹成功提示', async () => {
  const unknown = { ...successReport, status: 'unknown' as unknown as CollaborationRepairReport['status'] };
  repair.mockResolvedValue(unknown);
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));

  await act(async () => { await result.current.run(); });

  expect(toast.success).not.toHaveBeenCalled();
  expect(toast.warning).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledTimes(1);
  expect(result.current.report?.status).toBe('unknown');
});

test('请求失败转为错误态且不刷新配置', async () => {
  const failure = new Error('后端超时');
  repair.mockRejectedValue(failure);
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));

  await act(async () => { await result.current.run(); });

  expect(result.current.report).toBeUndefined();
  expect(result.current.error).toBe('未能确认修复结果，请稍后重试');
  expect(result.current.loading).toBe(false);
  expect(safeReportError).toHaveBeenCalledWith(failure, { title: 'Bot 信息修复失败' });
  expect(refresh).not.toHaveBeenCalled();
});

test('刷新失败保留原报告并给出独立刷新错误，不改写成修复失败', async () => {
  repair.mockResolvedValue(reportWith('succeeded'));
  const refresh = jest.fn().mockRejectedValue(new Error('读取失败'));
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));

  await act(async () => { await result.current.run(); });

  expect(result.current.report?.status).toBe('succeeded');
  expect(result.current.refreshError).toBe('修复请求已完成，配置刷新失败，请重新打开查看');
  expect(result.current.error).toBeUndefined();
  expect(safeReportError).not.toHaveBeenCalled();
  expect(toast.error).not.toHaveBeenCalled();
  expect(result.current.loading).toBe(false);
});

test('切换 botId 后旧请求完成不写新状态、不刷新、不提示', async () => {
  const active = deferred<CollaborationRepairReport>();
  repair.mockReturnValue(active.promise);
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result, rerender } = renderHook(
    ({ botId }) => useBotCollaborationRepair(botId, 'u1', true, refresh),
    { initialProps: { botId: 'b1' } },
  );
  act(() => { void result.current.run(); });

  rerender({ botId: 'b2' });
  expect(result.current.loading).toBe(false);

  await act(async () => {
    active.resolve(successReport);
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(refresh).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
  expect(toast.warning).not.toHaveBeenCalled();
  expect(toast.error).not.toHaveBeenCalled();
  expect(result.current.report).toBeUndefined();
  expect(result.current.loading).toBe(false);
});

test('卸载后旧请求完成不写状态、不刷新、不提示', async () => {
  const active = deferred<CollaborationRepairReport>();
  repair.mockReturnValue(active.promise);
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result, unmount } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));
  act(() => { void result.current.run(); });

  unmount();
  await act(async () => {
    active.resolve(successReport);
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(refresh).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
  expect(safeReportError).not.toHaveBeenCalled();
});

test('enabled 关闭后旧请求完成不写状态、不刷新、不提示', async () => {
  const active = deferred<CollaborationRepairReport>();
  repair.mockReturnValue(active.promise);
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result, rerender } = renderHook(
    ({ enabled }) => useBotCollaborationRepair('b1', 'u1', enabled, refresh),
    { initialProps: { enabled: true } },
  );
  act(() => { void result.current.run(); });

  rerender({ enabled: false });
  await act(async () => {
    active.resolve(successReport);
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(refresh).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
  expect(result.current.report).toBeUndefined();
});

test('enabled 为 false 时 run 不发请求', () => {
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', false, refresh));

  act(() => { void result.current.run(); });

  expect(repair).not.toHaveBeenCalled();
  expect(result.current.loading).toBe(false);
});

test('重新挂载时跟踪在途请求，不重复发起 POST', async () => {
  const active = deferred<CollaborationRepairReport>();
  pending.mockReturnValue(active.promise);
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));

  expect(pending).toHaveBeenCalledWith('b1', 'u1');
  expect(repair).not.toHaveBeenCalled();
  expect(result.current.loading).toBe(true);

  await act(async () => {
    active.resolve(successReport);
    await Promise.resolve();
    await Promise.resolve();
  });

  await waitFor(() => expect(result.current.report?.status).toBe('succeeded'));
  expect(result.current.loading).toBe(false);
  expect(toast.success).toHaveBeenCalledTimes(1);
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(repair).not.toHaveBeenCalled();
});

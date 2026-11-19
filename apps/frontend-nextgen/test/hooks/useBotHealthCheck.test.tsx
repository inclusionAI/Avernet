/** @jest-environment jsdom */

import type { BotHealthCheckSummary, BotHealthCheckTarget } from '@/domain/botHealthCheck';
import { useBotHealthCheck } from '@/hooks/useBotHealthCheck';
import { botHealthCheckService } from '@/services/botHealthCheck';
import '@testing-library/jest-dom';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/botHealthCheck', () => ({
  botHealthCheckService: {
    getCapability: () => ({
      dimensions: ['configuration'],
      showRadar: false,
      showLogDetails: false,
      showRawSnapshot: false,
    }),
    load: jest.fn(),
    runDiagnose: jest.fn(),
    previewPatch: jest.fn(),
    applyPatch: jest.fn(),
  },
}));

jest.mock('sonner', () => ({
  toast: { success: jest.fn(), error: jest.fn(), info: jest.fn() },
}));

const { toast } = jest.requireMock('sonner') as { toast: { success: jest.Mock; error: jest.Mock; info: jest.Mock } };

const target: BotHealthCheckTarget = {
  botId: 'b1',
  userId: 'u1',
  botName: '测试 Bot',
  engine: 'openclaw',
  context: { entityType: 'staff', entityId: 'e1' },
};

function buildSummary(overrides: Partial<BotHealthCheckSummary> = {}): BotHealthCheckSummary {
  return {
    botId: 'b1',
    entityId: 'e1',
    overallStatus: 'healthy',
    healthScore: 90,
    dimensions: [
      {
        key: 'configuration',
        label: '配置健康度',
        scanDim: 'full:L1',
        scanStatus: 'completed',
        status: 'passed',
        patches: [],
      },
    ],
    history: [],
    ...overrides,
  };
}

const scanningSummary = buildSummary({ overallStatus: 'scanning' });
const completedSummary = buildSummary({ overallStatus: 'healthy' });

describe('useBotHealthCheck 轮询', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    (botHealthCheckService.load as jest.Mock).mockReset();
    (botHealthCheckService.runDiagnose as jest.Mock).mockReset();
    toast.success.mockClear();
    toast.error.mockClear();
  });

  afterEach(() => {
    act(() => jest.runOnlyPendingTimers());
    jest.useRealTimers();
  });

  test('进入页面时若仍在检测，自动轮询直到出结果', async () => {
    (botHealthCheckService.load as jest.Mock)
      .mockResolvedValueOnce(scanningSummary)
      .mockResolvedValueOnce(completedSummary);

    const { result } = renderHook(() => useBotHealthCheck());
    await act(async () => {
      result.current.openHealthCheck(target);
    });

    // 首轮立即拉到 scanning，随后的报告中仍在 scanning → 持续轮询
    expect(result.current.checking).toBe(true);

    await act(async () => {
      await jest.advanceTimersByTimeAsync(3000);
    });

    expect(botHealthCheckService.load).toHaveBeenCalledTimes(2);
    expect(result.current.summary?.overallStatus).toBe('healthy');
    expect(result.current.checking).toBe(false);
    expect(toast.success).toHaveBeenCalledWith('健康检查完成');
  });

  test('轮询完成后停止，不再继续定时拉取', async () => {
    (botHealthCheckService.load as jest.Mock).mockResolvedValue(completedSummary);

    const { result } = renderHook(() => useBotHealthCheck());
    await act(async () => {
      result.current.openHealthCheck(target);
    });

    expect(result.current.checking).toBe(false);
    expect(botHealthCheckService.load).toHaveBeenCalledTimes(1);

    await act(async () => {
      await jest.advanceTimersByTimeAsync(30_000);
    });

    expect(botHealthCheckService.load).toHaveBeenCalledTimes(1);
  });

  test('重新检测：启动诊断后轮询到完成，检测中按钮态保持 loading', async () => {
    (botHealthCheckService.runDiagnose as jest.Mock).mockResolvedValue({ scan_id: 7 });
    // 进入页面时首轮即完成
    (botHealthCheckService.load as jest.Mock).mockResolvedValueOnce(completedSummary);

    const { result } = renderHook(() => useBotHealthCheck());
    await act(async () => {
      result.current.openHealthCheck(target);
    });
    expect(result.current.checking).toBe(false);

    // 重新检测：第一轮报告还在 scanning，推进一个轮询周期后完成
    (botHealthCheckService.load as jest.Mock)
      .mockResolvedValueOnce(scanningSummary)
      .mockResolvedValueOnce(completedSummary);

    await act(async () => {
      void result.current.runDiagnose();
    });
    expect(result.current.checking).toBe(true);

    await act(async () => {
      await jest.advanceTimersByTimeAsync(3000);
    });

    expect(botHealthCheckService.runDiagnose).toHaveBeenCalledWith(target);
    expect(toast.success).toHaveBeenCalledWith('健康检查已开始（#7）');
    expect(result.current.checking).toBe(false);
    expect(result.current.summary?.overallStatus).toBe('healthy');
  });

  test('部分维度失败时以错误提示收尾', async () => {
    const failedSummary = buildSummary({
      overallStatus: 'critical',
      dimensions: [
        {
          key: 'configuration',
          label: '配置健康度',
          scanDim: 'full:L1',
          scanStatus: 'failed',
          status: 'error',
          failedReason: '配置文件读取失败',
          patches: [],
        },
      ],
    });
    (botHealthCheckService.load as jest.Mock)
      .mockResolvedValueOnce(scanningSummary)
      .mockResolvedValueOnce(failedSummary);

    const { result } = renderHook(() => useBotHealthCheck());
    await act(async () => {
      result.current.openHealthCheck(target);
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(3000);
    });

    expect(result.current.checking).toBe(false);
    expect(toast.error).toHaveBeenCalledWith('配置文件读取失败');
  });
});

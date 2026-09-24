/** @jest-environment jsdom */
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import type { BotSessionFileView } from '@/stores/botSessionFileStore';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, renderHook } from '@testing-library/react';

const mockLoadReady = jest.fn();
const mockToastError = jest.fn();

jest.mock('@/services/workspace/botSessionFileService', () => ({
  botSessionFileService: {
    loadReady: (...args: unknown[]) => mockLoadReady(...(args as [])),
  },
  isLargeBotSessionFile: () => false,
}));

jest.mock('sonner', () => ({ toast: { error: mockToastError } }));

function makeFile(resourceId: string, displayName: string): BotSessionFileView {
  return { resourceId, displayName, status: 'ready', sizeBytes: 1, errorCode: null };
}

function readyFiles(): string[] {
  return useBotSessionFileStore
    .getState()
    .readyFiles.map((f) => f.resourceId)
    .sort();
}

describe('useBotSessionFiles（单聊会话文件 hook）', () => {
  beforeEach(() => {
    mockLoadReady.mockReset();
    useBotSessionFileStore.getState().resetForSession();
    useBotSessionFileStore.setState({ isLoadingList: false });
  });

  it('refresh 拉取列表并写入 store', async () => {
    mockLoadReady.mockResolvedValueOnce({ ok: true, data: { items: [makeFile('r1', 'a.json')], total: 1 } });
    const { result } = renderHook(({ sid }: { sid: string }) => useBotSessionFiles('bot-1', sid, 'u1'), {
      initialProps: { sid: 's1' },
    });
    await act(async () => {
      await result.current.refresh();
    });
    expect(readyFiles()).toEqual(['r1']);
    expect(useBotSessionFileStore.getState().isLoadingList).toBe(false);
  });

  it('切会话后旧响应晚到被丢弃，不污染新会话列表（PR#413 评审跟进 P3 #257104461）', async () => {
    type ListResult = { ok: true; data: { items: BotSessionFileView[]; total: number } };
    let resolveFirst!: (value: ListResult) => void;
    let resolveSecond!: (value: ListResult) => void;
    mockLoadReady
      .mockImplementationOnce(
        () =>
          new Promise<ListResult>((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<ListResult>((resolve) => {
            resolveSecond = resolve;
          }),
      );

    const { result, rerender } = renderHook(({ sid }: { sid: string }) => useBotSessionFiles('bot-1', sid, 'u1'), {
      initialProps: { sid: 's1' },
    });

    // s1 刷新发起（慢，不 await——模拟在途）。
    act(() => {
      void result.current.refresh();
    });
    // 切到 s2：reset 清空旧列表，发起第二请求。
    rerender({ sid: 's2' });
    act(() => {
      void result.current.refresh();
    });

    // s2 先返回 → store 列表为 s2 的文件。
    await act(async () => {
      resolveSecond({ ok: true, data: { items: [makeFile('r2', '会话2文件')], total: 1 } });
    });
    expect(readyFiles()).toEqual(['r2']);

    // s1 晚到：守卫丢弃过期响应，store 不被旧会话数据污染，loading 由最新请求收尾。
    await act(async () => {
      resolveFirst({ ok: true, data: { items: [makeFile('r1', '会话1文件')], total: 1 } });
    });
    expect(readyFiles()).toEqual(['r2']);
    expect(useBotSessionFileStore.getState().isLoadingList).toBe(false);
  });

  it('会话参数缺失时作废在途请求并收尾加载态，晚到响应不写入（评审 P4 跟进 #257104636）', async () => {
    type ListResult = { ok: true; data: { items: BotSessionFileView[]; total: number } };
    let resolveFirst!: (value: ListResult) => void;
    mockLoadReady.mockImplementationOnce(
      () =>
        new Promise<ListResult>((resolve) => {
          resolveFirst = resolve;
        }),
    );

    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useBotSessionFiles('bot-1', sid, 'u1'),
      { initialProps: { sid: 's1' as string | null } },
    );

    // s1 刷新发起（在途）。
    act(() => {
      void result.current.refresh();
    });

    // 切到无会话：refresh 早退——应作废在途请求并收尾加载态（reset 保留 loading 后由早退收尾）。
    rerender({ sid: null });
    act(() => {
      void result.current.refresh();
    });
    expect(useBotSessionFileStore.getState().isLoadingList).toBe(false);

    // s1 晚到：守卫丢弃，store 保持清空。
    await act(async () => {
      resolveFirst({ ok: true, data: { items: [makeFile('r1', '会话1文件')], total: 1 } });
    });
    expect(readyFiles()).toEqual([]);
    expect(useBotSessionFileStore.getState().isLoadingList).toBe(false);
  });
});

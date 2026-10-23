/** @jest-environment jsdom */
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import type { BotSessionFileView } from '@/stores/botSessionFileStore';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, renderHook, waitFor } from '@testing-library/react';

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
    mockToastError.mockClear();
    useBotSessionFileStore.getState().resetForSession();
    useBotSessionFileStore.setState({ isLoadingList: false });
  });

  it('好友会话刷新透传好友身份，不在挂载时请求', async () => {
    mockLoadReady.mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
    const { result } = renderHook(() => useBotSessionFiles('bot-1', 's1', 'u1', 'owner-1', 'u1'));
    expect(mockLoadReady).not.toHaveBeenCalled();
    await act(async () => { await result.current.refresh(); });
    expect(mockLoadReady).toHaveBeenCalledWith('bot-1', 's1', 'u1', 'owner-1', 'u1');
  });

  it('懒加载(2026-10-21):进入会话不再默认调 file 接口,refresh(/file 唤起或面板打开)才加载;缓存变化不重复请求', async () => {
    const { result } = renderHook(({ sid }: { sid: string }) => useBotSessionFiles('bot-1', sid, 'u1', 'owner-1'), {
      initialProps: { sid: 's1' },
    });
    // 懒加载:挂载/进入会话不自动调 file 接口。
    expect(mockLoadReady).not.toHaveBeenCalled();
    expect(result.current.readyFiles).toEqual([]);
    // 手动 refresh 才拉取。
    mockLoadReady.mockResolvedValueOnce({ ok: true, data: { items: [makeFile('r1', 'a.json')], total: 1 } });
    await act(async () => {
      await result.current.refresh();
    });
    expect(mockLoadReady).toHaveBeenCalledTimes(1);
    expect(mockLoadReady).toHaveBeenCalledWith('bot-1', 's1', 'u1', 'owner-1', undefined);
    expect(result.current.readyFiles.map((file) => file.resourceId)).toEqual(['r1']);
    // 本地缓存变化(上传就绪)不触发请求。
    act(() => result.current.addReadyFile(makeFile('uploaded', 'new.txt')));
    expect(mockLoadReady).toHaveBeenCalledTimes(1);
    // 再次手动刷新拉最新清单。
    mockLoadReady.mockResolvedValueOnce({ ok: true, data: { items: [makeFile('r2', 'b.json')], total: 1 } });
    await act(async () => {
      await result.current.refresh();
    });
    expect(readyFiles()).toEqual(['r2']);
    expect(mockLoadReady).toHaveBeenCalledTimes(2);
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

    // 懒加载(2026-10-21):s1 手动 refresh 发起慢请求;切到 s2(cleanup 作废在途)后再手动发起第二请求。
    act(() => {
      void result.current.refresh();
    });
    expect(mockLoadReady).toHaveBeenCalledTimes(1);
    rerender({ sid: 's2' });
    act(() => {
      void result.current.refresh();
    });
    expect(mockLoadReady).toHaveBeenCalledTimes(2);

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

    const { result, rerender } = renderHook(({ sid }: { sid: string | null }) => useBotSessionFiles('bot-1', sid, 'u1'), {
      initialProps: { sid: 's1' as string | null },
    });

    // 懒加载(2026-10-21):手动 refresh 发起慢请求;退出会话自动作废在途请求并收尾加载态。
    act(() => {
      void result.current.refresh();
    });
    rerender({ sid: null });
    expect(mockLoadReady).toHaveBeenCalledTimes(1);
    expect(useBotSessionFileStore.getState().isLoadingList).toBe(false);

    // s1 晚到：守卫丢弃，store 保持清空。
    await act(async () => {
      resolveFirst({ ok: true, data: { items: [makeFile('r1', '会话1文件')], total: 1 } });
    });
    expect(readyFiles()).toEqual([]);
    expect(useBotSessionFileStore.getState().isLoadingList).toBe(false);
  });

  it('卸载后旧响应不能覆盖新挂载会话的文件', async () => {
    let resolveOld!: (value: unknown) => void;
    mockLoadReady.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    );
    const old = renderHook(() => useBotSessionFiles('bot-1', 's1', 'u1'));
    // 懒加载(2026-10-21):手动 refresh 发起慢请求,卸载 cleanup 作废在途。
    act(() => {
      void old.result.current.refresh();
    });
    old.unmount();
    mockLoadReady.mockResolvedValueOnce({ ok: true, data: { items: [makeFile('new', 'new.txt')], total: 1 } });
    const next = renderHook(() => useBotSessionFiles('bot-1', 's2', 'u1'));
    await act(async () => {
      await next.result.current.refresh();
    });
    await waitFor(() => expect(readyFiles()).toEqual(['new']));
    await act(async () => resolveOld({ ok: true, data: { items: [makeFile('old', 'old.txt')], total: 1 } }));
    expect(readyFiles()).toEqual(['new']);
  });
});

/** @jest-environment jsdom */
import { useSessionFiles } from '@/pages/Workspace/hooks/useSessionFiles';
import type { SessionFileView } from '@/services/workspace/sessionFileService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, renderHook } from '@testing-library/react';

const mockLoadFiles = jest.fn();
const mockToastError = jest.fn();

jest.mock('@/services/workspace/sessionFileService', () => ({
  sessionFileService: {
    loadFiles: (...args: unknown[]) => mockLoadFiles(...(args as [])),
    resolveActorNames: async () => ({}),
  },
}));

jest.mock('sonner', () => ({ toast: { error: mockToastError } }));

function makeFile(fileId: string, name: string): SessionFileView {
  return {
    fileId,
    sessionId: 's-any',
    name,
    mimeType: 'text/plain',
    size: 1,
    status: 'ready',
    ownerActorId: 'human_1',
    ownerKind: 'human',
    ownerName: '风太',
    sha256: null,
    createdAt: 1,
    updatedAt: 1,
  };
}

describe('useSessionFiles（协作群会话文件 hook）', () => {
  beforeEach(() => {
    mockLoadFiles.mockReset();
  });

  it('挂载与会话切换时自动拉取列表', async () => {
    mockLoadFiles.mockResolvedValueOnce({ ok: true, data: { items: [makeFile('f1', 'a.md')], total: 1 } });
    const { result } = renderHook(({ sid }: { sid: string }) => useSessionFiles(sid, undefined, null), {
      initialProps: { sid: 's1' },
    });
    await act(async () => {
      await new Promise((r) => {
        setTimeout(r);
      });
    });
    expect(result.current.files.map((f) => f.fileId)).toEqual(['f1']);
    expect(result.current.isLoading).toBe(false);
  });

  it('快速切会话竞态：旧会话晚到响应被丢弃，不覆盖新会话列表（PR#413 评审跟进 P3 #257104461）', async () => {
    type ListResult = { ok: true; data: { items: SessionFileView[]; total: number } };
    let resolveFirst!: (value: ListResult) => void;
    let resolveSecond!: (value: ListResult) => void;
    mockLoadFiles
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

    const { result, rerender } = renderHook(({ sid }: { sid: string }) => useSessionFiles(sid, undefined, null), {
      initialProps: { sid: 's1' },
    });
    expect(result.current.isLoading).toBe(true);

    // 切到 s2：发起第二请求，s1 响应仍在途。
    rerender({ sid: 's2' });

    // s2 先返回 → 列表为 s2 的文件。
    await act(async () => {
      resolveSecond({ ok: true, data: { items: [makeFile('f2', '会话2文件')], total: 1 } });
    });
    expect(result.current.files.map((f) => f.fileId)).toEqual(['f2']);

    // s1 晚到：守卫丢弃过期响应，列表不被旧会话数据污染，loading 由最新请求收尾。
    await act(async () => {
      resolveFirst({ ok: true, data: { items: [makeFile('f1', '会话1文件')], total: 1 } });
    });
    expect(result.current.files.map((f) => f.fileId)).toEqual(['f2']);
    expect(result.current.isLoading).toBe(false);
  });

  it('会话标识缺失时作废在途请求并收尾加载态，晚到响应不写入（评审 P4 跟进 #257104636）', async () => {
    type ListResult = { ok: true; data: { items: SessionFileView[]; total: number } };
    let resolveFirst!: (value: ListResult) => void;
    mockLoadFiles.mockImplementationOnce(
      () =>
        new Promise<ListResult>((resolve) => {
          resolveFirst = resolve;
        }),
    );

    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionFiles(sid, undefined, null),
      { initialProps: { sid: 's1' as string | null } },
    );
    expect(result.current.isLoading).toBe(true);

    // 切到无会话：refresh 早退——应作废在途请求、清空列表并收尾加载态。
    rerender({ sid: null });
    expect(result.current.files).toEqual([]);
    expect(result.current.isLoading).toBe(false);

    // s1 晚到：守卫丢弃，列表保持清空。
    await act(async () => {
      resolveFirst({ ok: true, data: { items: [makeFile('f1', '会话1文件')], total: 1 } });
    });
    expect(result.current.files).toEqual([]);
    expect(result.current.isLoading).toBe(false);
  });
});

/** @jest-environment jsdom */
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { botSessionFileService } from '@/services/workspace/botSessionFileService';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// 使用 auto-mock，避免 factory 内引用 @jest/globals 的 jest 触发提升后的 TDZ。
jest.mock('@/services/workspace/botSessionFileService');

const mockedFileService = botSessionFileService as unknown as {
  validateFiles: jest.Mock;
  uploadOne: jest.Mock;
};
const uploadOne = mockedFileService.uploadOne;

beforeEach(() => {
  useBotSessionFileStore.getState().resetForSession();
  jest.clearAllMocks();
  mockedFileService.validateFiles.mockReturnValue(null);
});

describe('useBotSessionFileUpload', () => {
  it('支持选中文件后立即提交，并将完成上传的文件保留为可引用任务', async () => {
    uploadOne.mockResolvedValue({
      ok: true,
      data: {
        resourceId: 'resource-1',
        displayName: '需求说明.md',
        status: 'ready',
        sizeBytes: 10,
        errorCode: null,
      },
    });
    const file = new File(['content'], '需求说明.md', { type: 'text/markdown' });
    const { result } = renderHook(() => useBotSessionFileUpload('bot-1', 'session-1', 'user-1'));

    act(() => {
      result.current.stageFiles([file]);
      void result.current.submit();
    });

    await waitFor(() =>
      expect(uploadOne).toHaveBeenCalledWith('bot-1', 'session-1', 'user-1', file, expect.any(Object)),
    );
    expect(result.current.tasks[0]).toMatchObject({ phase: 'ready', resourceId: 'resource-1' });
  });
});

it('取消后忽略晚到的上传成功，不引用旧文件、不消费新会话队列或覆盖上传态', async () => {
  let resolveUpload!: (value: unknown) => void;
  uploadOne.mockImplementationOnce(() => new Promise((resolve) => { resolveUpload = resolve; }));
  const { result } = renderHook(() => useBotSessionFileUpload('bot-1', 'session-1', 'user-1'));
  const ready = jest.fn();
  let pending!: Promise<void>;
  act(() => {
    result.current.stageFiles([new File(['old'], 'old.md')]);
    pending = result.current.submit(ready);
  });
  const signal = (uploadOne.mock.calls[0][4] as { signal: AbortSignal }).signal;
  act(() => result.current.cancelAll());
  expect(signal.aborted).toBe(true);
  act(() => {
    useBotSessionFileStore.getState().resetForSession();
    result.current.stageFiles([new File(['new'], 'new.md')]);
    useBotSessionFileStore.getState().setIsUploading(true);
  });
  await act(async () => {
    resolveUpload({ ok: true, data: { resourceId: 'old-resource', displayName: 'old.md', status: 'ready' } });
    await pending;
  });
  expect(ready).not.toHaveBeenCalled();
  expect(useBotSessionFileStore.getState().readyFiles).toEqual([]);
  expect(useBotSessionFileStore.getState().isUploading).toBe(true);
  expect(uploadOne).toHaveBeenCalledTimes(1);
});

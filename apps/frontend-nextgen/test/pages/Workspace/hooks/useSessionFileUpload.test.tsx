/** @jest-environment jsdom */
import { useSessionFileUpload } from '@/pages/Workspace/hooks/useSessionFileUpload';
import type { SessionFileView } from '@/services/workspace/sessionFileService';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/sessionFileService');
const svc = sessionFileService as unknown as Record<string, jest.Mock<any>>;

const readyFile: SessionFileView = {
  fileId: 'f1',
  sessionId: 's1',
  name: 'a.md',
  mimeType: 'text/markdown',
  size: 12,
  status: 'ready',
  ownerActorId: 'human_1',
  ownerKind: 'human',
  ownerName: '张三',
  sha256: null,
  createdAt: 1,
  updatedAt: 2,
};

beforeEach(() => {
  jest.resetAllMocks();
});

it('stageFiles accepts allowed ext and rejects illegal', () => {
  const { result } = renderHook(() => useSessionFileUpload('s1', jest.fn()));
  act(() => {
    result.current.stageFiles([
      new File(['x'], 'good.md', { type: 'text/markdown' }),
      new File(['x'], '@/pages/Workspace/hooks/bad.exe'),
    ]);
  });
  expect(result.current.queue).toHaveLength(1);
  expect(result.current.queue[0].name).toBe('good.md');
});

it('submitStaged runs prepare → upload → complete and marks ready', async () => {
  svc.prepareUpload.mockResolvedValue({ ok: true, data: { fileId: 'f1', uploadUrl: undefined, parts: undefined } });
  svc.uploadContent.mockResolvedValue(undefined);
  svc.completeUpload.mockResolvedValue({ ok: true, data: readyFile });
  const onUploaded = jest.fn();
  const { result } = renderHook(() => useSessionFileUpload('s1', onUploaded));

  act(() => {
    result.current.stageFiles([new File(['hello'], 'a.md', { type: 'text/markdown' })]);
  });
  await act(async () => {
    await result.current.submitStaged();
  });
  await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(expect.objectContaining({ fileId: 'f1' })));
  expect(svc.prepareUpload).toHaveBeenCalled();
  expect(svc.uploadContent).toHaveBeenCalled();
  expect(svc.completeUpload).toHaveBeenCalledWith('s1', 'f1');
});

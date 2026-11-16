/** @jest-environment jsdom */
import { useSessionFilePreview } from '@/pages/Workspace/hooks/useSessionFilePreview';
import { sessionFileService, type SessionFileView } from '@/services/workspace/sessionFileService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { renderHook } from '@testing-library/react';

jest.mock('@/services/workspace/sessionFileService');

const fileOf = (name: string, mimeType = 'application/octet-stream'): SessionFileView => ({
  fileId: 'file-1',
  sessionId: 'group:round-1',
  name,
  mimeType,
  size: 9837,
  status: 'ready',
  ownerActorId: 'human_1',
  ownerKind: 'human',
  ownerName: '用户',
  sha256: null,
  createdAt: 0,
  updatedAt: 0,
});
const urlOf = (sessionId: string, fileId: string) =>
  `https://gateway.example/api/v1/collaboration/sessions/${encodeURIComponent(
    sessionId,
  )}/files/${fileId}/content?show=true`;

beforeEach(() => {
  jest.resetAllMocks();
  jest.mocked(sessionFileService.buildContentUrl).mockImplementation(urlOf);
  jest.mocked(sessionFileService.fetchContentBlob).mockResolvedValue({
    ok: true,
    data: { size: 2, text: async () => '{}' } as Blob,
  });
});

describe('useSessionFilePreview browser-loaded group files', () => {
  it.each(['说明.md', '数据.json', '配置.yaml', '日志.log', '代码.ts', '表格.csv'])(
    'previews %s through its URL without fetching or decoding bytes',
    (name) => {
      const file = fileOf(name);
      const { result } = renderHook(() => useSessionFilePreview(file));
      expect(result.current).toEqual({
        kind: 'text',
        status: 'ready',
        contentUrl: urlOf(file.sessionId, file.fileId),
        text: null,
        truncated: false,
        errorMessage: null,
      });
      expect(sessionFileService.fetchContentBlob).not.toHaveBeenCalled();
    },
  );

  it('uses MIME to preview an extensionless structured-text file without JS reads', () => {
    const { result } = renderHook(() =>
      useSessionFilePreview(fileOf('数据', 'application/vnd.api+json; charset=utf-8')),
    );
    expect(result.current).toMatchObject({ kind: 'text', status: 'ready', text: null });
    expect(sessionFileService.fetchContentBlob).not.toHaveBeenCalled();
  });

  it('switches URLs and clears preview when a file becomes unsupported or unselected', () => {
    const { result, rerender } = renderHook(
      ({ file }: { file: SessionFileView | null }) => useSessionFilePreview(file),
      {
        initialProps: { file: fileOf('说明.md') as SessionFileView | null },
      },
    );
    const other = { ...fileOf('数据.json'), fileId: 'file-2', sessionId: 'other:round-2' };
    rerender({ file: other });
    expect(result.current).toMatchObject({
      status: 'ready',
      contentUrl: urlOf(other.sessionId, other.fileId),
      text: null,
    });
    rerender({ file: fileOf('压缩包.zip') });
    expect(result.current).toMatchObject({ status: 'unsupported', contentUrl: null, text: null });
    rerender({ file: null });
    expect(result.current).toMatchObject({ status: 'unsupported', contentUrl: null });
    expect(sessionFileService.fetchContentBlob).not.toHaveBeenCalled();
  });

  it.each([
    ['图片.png', 'image'],
    ['报告.pdf', 'pdf'],
    ['网页.html', 'html'],
    ['视频.mp4', 'video'],
  ])('preserves browser loading for %s', (name, kind) => {
    const { result } = renderHook(() => useSessionFilePreview(fileOf(name)));
    expect(result.current).toMatchObject({ kind, status: 'ready', text: null });
    expect(sessionFileService.fetchContentBlob).not.toHaveBeenCalled();
  });
});

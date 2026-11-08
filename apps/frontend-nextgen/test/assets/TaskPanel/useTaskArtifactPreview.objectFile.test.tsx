/** @jest-environment jsdom */
import {
  downloadTaskArtifact,
  useTaskArtifactPreview,
} from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { downloadTaskArtifactContent } from '@/services/backendApi/tasks/taskController';
import { renderHook, waitFor } from '@testing-library/react';
import { beforeAll } from '@jest/globals';

jest.mock('@/services/backendApi/tasks/taskController', () => ({
  downloadTaskArtifactContent: jest.fn(),
}));

const mockedDownloadTaskArtifactContent =
  downloadTaskArtifactContent as jest.MockedFunction<typeof downloadTaskArtifactContent>;

const objectFileArtifact: TaskArtifactView = {
  id: 'art-obj-file',
  name: 'analysis-report.pdf',
  type: 'file',
  kind: 'file',
  updatedAt: '',
  fileRef: {
    storageType: 'object_file',
    storageId: 'task-artifacts/tk_20261027_0001/analysis-report.pdf',
    fileId: null,
    fileType: 'application/pdf',
  },
};

const context = { taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' };

beforeAll(() => {
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    writable: true,
    value: jest.fn().mockReturnValue('blob:object-file-bytes'),
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    writable: true,
    value: jest.fn(),
  });
});

beforeEach(() => {
  mockedDownloadTaskArtifactContent.mockReset();
  mockedDownloadTaskArtifactContent.mockResolvedValue(
    new Blob(['object-file-bytes'], { type: 'application/pdf' }),
  );
});

describe('object_file 产物预览', () => {
  it('走后端原始字节代理接口', async () => {
    const { result } = renderHook(() => useTaskArtifactPreview(objectFileArtifact, context));

    await waitFor(() => expect(result.current.status).toBe('ready'));

    expect(mockedDownloadTaskArtifactContent).toHaveBeenCalledWith('art-obj-file');
    expect(result.current.kind).toBe('pdf');
    expect(result.current.contentUrl).toBeTruthy();
  });
});

describe('object_file 产物下载', () => {
  it('走后端原始字节代理接口', async () => {
    await downloadTaskArtifact(objectFileArtifact, context);

    expect(mockedDownloadTaskArtifactContent).toHaveBeenCalledWith('art-obj-file');
  });
});

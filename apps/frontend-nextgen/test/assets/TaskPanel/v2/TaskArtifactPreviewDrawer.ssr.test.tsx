/** @jest-environment node */
import type { TaskArtifactPreviewState } from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { TaskArtifactPreviewDrawer } from '@/assets/TaskPanel/v2/TaskArtifactPreviewDrawer';
import { describe, expect, it } from '@jest/globals';
import type { jest as jestType } from '@jest/globals';
import { renderToString } from 'react-dom/server';

jest.mock('@/assets/TaskPanel/hooks/useTaskArtifactPreview', () => ({
  downloadTaskArtifact: jest.fn(),
  useTaskArtifactPreview: jest.fn(),
}));

const artifactHooks =
  require('@/assets/TaskPanel/hooks/useTaskArtifactPreview') as typeof import('@/assets/TaskPanel/hooks/useTaskArtifactPreview');
const mockedUsePreview = artifactHooks.useTaskArtifactPreview as jestType.MockedFunction<
  typeof artifactHooks.useTaskArtifactPreview
>;

const artifact: TaskArtifactView = {
  id: 'artifact-ssr',
  name: 'artifact.pdf',
  type: 'file',
  kind: 'file',
  updatedAt: '2026-10-20T10:00:00.000Z',
  fileRef: {
    storageType: 'session_file',
    storageId: 'session-1',
    fileType: 'application/pdf',
  },
};

describe('TaskArtifactPreviewDrawer SSR', () => {
  it('returns null without DOM', () => {
    mockedUsePreview.mockReturnValue({
      kind: 'pdf',
      status: 'ready',
      contentUrl: '/preview.pdf',
      text: null,
      truncated: false,
      size: 1024,
      errorMessage: null,
    } satisfies TaskArtifactPreviewState);

    const html = renderToString(
      <TaskArtifactPreviewDrawer
        artifact={artifact}
        context={{ taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' }}
        onClose={jest.fn()}
      />,
    );

    expect(html).toBe('');
  });
});

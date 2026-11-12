/** @jest-environment jsdom */
import React from 'react';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TaskArtifactsDrawer } from '@/pages/Lab/Tasks/TaskArtifactsDrawer';

const mockView = jest.fn((props: Record<string, unknown>) => <div data-testid="shared-artifacts-view">{String(props.taskId)}</div>);
const mockLegacyFetcher = jest.fn(() => <div>host-assembled-fetcher</div>);
jest.mock('@/components/Workspace/TaskPanelAssetBoundary', () => ({
  TaskPanelAssetBoundary: ({ children }: { children: (asset: unknown) => React.ReactNode }) => children({
    TaskArtifactsView: mockView, TaskPanelFetcher: mockLegacyFetcher,
  }),
}));
jest.mock('@/services/tasks/taskConfig', () => ({ TASK_API_BASE: 'https://api.example', TASK_READ_API_BASE: '/read/tasks' }));

beforeEach(() => jest.clearAllMocks());

describe('Lab task artifacts host shell', () => {
  it('mounts the complete asset view through the shared boundary with explicit owner and drawer portal', async () => {
    const onClose = jest.fn();
    const view = render(<TaskArtifactsDrawer taskId="t-1" ownerUserId="owner-not-viewer" onClose={onClose} />);
    expect(await screen.findByTestId('shared-artifacts-view')).toHaveTextContent('t-1');
    const dialog = screen.getByRole('dialog');
    await waitFor(() => expect(mockView).toHaveBeenLastCalledWith(expect.objectContaining({
      taskId: 't-1', ownerUserId: 'owner-not-viewer', apiBaseUrl: 'https://api.example', taskApiBase: '/read/tasks', previewPortalContainer: dialog,
    }), expect.anything()));
    expect(mockLegacyFetcher).not.toHaveBeenCalled();
    expect(screen.getByText('任务 ID：t-1')).toBeInTheDocument();
    view.rerender(<TaskArtifactsDrawer taskId="t-2" ownerUserId="other-owner" onClose={onClose} />);
    await waitFor(() => expect(mockView).toHaveBeenLastCalledWith(expect.objectContaining({ taskId: 't-2', ownerUserId: 'other-owner' }), expect.anything()));
    fireEvent.click(screen.getByRole('button', { name: '关闭任务产物抽屉' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not mount the asset when closed', () => {
    render(<TaskArtifactsDrawer taskId={null} ownerUserId="owner" onClose={jest.fn()} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mockView).not.toHaveBeenCalled();
  });
});

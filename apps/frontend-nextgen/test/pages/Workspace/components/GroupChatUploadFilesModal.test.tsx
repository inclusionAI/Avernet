/** @jest-environment jsdom */
import { UploadFilesModal } from '@/pages/Workspace/components/GroupChatPane/UploadFilesModal';
import type { UploadTask } from '@/pages/Workspace/hooks/useSessionFileUpload';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

jest.mock('@/components/ui', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
  Modal: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ModalContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ModalFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ModalHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ModalTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));
jest.mock('@/components/ui/Tooltip', () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

function createTask(overrides: Partial<UploadTask> = {}): UploadTask {
  return {
    localId: 'task-1',
    name: '需求说明.md',
    size: 1024,
    mime: 'text/markdown',
    phase: 'ready',
    progress: 100,
    fileId: 'file-1',
    file: new File(['content'], '需求说明.md', { type: 'text/markdown' }),
    ...overrides,
  };
}

describe('GroupChat UploadFilesModal', () => {
  it('选中文件后自动开始上传', () => {
    const stageFiles = jest.fn();
    const submitStaged = jest.fn(() => Promise.resolve());

    render(
      <UploadFilesModal
        open
        onClose={() => {}}
        queue={[]}
        stageFiles={stageFiles}
        submitStaged={submitStaged}
        cancelTask={() => Promise.resolve()}
        retryTask={() => Promise.resolve()}
        discardAll={() => Promise.resolve()}
        clearCompleted={() => {}}
        hasPending={() => false}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['content'], '需求说明.md', { type: 'text/markdown' });
    fireEvent.change(input, { target: { files: [file] } });

    expect(stageFiles).toHaveBeenCalledWith([file]);
    expect(submitStaged).toHaveBeenCalledTimes(1);
    expect(screen.getByText('选中文件后将自动上传')).toBeInTheDocument();
  });

  it.each(['uploading', 'ready'] as const)('%s 时不显示添加至会话按钮，仍可关闭窗口', async (phase) => {
    const onClose = jest.fn();
    const clearCompleted = jest.fn();
    const discardAll = jest.fn(() => Promise.resolve());
    render(
      <UploadFilesModal
        open
        onClose={onClose}
        queue={[createTask({ phase })]}
        stageFiles={() => {}}
        submitStaged={() => Promise.resolve()}
        cancelTask={() => Promise.resolve()}
        retryTask={() => Promise.resolve()}
        discardAll={discardAll}
        clearCompleted={clearCompleted}
        hasPending={() => phase === 'uploading'}
      />,
    );
    expect(screen.queryByRole('button', { name: /添加至会话|上传中/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(discardAll).toHaveBeenCalledTimes(phase === 'uploading' ? 1 : 0);
    expect(clearCompleted).toHaveBeenCalledTimes(phase === 'ready' ? 1 : 0);
  });
});

/** @jest-environment jsdom */
import { UploadFilesModal } from '@/pages/Workspace/components/GroupChatPane/UploadFilesModal';
import type { UploadTask } from '@/pages/Workspace/hooks/useSessionFileUpload';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
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
        isUploading={false}
        stageFiles={stageFiles}
        submitStaged={submitStaged}
        cancelTask={() => Promise.resolve()}
        retryTask={() => Promise.resolve()}
        discardAll={() => Promise.resolve()}
        clearCompleted={() => {}}
        hasPending={() => false}
        onAddToSession={() => {}}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['content'], '需求说明.md', { type: 'text/markdown' });
    fireEvent.change(input, { target: { files: [file] } });

    expect(stageFiles).toHaveBeenCalledWith([file]);
    expect(submitStaged).toHaveBeenCalledTimes(1);
    expect(screen.getByText('选中文件后将自动上传，完成后可添加至会话')).toBeInTheDocument();
  });

  it('使用添加至会话按钮触发引用回调，未完成上传时不可用', () => {
    const onAddToSession = jest.fn();
    const { rerender } = render(
      <UploadFilesModal
        open
        onClose={() => {}}
        queue={[createTask({ phase: 'staged', fileId: undefined, progress: 0 })]}
        isUploading={false}
        stageFiles={() => {}}
        submitStaged={() => Promise.resolve()}
        cancelTask={() => Promise.resolve()}
        retryTask={() => Promise.resolve()}
        discardAll={() => Promise.resolve()}
        clearCompleted={() => {}}
        hasPending={() => false}
        onAddToSession={onAddToSession}
      />,
    );
    const addButton = screen.getByRole('button', { name: '添加至会话' });
    expect(addButton).toBeDisabled();

    rerender(
      <UploadFilesModal
        open
        onClose={() => {}}
        queue={[createTask()]}
        isUploading={false}
        stageFiles={() => {}}
        submitStaged={() => Promise.resolve()}
        cancelTask={() => Promise.resolve()}
        retryTask={() => Promise.resolve()}
        discardAll={() => Promise.resolve()}
        clearCompleted={() => {}}
        hasPending={() => false}
        onAddToSession={onAddToSession}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '添加至会话' }));
    expect(onAddToSession).toHaveBeenCalledTimes(1);
  });
});

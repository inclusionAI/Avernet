/** @jest-environment jsdom */
import { BotUploadFilesModal } from '@/pages/Workspace/components/BotSessionFiles/UploadFilesModal';
import type { UploadTask } from '@/stores/botSessionFileStore';
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
    phase: 'ready',
    progress: 100,
    resourceId: 'resource-1',
    file: new File(['content'], '需求说明.md', { type: 'text/markdown' }),
    ...overrides,
  };
}

describe('BotUploadFilesModal', () => {
  it('选中文件后自动触发上传，不再需要点击上传按钮', () => {
    const stageFiles = jest.fn();
    const submit = jest.fn(() => Promise.resolve());

    render(
      <BotUploadFilesModal
        open
        onClose={() => {}}
        queue={[]}
        isUploading={false}
        stageFiles={stageFiles}
        submit={submit}
        onAddToSession={() => {}}
        removeTask={() => {}}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['content'], '需求说明.md', { type: 'text/markdown' });
    fireEvent.change(input, { target: { files: [file] } });

    expect(stageFiles).toHaveBeenCalledWith([file]);
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it('点击添加至会话后触发引用回调，未完成上传时按钮不可用', () => {
    const onAddToSession = jest.fn();
    const { rerender } = render(
      <BotUploadFilesModal
        open
        onClose={() => {}}
        queue={[createTask({ phase: 'staged', resourceId: undefined, progress: 0 })]}
        isUploading={false}
        stageFiles={() => []}
        submit={() => Promise.resolve()}
        onAddToSession={onAddToSession}
        removeTask={() => {}}
      />,
    );
    const addButton = screen.getByRole('button', { name: '添加至会话' });
    expect(addButton).toBeDisabled();

    rerender(
      <BotUploadFilesModal
        open
        onClose={() => {}}
        queue={[createTask()]}
        isUploading={false}
        stageFiles={() => []}
        submit={() => Promise.resolve()}
        onAddToSession={onAddToSession}
        removeTask={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '添加至会话' }));
    expect(onAddToSession).toHaveBeenCalledTimes(1);
  });
});

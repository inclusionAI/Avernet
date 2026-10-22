/** @jest-environment jsdom */
import { type TaskArtifactPreviewState, useTaskArtifactPreview } from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { TaskArtifactPreviewDrawer } from '@/assets/TaskPanel/v2/TaskArtifactPreviewDrawer';
import { beforeEach, describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

jest.mock('@/assets/TaskPanel/hooks/useTaskArtifactPreview', () => ({
  downloadTaskArtifact: jest.fn(),
  useTaskArtifactPreview: jest.fn(),
}));

const mockedUseTaskArtifactPreview = useTaskArtifactPreview as jest.MockedFunction<typeof useTaskArtifactPreview>;

const context = { taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' };

function artifact(name: string, fileType: string): TaskArtifactView {
  return {
    id: `artifact-${name}`,
    name,
    type: 'file',
    kind: 'file',
    updatedAt: '',
    fileRef: {
      storageType: 'bot_workspace',
      storageId: `workspace/${name}`,
      fileType,
    },
  };
}

function textPreview(overrides: Partial<TaskArtifactPreviewState> = {}): TaskArtifactPreviewState {
  return {
    kind: 'text',
    status: 'ready',
    contentUrl: null,
    text: '',
    truncated: false,
    size: 1024,
    errorMessage: null,
    ...overrides,
  };
}

describe('TaskArtifactPreviewDrawer 文本预览', () => {
  beforeEach(() => {
    mockedUseTaskArtifactPreview.mockReset();
  });

  it('Markdown 使用与单 Bot 会话资源文件一致的文档排版和滚动容器', () => {
    mockedUseTaskArtifactPreview.mockReturnValue(textPreview({ text: '# 分析报告\n\n结论包含 **关键发现**。' }));

    render(
      <TaskArtifactPreviewDrawer
        artifact={artifact('分析报告.md', 'text/markdown')}
        context={context}
        onClose={jest.fn()}
      />,
    );

    expect(screen.getByRole('heading', { name: '分析报告', level: 1 })).toBeInTheDocument();
    expect(screen.getByText('关键发现')).toBeInTheDocument();
    const markdown = document.querySelector('.markdown-body');
    expect(markdown).toHaveClass('app-scrollbar', 'overflow-y-auto', 'px-6', 'py-4', 'text-sm', 'leading-6');
  });

  it('普通文本使用等宽排版，并在内容区底部展示截断提示', () => {
    mockedUseTaskArtifactPreview.mockReturnValue(
      textPreview({ text: '2026-10-22 INFO task completed', truncated: true }),
    );

    render(
      <TaskArtifactPreviewDrawer artifact={artifact('task.log', 'text/plain')} context={context} onClose={jest.fn()} />,
    );

    const plainText = screen.getByTestId('task-artifact-plain-text');
    expect(plainText).toHaveTextContent('2026-10-22 INFO task completed');
    expect(plainText).toHaveClass(
      'app-scrollbar',
      'overflow-y-auto',
      'whitespace-pre-wrap',
      'break-all',
      'px-6',
      'py-4',
      'font-mono',
      'text-xs',
      'leading-6',
    );
    expect(screen.getByText('内容较长，仅显示部分，请下载查看完整内容。')).toBeInTheDocument();
  });
});

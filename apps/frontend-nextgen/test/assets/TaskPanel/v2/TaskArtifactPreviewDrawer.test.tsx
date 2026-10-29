/** @jest-environment jsdom */
import { type TaskArtifactPreviewState, useTaskArtifactPreview } from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { TaskArtifactPreviewDrawer } from '@/assets/TaskPanel/v2/TaskArtifactPreviewDrawer';
import { beforeEach, describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen, within } from '@testing-library/react';

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
    mockedUseTaskArtifactPreview.mockReturnValue({
      kind: 'other',
      status: 'unsupported',
      contentUrl: null,
      text: null,
      truncated: false,
      size: null,
      errorMessage: null,
    });
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

  it('纯文本产物不展示下载入口，保留关闭入口', () => {
    mockedUseTaskArtifactPreview.mockReturnValue(textPreview({ text: '纯文本产物内容' }));

    render(
      <TaskArtifactPreviewDrawer
        artifact={{
          id: 'artifact-text',
          name: '执行摘要',
          type: 'document',
          kind: 'text',
          summary: '纯文本产物内容',
          updatedAt: '',
        }}
        context={context}
        onClose={jest.fn()}
      />,
    );

    expect(screen.getByTestId('task-artifact-plain-text')).toHaveTextContent('纯文本产物内容');
    expect(screen.queryByRole('button', { name: '下载文件' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument();
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

describe('TaskArtifactPreviewDrawer 链接与代码详情', () => {
  beforeEach(() => {
    mockedUseTaskArtifactPreview.mockReset();
    mockedUseTaskArtifactPreview.mockReturnValue({
      kind: 'other',
      status: 'unsupported',
      contentUrl: null,
      text: null,
      truncated: false,
      size: null,
      errorMessage: null,
    });
  });

  it('链接产物展示链接类型、地址、产出者和创建时间', () => {
    render(
      <TaskArtifactPreviewDrawer
        artifact={{
          id: 'artifact-link',
          name: '需求说明书',
          type: 'link',
          kind: 'url_link',
          url: 'https://docs.example/requirement',
          linkRef: { url: 'https://docs.example/requirement', type: '需求文档' },
          owner: 'group-1',
          updatedAt: '2026-10-23T10:00:00.000Z',
        }}
        context={context}
        onClose={jest.fn()}
      />,
    );

    const details = screen.getByTestId('task-artifact-link-details');
    expect(within(details).getByText('需求文档')).toBeInTheDocument();
    expect(within(details).getByRole('link', { name: 'https://docs.example/requirement' })).toHaveAttribute(
      'href',
      'https://docs.example/requirement',
    );
    expect(within(details).getByText('group-1')).toBeInTheDocument();
    expect(within(details).getByText('2026-10-23 18:00')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '打开链接' })).toHaveAttribute(
      'href',
      'https://docs.example/requirement',
    );
    expect(screen.queryByRole('button', { name: '下载文件' })).not.toBeInTheDocument();
  });

  it('代码产物展示代码库、PR 链接和 PR 描述', () => {
    render(
      <TaskArtifactPreviewDrawer
        artifact={{
          id: 'artifact-code',
          name: '实现 PR',
          type: 'other',
          kind: 'code',
          url: 'https://git.example/avernet/pull/1',
          summary: '实现任务产物预览',
          codeRef: {
            codeBaseName: 'avernet',
            codeBaseAddress: 'https://git.example/avernet',
            pullRequestUrl: 'https://git.example/avernet/pull/1',
            description: '实现任务产物预览',
          },
          owner: 'bot-coder',
          updatedAt: '2026-10-23T11:00:00.000Z',
        }}
        context={context}
        onClose={jest.fn()}
      />,
    );

    const details = screen.getByTestId('task-artifact-code-details');
    expect(within(details).getByText('avernet')).toBeInTheDocument();
    expect(within(details).getByRole('link', { name: 'https://git.example/avernet' })).toHaveAttribute(
      'href',
      'https://git.example/avernet',
    );
    expect(within(details).getByRole('link', { name: 'https://git.example/avernet/pull/1' })).toHaveAttribute(
      'href',
      'https://git.example/avernet/pull/1',
    );
    expect(within(details).getByText('实现任务产物预览')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看 PR' })).toHaveAttribute(
      'href',
      'https://git.example/avernet/pull/1',
    );
    expect(screen.queryByRole('button', { name: '下载文件' })).not.toBeInTheDocument();
  });
});

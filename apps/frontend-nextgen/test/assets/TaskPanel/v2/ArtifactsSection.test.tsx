/** @jest-environment jsdom */
// 产物卡列表时间必须显示北京时间;代码/链接产物仅保留外链跳转入口。
import { ArtifactsSection } from '@/assets/TaskPanel/v2/ArtifactsSection';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import type { TaskArtifactAccessContext } from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import { beforeEach, describe, expect, it } from '@jest/globals';
import type { jest as jestType } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/assets/TaskPanel/hooks/useTaskArtifactPreview', () => ({
  downloadTaskArtifact: jest.fn(),
}));

const artifactHooks =
  require('@/assets/TaskPanel/hooks/useTaskArtifactPreview') as typeof import('@/assets/TaskPanel/hooks/useTaskArtifactPreview');
const mockedDownload = artifactHooks.downloadTaskArtifact as jestType.MockedFunction<
  typeof artifactHooks.downloadTaskArtifact
>;

const context: TaskArtifactAccessContext = {
  taskId: 'task-1',
  ownerBotId: 'bot-1',
  ownerUserId: 'user-1',
};

const baseArtifact: TaskArtifactView = {
  id: 'artifact-utc',
  name: '分析报告',
  type: 'document',
  kind: 'text',
  summary: '北京时间产物',
  owner: '张三',
  updatedAt: '2026-10-23T10:00:00.000Z',
  fileRef: null,
  nodeId: null,
};

const fileArtifact = (id: string, fileRef: TaskArtifactView['fileRef']): TaskArtifactView => ({
  ...baseArtifact,
  id,
  name: `${id}.pdf`,
  type: 'file',
  kind: 'file',
  summary: null,
  fileRef,
});

const renderSection = (artifacts: TaskArtifactView[]) => render(
  <ArtifactsSection artifacts={artifacts} apiBaseUrl="" context={context} />,
);

describe('ArtifactsSection', () => {
  beforeEach(() => {
    mockedDownload.mockReset();
  });

  it('产物时间按北京时间显示', () => {
    renderSection([baseArtifact]);
    expect(screen.getByText('张三 · 2026-10-23 18:00')).toBeInTheDocument();
  });

  it('下载失败时展示 Error 的错误消息', async () => {
    mockedDownload.mockRejectedValueOnce(new Error('资源不存在'));
    renderSection([
      fileArtifact('download-error', {
        storageType: 'session_file',
        storageId: 'session-1',
        fileId: 'file-1',
        fileType: 'application/pdf',
      }),
    ]);

    fireEvent.click(screen.getByRole('button', { name: '下载产物 download-error.pdf' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('资源不存在');
  });

  it('下载抛出非 Error 时展示兜底错误消息', async () => {
    mockedDownload.mockRejectedValueOnce('network failure');
    renderSection([
      fileArtifact('download-failure', {
        storageType: 'session_file',
        storageId: 'session-2',
        fileId: 'file-2',
        fileType: 'application/pdf',
      }),
    ]);

    fireEvent.click(screen.getByRole('button', { name: '下载产物 download-failure.pdf' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('产物下载失败，请稍后重试。');
  });

  it('无预览动作且存在外链时只渲染外链图标', () => {
    renderSection([
      { ...baseArtifact, id: 'legacy-url', name: 'legacy-url', kind: null, type: 'other', url: 'https://example.com/legacy-url' },
    ]);

    expect(screen.queryByRole('button', { name: /预览产物/ })).toBeNull();
    expect(screen.getByTitle('打开产物:legacy-url')).toHaveAttribute('href', 'https://example.com/legacy-url');
  });

  it('未知 kind、legacy type 与无外链数据时回退原始标识且不渲染操作区', () => {
    renderSection([
      { ...baseArtifact, id: 'unknown-kind', kind: 'magic' as unknown as TaskArtifactView['kind'], type: 'other' },
      { ...baseArtifact, id: 'unknown-type', kind: null, type: 'legacy_report' } as unknown as TaskArtifactView,
    ]);

    expect(screen.getByText('magic')).toBeInTheDocument();
    expect(screen.getByText('legacy_report')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /预览产物/ })).toBeNull();
    expect(screen.queryByRole('link', { name: /跳转产物/ })).toBeNull();
  });
});

describe('ArtifactsSection 代码/链接产物操作', () => {
  const baseArtifact: TaskArtifactView = {
    id: 'artifact-external',
    name: '外部产物',
    type: 'other',
    kind: 'code',
    summary: null,
    owner: '张三',
    updatedAt: '2026-10-23T10:00:00.000Z',
    fileRef: null,
    nodeId: null,
  };

  it('代码/链接产物不展示预览按钮,只保留跳转按钮', () => {
    renderSection([
      { ...baseArtifact, id: 'artifact-code', kind: 'code', url: 'https://example.com/pr/1' },
      { ...baseArtifact, id: 'artifact-link', kind: 'url_link', url: 'https://example.com/link' },
      { ...baseArtifact, id: 'artifact-legacy-link', type: 'link', kind: null, url: 'https://example.com/legacy' },
    ]);

    expect(screen.queryByRole('button', { name: /预览产物/ })).toBeNull();
    const jumps = screen.getAllByRole('link', { name: /跳转产物/ });
    expect(jumps).toHaveLength(3);
    expect(jumps[0]).toHaveAttribute('href', 'https://example.com/pr/1');
    expect(jumps[1]).toHaveAttribute('href', 'https://example.com/link');
    expect(jumps[2]).toHaveAttribute('href', 'https://example.com/legacy');
  });
});

/** @vitest-environment jsdom */
// 产物卡列表时间必须显示北京时间;代码/链接产物仅保留外链跳转入口。
import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { TaskArtifactView } from '../../../src/panel/types';
import { ArtifactsSection } from '../../../src/panel/v2/ArtifactsSection';

const artifact: TaskArtifactView = {
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

describe('ArtifactsSection', () => {
  it('产物时间按北京时间显示', () => {
    render(
      <ArtifactsSection
        artifacts={[artifact]}
        apiBaseUrl=""
        context={{ taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' }}
      />,
    );

    expect(screen.getByText('张三 · 2026-10-23 18:00')).toBeInTheDocument();
  });
});

describe('ArtifactsSection 最终产物标识', () => {
  const finalArtifact: TaskArtifactView = {
    id: 'artifact-final',
    name: '第一棒最终产物',
    type: 'document',
    kind: 'text',
    stage: 1,
    isFinalArtifact: true,
    updatedAt: '2026-10-23T10:00:00.000Z',
    fileRef: null,
    nodeId: 'n-1',
  };
  const normalArtifact: TaskArtifactView = {
    ...finalArtifact,
    id: 'artifact-normal',
    name: '第二棒普通产物',
    stage: 2,
    isFinalArtifact: false,
  };

  it('仅后端标记的最终产物显示标识，普通产物使用真实节点来源而非边框高亮', () => {
    render(
      <ArtifactsSection
        artifacts={[finalArtifact, normalArtifact]}
        apiBaseUrl=""
        context={{ taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' }}
      />,
    );

    const finalRow = screen.getByText('第一棒最终产物').closest('li')!;
    const normalRow = screen.getByText('第二棒普通产物').closest('li')!;
    expect(finalRow).toHaveTextContent('最终产物');
    expect(finalRow).not.toHaveTextContent('第1棒产物');
    expect(normalRow).toHaveTextContent('节点 n-1');
    expect(finalRow).toHaveAttribute('data-final-artifact', 'true');
    expect(finalRow).toHaveClass('task-artifact-row');
    expect(normalRow).not.toHaveAttribute('data-final-artifact');
    expect(normalRow).toHaveClass('task-artifact-row');
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
    render(
      <ArtifactsSection
        artifacts={[
          { ...baseArtifact, id: 'artifact-code', kind: 'code', url: 'https://example.com/pr/1' },
          { ...baseArtifact, id: 'artifact-link', kind: 'url_link', url: 'https://example.com/link' },
          { ...baseArtifact, id: 'artifact-legacy-link', type: 'link', kind: null, url: 'https://example.com/legacy' },
        ]}
        apiBaseUrl=""
        context={{ taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' }}
      />,
    );

    expect(screen.queryByRole('button', { name: /预览产物/ })).toBeNull();
    const jumps = screen.getAllByRole('link', { name: /跳转产物/ });
    expect(jumps).toHaveLength(3);
    expect(jumps[0]).toHaveAttribute('href', 'https://example.com/pr/1');
    expect(jumps[1]).toHaveAttribute('href', 'https://example.com/link');
    expect(jumps[2]).toHaveAttribute('href', 'https://example.com/legacy');
  });
});

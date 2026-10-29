/** @jest-environment jsdom */
// 产物卡列表时间必须显示北京时间;代码/链接产物仅保留外链跳转入口。
import { ArtifactsSection } from '@/assets/TaskPanel/v2/ArtifactsSection';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

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

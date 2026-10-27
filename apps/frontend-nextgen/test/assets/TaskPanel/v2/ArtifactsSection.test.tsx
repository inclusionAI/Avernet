/** @jest-environment jsdom */
// 产物卡列表时间必须显示北京时间,而不是把 ISO 串中的 UTC 时间直接截断展示。
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

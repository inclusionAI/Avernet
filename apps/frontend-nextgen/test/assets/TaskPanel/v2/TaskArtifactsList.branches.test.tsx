/** @jest-environment jsdom */
// TaskArtifactsList 的聚合去重与可选 prop 分支。
import { TaskArtifactsList, collectTaskArtifacts } from '@/assets/TaskPanel/v2/TaskArtifactsList';
import type { TaskNodeView, TaskView } from '@/assets/TaskPanel/types';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';

const node = (id: string, artifactIds: string[]): TaskNodeView =>
  ({
    artifacts: artifactIds.map((artifactId) => ({
      id: artifactId,
      name: `产物 ${artifactId}`,
      type: 'document',
      kind: 'text',
      summary: '节点产物',
      owner: 'bot-1',
      updatedAt: '2026-10-20T10:00:00.000Z',
    })),
    hasSubTask: false,
    stepTraces: [],
    id,
    name: `节点 ${id}`,
    sequence: 1,
    status: 'done',
  }) as TaskNodeView;

const task = {
  acceptances: ['交付产物'],
  artifacts: [
    {
      id: 'artifact-1',
      name: '产物 artifact-1',
      type: 'document' as const,
      kind: 'text' as const,
      owner: 'bot-1',
      updatedAt: '2026-10-20T10:00:00.000Z',
    },
    {
      id: 'artifact-task',
      name: '任务级产物',
      type: 'document' as const,
      kind: 'text' as const,
      owner: 'bot-2',
      updatedAt: '2026-10-20T11:00:00.000Z',
    },
  ],
  createdAt: '2026-10-20T10:00:00.000Z',
  dagEdges: [],
  dagNodes: [],
  description: '任务描述',
  finishedAt: null,
  goal: '任务目标',
  loopRound: 1,
  name: '任务',
  needsAttention: false,
  nodes: [node('node-1', ['artifact-1', 'artifact-2'])],
  objective: '任务目标',
  ownerBotId: 'bot-owner',
  ownerBotName: 'Owner Bot',
  ownerUserId: 'user-1',
  progress: {
    total: 1,
    pending: 0,
    planning: 0,
    running: 0,
    done: 1,
    failed: 0,
    hung: 0,
    skipped: 0,
    percent: 100,
  },
  sourceLabel: 'Bot',
  status: 'DONE' as const,
  taskType: 'dynamic',
  taskTypeLabel: '动态',
} as unknown as TaskView;

describe('TaskArtifactsList', () => {
  it('汇总节点与任务产物并按 ID 去重', () => {
    const artifacts = collectTaskArtifacts(task);

    expect(artifacts.map((artifact) => artifact.id)).toEqual(['artifact-1', 'artifact-2', 'artifact-task']);
  });

  it('默认使用产物标题并启用群展示富化', () => {
    render(<TaskArtifactsList task={task} apiBaseUrl="" />);

    expect(screen.getByText('产物')).toBeInTheDocument();
    expect(screen.getByText('3 项')).toBeInTheDocument();
  });

  it('支持自定义标题并关闭同屏重复群富化', () => {
    render(
      <TaskArtifactsList
        task={task}
        apiBaseUrl="/api"
        title="节点产物区"
        previewPortalContainer={document.body}
        enrichGroupDisplay={false}
      />,
    );

    expect(screen.getByText('节点产物区')).toBeInTheDocument();
  });
});

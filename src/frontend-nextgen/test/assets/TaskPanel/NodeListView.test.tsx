/** @jest-environment jsdom */
import { NodeListView } from '@/assets/TaskPanel/NodeListView';
import type { TaskNodeView } from '@/assets/TaskPanel/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const rootNode: TaskNodeView = {
  id: 'task-root',
  name: '根节点',
  sequence: 1,
  status: 'running',
  executor: null,
  executorColor: null,
  runMode: 'single_bot',
  sessionId: 'session-root',
  assignee: 'root-bot',
  hasSubTask: false,
  subTaskId: null,
  stepTraces: [],
  acceptanceResult: null,
  artifacts: [],
};

describe('NodeListView root session drill-down', () => {
  it('根节点没有 executor/name 时仍能通过 assignee 打开下钻 tab', () => {
    const onOpenGroupSession = jest.fn();
    render(
      <NodeListView
        nodes={[rootNode]}
        ownerBotId="root-bot"
        onViewNodeDetail={jest.fn()}
        onOpenGroupSession={onOpenGroupSession}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '查看执行会话 root-bot' }));
    expect(onOpenGroupSession).toHaveBeenCalledWith(rootNode);
  });
});

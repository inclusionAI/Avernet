/** @jest-environment jsdom */
import { ViewSessionButton } from '@/pages/MyTask/components/ViewSessionButton';
import type { TaskListItem } from '@/domain/tasks/models';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { history } from '@umijs/max';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

const record = {
  task_id: 'task-clicked',
  source_type: 'bot',
  owner_bot_id: 'bot-1:u1',
  owner_user_id: 'u1',
  execution_config: { task_type: 'dynamic', main_session_id: 'sess-1' },
  status: 'EXECUTING',
} as TaskListItem;

describe('ViewSessionButton', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useTaskSidePanelStore.getState().resetAll();
  });

  it('进入对话先记录点击的任务作为目标会话的 TaskPanel 焦点', () => {
    render(<ViewSessionButton record={record} label="进入对话" />);
    fireEvent.click(screen.getByRole('button', { name: '进入对话' }));

    expect(useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId).toBe('task-clicked');
    expect(history.push).toHaveBeenCalledWith('/workspace/chat?bot=bot-1%3Au1%3Au1&session=sess-1');
  });
});

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
  owner_bot_id: 'bot-1',
  owner_user_id: 'creator-id',
  execution_config: { task_type: 'dynamic', main_session_id: 'sess-1' },
  status: 'EXECUTING',
} as TaskListItem;

describe('ViewSessionButton', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useTaskSidePanelStore.getState().resetAll();
  });

  it('进入对话使用 Bot 实际 owner，而不是任务创建人', () => {
    render(<ViewSessionButton record={record} label="进入对话" ownerBotUserIdMap={{ 'bot-1': 'actual-owner-id' }} />);
    fireEvent.click(screen.getByRole('button', { name: '进入对话' }));

    expect(useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId).toBe('task-clicked');
    expect(history.push).toHaveBeenCalledWith('/workspace/chat?bot=bot-1%3Aactual-owner-id&session=sess-1');
    expect(history.push).not.toHaveBeenCalledWith(expect.stringContaining('creator-id'));
  });

  it('复合 owner_bot_id 优先按完整 ID 查 owner，且不会重复拼接 owner', () => {
    render(
      <ViewSessionButton
        record={{ ...record, task_id: 'task-composite', owner_bot_id: 'bot-1:legacy-creator-id' }}
        label="进入对话"
        ownerBotUserIdMap={{
          'bot-1:legacy-creator-id': 'actual-owner-id',
          'bot-1': 'actual-owner-id',
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '进入对话' }));

    expect(history.push).toHaveBeenCalledWith('/workspace/chat?bot=bot-1%3Aactual-owner-id&session=sess-1');
    expect(history.push).not.toHaveBeenCalledWith(
      '/workspace/chat?bot=bot-1%3Alegacy-creator-id%3Aactual-owner-id&session=sess-1',
    );
  });

  it('复合 owner_bot_id 已携带实际 owner 时不依赖 Bot 目录', () => {
    render(
      <ViewSessionButton
        record={{ ...record, task_id: 'task-embedded-owner', owner_bot_id: 'bot-1:actual-owner-id' }}
        label="进入对话"
        ownerBotUserIdMap={{}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '进入对话' }));

    expect(history.push).toHaveBeenCalledWith('/workspace/chat?bot=bot-1%3Aactual-owner-id&session=sess-1');
  });

  it('裸 owner_bot_id 无法解析 Bot 实际 owner 时隐藏单 Bot 会话入口', () => {
    render(<ViewSessionButton record={record} label="进入对话" ownerBotUserIdMap={{}} />);
    expect(screen.queryByRole('button', { name: '进入对话' })).not.toBeInTheDocument();
    expect(history.push).not.toHaveBeenCalled();
  });
});

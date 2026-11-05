/** @jest-environment jsdom */
import { RoutineRunConversation } from '@/components/ScheduledTasks/RoutineRunConversation';
import { MyTaskRoutineDrawer } from '@/pages/MyTask/components/MyTaskRoutineDrawer';
import type { RoutineRun } from '@/services/botScheduledTasks/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

const base: RoutineRun = { id: 'run', routineId: 'job', status: 'ok', runtime_stage: 'online', device_uuid: 'instance-a', operator_id: 'caller-a' };
const open = () => fireEvent.click(screen.getByRole('button', { name: '查看会话' }));

test('shows ordered messages and partial execution errors, closes and reopens without requests', () => {
  render(<RoutineRunConversation run={{ ...base, input: 'fallback input', output: 'fallback output', error: 'partial failure', messages: [
    { role: 'user', content: 'historical question' }, { role: 'assistant', content: 'historical answer' },
  ] }} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  open();
  const dialog = screen.getByRole('dialog', { name: '执行会话详情' });
  expect(within(dialog).getByText('historical question')).toBeInTheDocument();
  expect(within(dialog).getByText('historical answer')).toBeInTheDocument();
  expect(within(dialog).getByText('partial failure')).toBeInTheDocument();
  expect(within(dialog).queryByText('fallback output')).not.toBeInTheDocument();
  expect(dialog).toHaveTextContent('运行实例：instance-a');
  expect(dialog).toHaveTextContent('Caller：caller-a');
  fireEvent.click(screen.getByRole('button', { name: '关闭执行会话' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  open();
  expect(screen.getByText('historical question')).toBeInTheDocument();
});

test('falls back to historical input/output, rendering HTML as text rather than executable markup', () => {
  const output = '<img src=x onerror=alert(1)>\nanswer';
  render(<RoutineRunConversation run={{ ...base, input: 'historical prompt', output }} />);
  open();
  const dialog = screen.getByRole('dialog');
  expect(dialog).toHaveTextContent('historical prompt');
  expect(dialog).toHaveTextContent('<img src=x onerror=alert(1)>');
  expect(dialog.querySelector('img')).toBeNull();
});

test('older backend or missing content shows an explicit empty state instead of inventing a transcript', () => {
  render(<RoutineRunConversation run={base} />);
  open();
  expect(screen.getByText('未报告会话内容')).toBeInTheDocument();
});

test('duplicate execution IDs on different instances open the selected row content', () => {
  render(<>
    <RoutineRunConversation run={{ ...base, output: 'answer-a' }} />
    <RoutineRunConversation run={{ ...base, device_uuid: 'instance-b', operator_id: 'caller-b', output: 'answer-b' }} />
  </>);
  fireEvent.click(screen.getAllByRole('button', { name: '查看会话' })[1]);
  const dialog = screen.getByRole('dialog');
  expect(dialog).toHaveTextContent('answer-b');
  expect(dialog).toHaveTextContent('instance-b');
  expect(dialog).toHaveTextContent('caller-b');
  expect(dialog).not.toHaveTextContent('answer-a');
});

test('MyTask instance details expose the same read-only execution conversation', () => {
  render(<MyTaskRoutineDrawer selectedRoutineKey="job" onCloseRoutine={jest.fn()} botNameMap={{}}
    selectedRoutine={{ id: 'job', botId: 'bot', botName: 'Bot', name: 'Daily', model: 'default', frequency: 'daily', raw: {} }}
    selectedRoutineRunsLoading={false} selectedRoutineRunsError={null}
    selectedRoutineRuns={[{ id: 'run', botId: 'bot', routineId: 'job', routineName: 'Daily', instanceNo: 'run',
      status: 'ok', raw: {}, conversation: { ...base, output: 'MyTask historical answer' } }]} />);
  open();
  expect(screen.getByRole('dialog', { name: '执行会话详情' })).toHaveTextContent('MyTask historical answer');
});

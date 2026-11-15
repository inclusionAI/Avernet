/** @jest-environment jsdom */
import { RoutineActions } from '@/pages/BotWorkshop/ScheduledTasks/RoutineActions';
import { RoutineDetail } from '@/pages/BotWorkshop/ScheduledTasks/RoutineDetail';
import { RoutineList } from '@/pages/BotWorkshop/ScheduledTasks/RoutineList';
import { RoutineNextRun } from '@/pages/BotWorkshop/ScheduledTasks/RoutineNextRun';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const state = {
  context: { can_manage: true, bot_type: 'personal' },
  editable: true,
  busy: false,
  run: jest.fn(),
  toggle: jest.fn(),
  remove: jest.fn(),
} as any;
const props = { state, onEdit: jest.fn(), onDeleted: jest.fn(), operate: jest.fn() };
const task = { id: '1', name: '任务', enabled: false, cron: '* * * * *', timezone: 'Asia/Shanghai' } as any;

beforeEach(() => jest.clearAllMocks());

test('停用任务显示先启用说明，不触发执行或启停', () => {
  render(<RoutineActions {...props} task={task} />);
  expect(screen.getByText('任务已停用，请先启用任务')).toBeInTheDocument();
  const run = screen.getByRole('button', { name: '立即执行：任务已停用，请先启用任务' });
  expect(run).toBeDisabled();
  fireEvent.click(run);
  expect(props.operate).not.toHaveBeenCalled();
  expect(state.run).not.toHaveBeenCalled();
  expect(state.toggle).not.toHaveBeenCalled();
});

test('未知状态不冒充停用，也不能发起启停或触发', () => {
  const unknown = { ...task, enabled: undefined };
  render(
    <>
      <RoutineActions {...props} task={unknown} />
      <RoutineNextRun task={unknown} now={0} />
    </>,
  );
  expect(screen.getByText('状态未知，请刷新后重试')).toBeInTheDocument();
  expect(screen.getAllByText('状态未知').length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: '立即执行：状态未知，请刷新后重试' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '状态未知' })).toBeDisabled();
  expect(props.operate).not.toHaveBeenCalled();
});

test('已启用任务保留立即执行入口', () => {
  render(<RoutineActions {...props} task={{ ...task, enabled: true }} />);
  expect(screen.getByRole('button', { name: '立即执行' })).toBeEnabled();
  expect(screen.queryByText('任务已停用，请先启用任务')).not.toBeInTheDocument();
});

test('列表和详情均不把缺失状态显示成停用', async () => {
  const unknown = { ...task, key: '1', command: '', enabled: undefined };
  const context = { bot_id: 'bot', bot_type: 'personal' } as any;
  const client = {
    detail: jest.fn().mockResolvedValue(unknown),
    runs: jest.fn().mockResolvedValue({ items: [], total: 0, failed_targets: [] }),
  } as any;
  const view = render(
    <RoutineList
      tasks={[unknown]}
      total={1}
      page={1}
      pageSize={10}
      stage={undefined}
      showStageFilter={false}
      keyword=""
      canManage={false}
      editable={true}
      busy={false}
      incomplete={false}
      onKeyword={jest.fn()}
      onStage={jest.fn()}
      onPage={jest.fn()}
      onPageSize={jest.fn()}
      onCreate={jest.fn()}
      onSelect={jest.fn()}
      actions={() => null}
    />,
  );
  expect(screen.getAllByText('状态未知')).toHaveLength(2);
  expect(screen.queryByText('已停用')).not.toBeInTheDocument();
  view.unmount();
  render(<RoutineDetail task={unknown} context={context} client={client} revision={0} actions={() => null} />);
  expect((await screen.findAllByText('状态未知')).length).toBe(2);
  expect(screen.queryByText('已停用')).not.toBeInTheDocument();
});

test('启用任务确认后按目标执行，忙碌和无权限时不执行', async () => {
  const target = { ...task, enabled: true, runtime_stage: 'verify', device_uuid: 'device' };
  state.run.mockResolvedValue({ success: true });
  const operate = jest.fn(async (work: () => Promise<unknown>) => {
    await work();
  });
  const view = render(<RoutineActions {...props} operate={operate} task={target} />);
  fireEvent.click(screen.getByRole('button', { name: '立即执行' }));
  fireEvent.click(screen.getByRole('button', { name: '立即执行', hidden: false }));
  expect(operate).toHaveBeenCalledTimes(1);
  expect(state.run).toHaveBeenCalledWith(target);
  view.unmount();
  const busy = render(<RoutineActions {...props} task={target} state={{ ...state, busy: true }} />);
  expect(screen.getByRole('button', { name: '立即执行' })).toBeDisabled();
  busy.unmount();
  render(
    <RoutineActions {...props} task={target} state={{ ...state, context: { ...state.context, can_manage: false } }} />,
  );
  expect(screen.queryByRole('button', { name: '立即执行' })).not.toBeInTheDocument();
});

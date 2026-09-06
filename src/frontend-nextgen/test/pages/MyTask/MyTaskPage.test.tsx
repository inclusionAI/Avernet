/** @jest-environment jsdom */
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useWorkIdentityAccess, type WorkIdentityAccess } from '@/hooks/useWorkIdentityAccess';
import MyTaskPage from '@/pages/MyTask/MyTaskPage';
import { useMyTaskTasks } from '@/pages/MyTask/hooks/useMyTaskTasks';
import { useRoutineTasks } from '@/pages/MyTask/hooks/useRoutineTasks';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import '@testing-library/jest-dom';
import { act, render, screen } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/hooks/useWorkIdentityAccess', () => ({ useWorkIdentityAccess: jest.fn() }));
jest.mock('@/pages/Workspace/hooks/useOwnedBots', () => ({ useOwnedBots: jest.fn() }));
jest.mock('@/pages/MyTask/hooks/useMyTaskTasks', () => ({ useMyTaskTasks: jest.fn() }));
jest.mock('@/pages/MyTask/hooks/useRoutineTasks', () => ({
  ALL_ROUTINE_BOT_VALUE: '__all__',
  makeRoutineKey: (botId: string, routineId: string, stage?: string) =>
    stage ? `${botId}::${routineId}::${stage}` : `${botId}::${routineId}`,
  useRoutineTasks: jest.fn(),
}));
jest.mock('@/pages/MyTask/components/UserTaskTab', () => ({
  UserTaskTab: () => <div data-testid="user-task-tab">用户任务内容</div>,
}));
jest.mock('@/pages/MyTask/components/RoutineTaskTab', () => ({
  RoutineTaskTab: ({ selectedBotId, botOptions, showBotSelector }: Record<string, unknown>) => (
    <div
      data-testid="routine-task-tab"
      data-selected-bot-id={String(selectedBotId)}
      data-bot-options={JSON.stringify(botOptions)}
      data-show-bot-selector={String(showBotSelector)}
    >
      定时任务内容
    </div>
  ),
}));
jest.mock('@/pages/MyTask/components/MyTaskDrawers', () => ({ MyTaskDrawers: () => null }));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;
const mockedUseWorkIdentityAccess = useWorkIdentityAccess as jest.MockedFunction<typeof useWorkIdentityAccess>;
const mockedUseOwnedBots = useOwnedBots as jest.MockedFunction<typeof useOwnedBots>;
const mockedUseMyTaskTasks = useMyTaskTasks as jest.MockedFunction<typeof useMyTaskTasks>;
const mockedUseRoutineTasks = useRoutineTasks as jest.MockedFunction<typeof useRoutineTasks>;

const emptyUserTasks = {
  taskRecords: [],
  total: 0,
  loading: false,
  error: null,
  refresh: jest.fn(),
};

const emptyRoutineTasks = {
  routines: [],
  loading: false,
  error: null,
  total: 0,
  refreshRoutines: jest.fn(),
  selectedRoutine: null,
  selectedRoutineRuns: [],
  selectedRoutineRunsLoading: false,
  selectedRoutineRunsError: null,
  historyRuns: [],
  historyLoading: false,
  historyError: null,
  runRoutine: jest.fn(),
};

function access(activeIdentity: WorkIdentityAccess['activeIdentity']): WorkIdentityAccess {
  return {
    activeIdentity,
    activeIdentityKind: activeIdentity?.kind ?? null,
    canViewPublicGroups: activeIdentity?.kind !== 'bot',
  };
}

describe('MyTaskPage work identity visibility', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '327325', displayName: '当前用户', online: true },
      status: 'ready',
    });
    mockedUseOwnedBots.mockReturnValue({
      chatBots: [],
      hasAgentCodingBots: false,
      isLoading: false,
      error: null,
      reload: jest.fn(),
    });
    mockedUseMyTaskTasks.mockReturnValue(emptyUserTasks);
    mockedUseRoutineTasks.mockReturnValue(emptyRoutineTasks);
  });

  it('用户工作身份只展示用户任务，并禁止定时任务请求', () => {
    mockedUseWorkIdentityAccess.mockReturnValue(
      access({ id: 'human_327325', kind: 'user', displayName: '当前用户', online: true }),
    );

    render(<MyTaskPage />);

    expect(screen.getByTestId('user-task-tab')).toBeInTheDocument();
    expect(screen.queryByTestId('routine-task-tab')).not.toBeInTheDocument();
    expect(screen.queryByText('定时任务')).not.toBeInTheDocument();
    expect(mockedUseMyTaskTasks).toHaveBeenLastCalledWith('327325', 1, 10, 'all', true);
    expect(mockedUseRoutineTasks.mock.calls.at(-1)?.at(-1)).toBe(false);
  });

  it('Bot 工作身份只展示当前 Bot 的定时任务，并禁止用户任务请求', () => {
    mockedUseWorkIdentityAccess.mockReturnValue(
      access({ id: 'bot-123:327325', kind: 'bot', displayName: '当前 Bot', online: true }),
    );

    render(<MyTaskPage />);

    expect(screen.queryByTestId('user-task-tab')).not.toBeInTheDocument();
    const routineTab = screen.getByTestId('routine-task-tab');
    expect(routineTab).toHaveAttribute('data-selected-bot-id', 'bot-123');
    expect(routineTab).toHaveAttribute('data-bot-options', JSON.stringify([{ value: 'bot-123', label: '当前 Bot' }]));
    expect(routineTab).toHaveAttribute('data-show-bot-selector', 'false');
    expect(mockedUseMyTaskTasks.mock.calls.at(-1)?.at(-1)).toBe(false);
    expect(mockedUseRoutineTasks).toHaveBeenLastCalledWith(
      'bot-123',
      [{ botId: 'bot-123', botName: '当前 Bot' }],
      1,
      10,
      null,
      false,
      true,
    );
  });

  it('工作身份从用户切换为 Bot 后同步切换内容', () => {
    let currentAccess = access({ id: 'human_327325', kind: 'user', displayName: '当前用户', online: true });
    mockedUseWorkIdentityAccess.mockImplementation(() => currentAccess);
    const view = render(<MyTaskPage />);
    expect(screen.getByTestId('user-task-tab')).toBeInTheDocument();

    currentAccess = access({ id: 'bot-456:327325', kind: 'bot', displayName: '切换后 Bot', online: true });
    act(() => view.rerender(<MyTaskPage />));

    expect(screen.queryByTestId('user-task-tab')).not.toBeInTheDocument();
    expect(screen.getByTestId('routine-task-tab')).toHaveAttribute('data-selected-bot-id', 'bot-456');
  });
});

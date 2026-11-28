/** @jest-environment jsdom */
import type { TodoArtifact, TodoEvent, TodoProgress, TodoProductInteraction, TodoRecord } from '@/domain/todos/models';
import TodoDetailPage from '@/pages/Lab/Todos/TodoDetailPage';
import * as todoService from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn(), replace: jest.fn() },
  useLocation: () => ({
      pathname: '/lab/todos/todo-1',
      search: (globalThis as { __todoFocusQuery?: string }).__todoFocusQuery ?? '',
    }),
  useParams: () => ({ todoId: 'todo-1' }),
}));

jest.mock('@/hooks/useSpaceContext', () => ({
  useSpaceContext: (selector: (value: unknown) => unknown) =>
    selector({
      currentSpace: { spaceId: 10, spaceName: '测试空间', spaceType: 'TEAM' },
      currentSpaceId: 10,
      initialized: true,
    }),
}));

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: { userId: 'user-1', displayName: '风太', avatarUrl: undefined, online: true },
    status: 'ready',
  }),
}));

jest.mock('@/services/todos/todoService', () => ({
  loadTodo: jest.fn(),
  acceptTodo: jest.fn(),
  loadTodoInteractions: jest.fn(),
  addTodoInteraction: jest.fn(),
  addTodoProductInteraction: jest.fn(),
  loadTodoProductInteractionPage: jest.fn(),
  stopTodoProductInteraction: jest.fn(),
  loadTodoEvents: jest.fn(),
  loadTodoArtifacts: jest.fn(),
  loadTodoMentionBots: jest.fn(),
  downloadTodoArtifactContent: jest.fn(),
  startTodoRun: jest.fn(),
  loadTodoProgress: jest.fn(),
}));

jest.mock('@/components/ui/notify', () => ({ notifySuccess: jest.fn() }));
jest.mock('@/utils/errorToast', () => ({ safeReportError: jest.fn() }));

const mockedService = jest.mocked(todoService);
const mockedHistory = jest.mocked(history);

const todo: TodoRecord = {
  todoId: 'todo-1',
  spaceId: 10,
  title: 'Q3 竞品分析',
  description: '整理三款产品的定价与功能矩阵',
  botId: 'bot-1',
  botName: '数据分析助手',
  status: 'pending_action',
  collabContextId: 'ctx-1',
  createdAt: '2026-11-08T01:12:00Z',
  updatedAt: '2026-11-08T06:48:00Z',
};

const todoProgress: TodoProgress = {
  todoId: 'todo-1',
  status: 'pending_action',
  runtimeKind: 'legacy_context',
  runtimeUnavailable: false,
  currentStageCode: 'analyze',
  stages: [
    {
      stageInstanceId: 'stage-1',
      code: 'analyze',
      name: '需求分析',
      order: 1,
      kind: 'bot',
      status: 'pending_action',
      ownerUserId: '',
      ownerUserName: '',
      executorSubjectId: 'bot-1',
      executorSubjectName: '数据分析助手',
      summary: '等待处理',
      steps: [],
      outputs: [],
      attemptCount: 1,
      detailsLoaded: true,
      ignoredMessageCount: 0,
    },
  ],
};

const productProgress: TodoProgress = {
  ...todoProgress,
  runtimeKind: 'product_collet',
  stages: [
    {
      stageInstanceId: 'stage-1',
      code: 'analyze',
      name: '需求分析',
      order: 1,
      kind: 'bot',
      status: 'pending_action',
      ownerUserId: 'human-1',
      ownerUserName: '风太',
      executorSubjectId: 'bot-1',
      executorSubjectName: '数据分析助手',
      summary: '等待补充信息',
      steps: [],
      outputs: [],
      attemptCount: 1,
      detailsLoaded: true,
      ignoredMessageCount: 0,
    },
  ],
};

function productInteraction(overrides: Partial<TodoProductInteraction> = {}): TodoProductInteraction {
  return {
    interactionId: 'pi-1',
    todoId: 'todo-1',
    kind: 'human_message',
    actorId: '149844',
    actorName: '风太',
    requestMessage: '请补充价格来源',
    deliveryState: 'succeeded',
    productColletId: 1,
    colletRolloutId: 10,
    expectedRevision: 3,
    response: { type: 'RE_DO', message: '已接受补充要求，正在重做。' },
    createdAt: '2026-11-27T10:00:00Z',
    updatedAt: '2026-11-27T10:00:01Z',
    ...overrides,
  };
}

const artifacts: TodoArtifact[] = [
  {
    artifactId: 'artifact-text',
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: '执行摘要',
    kind: 'text',
    content: { text: '已完成三款产品的定价对比。', isFinalArtifact: false },
    createdAt: '2026-11-08T06:00:00Z',
  },
  {
    artifactId: 'artifact-file',
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: '定价与功能矩阵',
    kind: 'file',
    content: {
      fileName: '定价与功能矩阵.xlsx',
      fileType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      size: 2048,
      isFinalArtifact: true,
    },
    createdAt: '2026-11-08T06:10:00Z',
  },
  {
    artifactId: 'artifact-link',
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: '产品官网',
    kind: 'url_link',
    content: { url: 'https://example.test/product', type: '官网', isFinalArtifact: false },
    createdAt: '2026-11-08T06:20:00Z',
  },
  {
    artifactId: 'artifact-code',
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: '分析脚本',
    kind: 'code',
    content: {
      codeBaseName: 'pricing-analysis',
      codeBaseAddress: 'https://example.test/repo',
      prLink: 'https://example.test/repo/pull/1',
      prDesc: '新增定价采集和对比脚本',
      isFinalArtifact: true,
    },
    createdAt: '2026-11-08T06:30:00Z',
  },
];

function event(eventSeq: number, overrides: Partial<TodoEvent> = {}): TodoEvent {
  return {
    eventId: `event-${eventSeq}`,
    eventSeq,
    eventType: 'execution.status',
    actorId: 'bot-1',
    actorType: 'bot',
    content: `事件 ${eventSeq}`,
    executionId: 'execution-1',
    contextVersion: 1,
    status: 'in_progress',
    createdAt: `2026-11-08T0${eventSeq}:12:00Z`,
    artifacts: [],
    payload: {},
    ...overrides,
  };
}

function artifactEvent(eventSeq: number, artifact: TodoArtifact, content: string): TodoEvent {
  return event(eventSeq, {
    eventType: 'artifact.reported',
    actorType: 'bot',
    content,
    payload: {
      artifact_id: artifact.artifactId,
      todo_execution_id: artifact.executionId,
      run_id: 'run-1',
      artifact_kind: artifact.kind,
      artifact_name: artifact.artifactName,
    },
  });
}

function resetStore() {
  useTodoStore.setState({
    todos: [],
    total: 0,
    selectedTodo: null,
    interactions: [],
    events: [],
    artifacts: [],
    progress: null,
    productInteractionPage: null,
    productInteractionLoading: false,
    productInteractionError: null,
    listLoading: false,
    detailLoading: false,
    eventsLoading: false,
    artifactsLoading: false,
    progressLoading: false,
    mutationLoading: false,
    listError: null,
    detailError: null,
    eventsError: null,
    artifactsError: null,
    progressError: null,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  resetStore();
  mockedService.loadTodo.mockResolvedValue(todo);
  mockedService.loadTodoInteractions.mockResolvedValue([
    event(1, { eventType: 'task.created', actorType: 'human', content: '整理三款产品的定价与功能矩阵' }),
    artifactEvent(3, artifacts[0], '产出文本：已完成三款产品的定价对比。'),
    artifactEvent(4, artifacts[1], '产出文件：定价与功能矩阵.xlsx'),
    artifactEvent(5, artifacts[2], '产出链接：产品官网'),
    artifactEvent(6, artifacts[3], '产出代码：pricing-analysis'),
    event(7, { eventType: 'task.completed', actorType: 'bot', content: '' }),
    event(8, { eventType: 'user_interaction.posted', actorType: 'human', content: '请补充价格来源' }),
  ]);
  mockedService.loadTodoEvents.mockResolvedValue([
    event(1, {
      eventType: 'task.created',
      actorType: 'human',
      content: '整理三款产品的定价与功能矩阵',
    }),
    event(2, { eventType: 'task.started', content: '开始采集竞品资料' }),
    artifactEvent(3, artifacts[0], '产出文本：已完成三款产品的定价对比。'),
    artifactEvent(4, artifacts[1], '产出文件：定价与功能矩阵.xlsx'),
    artifactEvent(5, artifacts[2], '产出链接：产品官网'),
    artifactEvent(6, artifacts[3], '产出代码：pricing-analysis'),
    event(7, { eventType: 'task.completed', content: '', status: 'pending_action' }),
    event(8, { eventType: 'user_interaction.posted', actorType: 'human', content: '请补充价格来源' }),
  ]);
  mockedService.loadTodoArtifacts.mockResolvedValue(artifacts);
  mockedService.loadTodoProgress.mockResolvedValue(todoProgress);
  mockedService.loadTodoProductInteractionPage.mockResolvedValue({ items: [], canSend: true });
  mockedService.loadTodoMentionBots.mockResolvedValue([
    { botId: 'bot-1', botName: '数据分析助手' },
    { botId: 'bot-review', botName: '复核助手' },
  ]);
  mockedService.downloadTodoArtifactContent.mockResolvedValue(new Blob(['file']));
  mockedService.addTodoInteraction.mockResolvedValue(event(5, { content: '补充价格来源' }));
  mockedService.acceptTodo.mockResolvedValue({ ...todo, status: 'completed' });
});

describe('TodoDetailPage', () => {
  it('以独立页面展示进度、互动和产物视图', async () => {
    render(<TodoDetailPage />);

    expect(await screen.findByRole('heading', { name: 'Q3 竞品分析' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回待办列表' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '待办详情视图' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '进度' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '互动' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '产物 4' })).toBeInTheDocument();

    const progressView = await screen.findByRole('region', { name: '执行阶段进度' });
    expect(within(progressView).getByText('需求分析')).toBeInTheDocument();
    expect(within(progressView).getAllByText('等待处理')).toHaveLength(2);
    expect(within(progressView).getByText('数据分析助手')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '待办验收' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '待办详情' })).toBeInTheDocument();
    expect(mockedService.loadTodoProgress).toHaveBeenCalledWith('todo-1', {
      expandedStageCode: undefined,
      messageLimit: 50,
    });
    expect(mockedService.loadTodoArtifacts).toHaveBeenCalledWith('todo-1');
  });

  it('切换互动视图并提交互动', async () => {
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });

    fireEvent.click(screen.getByRole('tab', { name: '互动' }));
    const interactionView = screen.getByRole('region', { name: '待办互动' });
    expect(within(interactionView).getAllByText('数据分析助手').length).toBeGreaterThan(0);
    expect(within(interactionView).getAllByText('风太').length).toBeGreaterThan(0);
    expect(within(interactionView).getByText('数据分析助手 开始处理')).toBeInTheDocument();
    expect(within(interactionView).getByText('转为待处理，等待发起人验收')).toBeInTheDocument();
    expect(
      within(interactionView).getByText('已完成三款产品的定价对比。'),
    ).toBeInTheDocument();
    expect(within(interactionView).getByText('定价与功能矩阵.xlsx')).toBeInTheDocument();
    expect(within(interactionView).getByText('产品官网')).toBeInTheDocument();
    expect(await within(interactionView).findByText('请补充价格来源')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('告诉 Bot 需要补充或修改的内容'), {
      target: { value: ' 补充价格来源 ' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送互动' }));

    await waitFor(() =>
      expect(mockedService.addTodoInteraction).toHaveBeenCalledWith('todo-1', { message: '补充价格来源' }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('按楼层回复时提交稳定事件 ID，并可在发送前取消回复', async () => {
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    fireEvent.click(screen.getByRole('tab', { name: '互动' }));

    const interactionView = screen.getByRole('region', { name: '待办互动' });
    const feedbackFloor = within(interactionView).getByText('请补充价格来源').closest('li') as HTMLElement;
    fireEvent.click(within(feedbackFloor).getByRole('button', { name: '回复 f8' }));
    expect(within(interactionView).getByText('回复 f8 · 风太')).toBeInTheDocument();

    fireEvent.change(within(interactionView).getByPlaceholderText('告诉 Bot 需要补充或修改的内容'), {
      target: { value: '我会在这个楼层补充' },
    });
    fireEvent.click(within(interactionView).getByRole('button', { name: '发送互动' }));

    await waitFor(() =>
      expect(mockedService.addTodoInteraction).toHaveBeenCalledWith('todo-1', {
        message: '我会在这个楼层补充',
        replyToEventId: 'event-8',
      }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();

    fireEvent.click(within(feedbackFloor).getByRole('button', { name: '回复 f8' }));
    fireEvent.click(within(interactionView).getByRole('button', { name: '取消回复' }));
    fireEvent.change(within(interactionView).getByPlaceholderText('告诉 Bot 需要补充或修改的内容'), {
      target: { value: '这次不引用楼层' },
    });
    fireEvent.click(within(interactionView).getByRole('button', { name: '发送互动' }));

    await waitFor(() => expect(mockedService.addTodoInteraction).toHaveBeenCalledTimes(2));
    expect(mockedService.addTodoInteraction).toHaveBeenLastCalledWith('todo-1', {
      message: '这次不引用楼层',
    });
  });

  it('展示刷新后持久化的回复引用', async () => {
    mockedService.loadTodoInteractions.mockResolvedValue([
      event(1, { eventType: 'task.created', actorType: 'human', content: todo.description }),
      event(2, {
        eventType: 'user_interaction.posted',
        actorType: 'human',
        content: '请继续核验',
        payload: { reply_to_event_id: 'event-1' },
      }),
    ]);
    mockedService.loadTodoEvents.mockResolvedValue([
      event(1, { eventType: 'task.created', actorType: 'human', content: todo.description }),
      event(2, {
        eventType: 'user_interaction.posted',
        actorType: 'human',
        content: '请继续核验',
        payload: { reply_to_event_id: 'event-1' },
      }),
    ]);

    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    fireEvent.click(screen.getByRole('tab', { name: '互动' }));

    const replyFloor = screen.getByText('请继续核验').closest('li') as HTMLElement;
    const quote = within(replyFloor).getByRole('blockquote');
    expect(within(quote).getByText('f1')).toBeInTheDocument();
    expect(within(quote).getByText(todo.description)).toBeInTheDocument();
  });

  it('可从待办所属空间选择 Bot mention，并提交结构化 Bot ID', async () => {
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    fireEvent.click(screen.getByRole('tab', { name: '互动' }));

    const interactionView = screen.getByRole('region', { name: '待办互动' });
    await waitFor(() => expect(mockedService.loadTodoMentionBots).toHaveBeenCalledWith(10));
    fireEvent.click(within(interactionView).getByRole('button', { name: '@ Bot' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: '@复核助手' }));
    expect(within(interactionView).getByLabelText('已选择的 Bot')).toHaveTextContent('@复核助手');

    fireEvent.change(within(interactionView).getByPlaceholderText('告诉 Bot 需要补充或修改的内容'), {
      target: { value: '@复核助手 请核验价格来源' },
    });
    fireEvent.click(within(interactionView).getByRole('button', { name: '发送互动' }));

    await waitFor(() =>
      expect(mockedService.addTodoInteraction).toHaveBeenCalledWith('todo-1', {
        message: '@复核助手 请核验价格来源',
        mentions: [{ actorType: 'bot', actorId: 'bot-review' }],
      }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('仅可回复有稳定事件 ID 的消息和产物，通知与孤立产物不可回复', async () => {
    const orphanArtifact: TodoArtifact = {
      artifactId: 'artifact-orphan',
      todoId: 'todo-1',
      executionId: 'execution-1',
      artifactName: '历史孤立产物',
      kind: 'text',
      content: { text: '没有 backing event 的历史产物', isFinalArtifact: false },
      createdAt: '2026-11-08T07:00:00Z',
    };
    mockedService.loadTodoArtifacts.mockResolvedValue([...artifacts, orphanArtifact]);

    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    fireEvent.click(screen.getByRole('tab', { name: '互动' }));

    const interactionView = screen.getByRole('region', { name: '待办互动' });
    const artifactFloor = within(interactionView).getByText('已完成三款产品的定价对比。').closest('li') as HTMLElement;
    expect(within(artifactFloor).getByRole('button', { name: '回复 f3' })).toBeInTheDocument();
    const noticeFloor = within(interactionView).getByText('转为待处理，等待发起人验收').closest('li') as HTMLElement;
    expect(within(noticeFloor).queryByRole('button', { name: /回复/ })).not.toBeInTheDocument();
    const orphanFloor = within(interactionView).getByText('没有 backing event 的历史产物').closest('li') as HTMLElement;
    expect(within(orphanFloor).queryByRole('button', { name: /回复/ })).not.toBeInTheDocument();
  });

  it('互动页从 task.created 的 objective 展示原始待办内容，不展示事件类型', async () => {
    const objective = '帮我整理下三款产品的定价与功能矩阵，A/B/C 三个竞品。';
    mockedService.loadTodoInteractions.mockResolvedValue([
      event(1, {
        eventType: 'task.created',
        actorType: 'human',
        content: '',
        payload: { objective },
      }),
    ]);
    mockedService.loadTodoEvents.mockResolvedValue([
      event(1, {
        eventType: 'task.created',
        actorType: 'human',
        content: '',
        payload: { objective },
      }),
    ]);

    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    fireEvent.click(screen.getByRole('tab', { name: '互动' }));

    const interactionView = screen.getByRole('region', { name: '待办互动' });
    expect(within(interactionView).getByText(objective)).toBeInTheDocument();
    expect(within(interactionView).queryByText('task.created')).not.toBeInTheDocument();
  });

  it('拒绝验收切到互动标记楼层，草稿跨页保留并提交完整决策', async () => {
    mockedService.acceptTodo.mockResolvedValue(todo);
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });

    fireEvent.click(
      within(screen.getByRole('region', { name: '待办验收' })).getByRole('button', { name: '验收不通过' }),
    );

    const interactionView = screen.getByRole('region', { name: '待办互动' });
    const feedback = within(interactionView).getByText('请补充价格来源');
    const feedbackFloor = feedback.closest('li') as HTMLElement;
    expect(feedbackFloor).toHaveAttribute('data-floor', '8');
    const marker = within(feedbackFloor).getByRole('button', { name: '标记 f8 为问题点' });
    fireEvent.click(marker);
    expect(marker).toHaveAttribute('aria-pressed', 'true');
    expect(marker).toHaveClass('bg-destructive');

    fireEvent.change(within(interactionView).getByPlaceholderText('填写完整返工要求'), {
      target: { value: ' 请修正产品 B 的企业版价格 ' },
    });
    fireEvent.click(screen.getByRole('tab', { name: '进度' }));

    const acceptance = screen.getByRole('region', { name: '待办验收' });
    expect(within(acceptance).getByText('已选中问题楼层：f8')).toBeInTheDocument();
    expect(within(acceptance).getByText('返工要求正在互动页编辑')).toBeInTheDocument();
    expect(within(acceptance).queryByPlaceholderText('填写完整返工要求')).not.toBeInTheDocument();
    expect(within(acceptance).queryByRole('button', { name: '提交并退回返工' })).not.toBeInTheDocument();

    fireEvent.click(within(acceptance).getByRole('button', { name: '返回互动继续填写' }));
    const resumedInteractionView = screen.getByRole('region', { name: '待办互动' });
    expect(within(resumedInteractionView).getByPlaceholderText('填写完整返工要求')).toHaveValue(
      ' 请修正产品 B 的企业版价格 ',
    );
    expect(
      within(within(resumedInteractionView).getByText('请补充价格来源').closest('li') as HTMLElement).getByRole(
        'button',
        { name: '取消 f8 问题点标记' },
      ),
    ).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(resumedInteractionView).getByRole('button', { name: '提交并退回返工' }));

    await waitFor(() => expect(mockedService.acceptTodo).toHaveBeenCalledTimes(1));
    const input = mockedService.acceptTodo.mock.calls[0][1];
    expect(input).toEqual({
      decisionId: expect.stringMatching(/^decision_/),
      verdict: 'rejected',
      message: '请修正产品 B 的企业版价格',
      targets: [{ kind: 'event', id: 'event-8', floor: 8 }],
    });
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('拒绝提交失败后保留草稿并复用同一个 decision id，取消时清空', async () => {
    mockedService.acceptTodo.mockRejectedValueOnce(new Error('temporary')).mockResolvedValueOnce(todo);
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });

    fireEvent.click(
      within(screen.getByRole('region', { name: '待办验收' })).getByRole('button', { name: '验收不通过' }),
    );
    const interactionView = screen.getByRole('region', { name: '待办互动' });
    const feedbackFloor = within(interactionView).getByText('请补充价格来源').closest('li') as HTMLElement;
    fireEvent.click(within(feedbackFloor).getByRole('button', { name: '标记 f8 为问题点' }));
    fireEvent.change(within(interactionView).getByPlaceholderText('填写完整返工要求'), {
      target: { value: '返工说明' },
    });
    fireEvent.click(within(interactionView).getByRole('button', { name: '提交并退回返工' }));
    await waitFor(() => expect(mockedService.acceptTodo).toHaveBeenCalledTimes(1));
    const firstDecisionId = mockedService.acceptTodo.mock.calls[0][1].decisionId;
    expect(within(interactionView).getByPlaceholderText('填写完整返工要求')).toHaveValue('返工说明');

    fireEvent.click(within(interactionView).getByRole('button', { name: '提交并退回返工' }));
    await waitFor(() => expect(mockedService.acceptTodo).toHaveBeenCalledTimes(2));
    expect(mockedService.acceptTodo.mock.calls[1][1].decisionId).toBe(firstDecisionId);
    expect(within(interactionView).queryByPlaceholderText('填写完整返工要求')).not.toBeInTheDocument();
    expect(within(interactionView).getByPlaceholderText('告诉 Bot 需要补充或修改的内容')).toBeInTheDocument();
  });

  it('取消拒绝清空说明和问题目标', async () => {
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });

    fireEvent.click(
      within(screen.getByRole('region', { name: '待办验收' })).getByRole('button', { name: '验收不通过' }),
    );
    const interactionView = screen.getByRole('region', { name: '待办互动' });
    const feedbackFloor = within(interactionView).getByText('请补充价格来源').closest('li') as HTMLElement;
    fireEvent.click(within(feedbackFloor).getByRole('button', { name: '标记 f8 为问题点' }));
    fireEvent.change(within(interactionView).getByPlaceholderText('填写完整返工要求'), {
      target: { value: '返工说明' },
    });
    fireEvent.click(within(interactionView).getByRole('button', { name: '取消' }));

    expect(within(interactionView).queryByPlaceholderText('填写完整返工要求')).not.toBeInTheDocument();
    expect(within(interactionView).getByPlaceholderText('告诉 Bot 需要补充或修改的内容')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '进度' }));
    fireEvent.click(
      within(screen.getByRole('region', { name: '待办验收' })).getByRole('button', { name: '验收不通过' }),
    );
    expect(screen.getByText('请先标记有问题的互动楼层')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('填写完整返工要求')).toHaveValue('');
  });

  it('验收通过提交无目标的稳定决策', async () => {
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });

    fireEvent.click(within(screen.getByRole('region', { name: '待办验收' })).getByRole('button', { name: '验收通过' }));

    await waitFor(() => expect(mockedService.acceptTodo).toHaveBeenCalledTimes(1));
    expect(mockedService.acceptTodo).toHaveBeenCalledWith('todo-1', {
      decisionId: expect.stringMatching(/^decision_/),
      verdict: 'approved',
      targets: [],
      message: undefined,
    });
  });

  it('互动页用引用预览展示验收问题点', async () => {
    const rejection = '请修正产品 B 的企业版价格，并补充官网来源、更新时间和无法确认项的风险说明。';
    const rejectedEvent = event(5, {
      eventType: 'task.rejected',
      actorType: 'human',
      content: rejection,
      payload: {
        content: rejection,
        targets: [
          { kind: 'artifact', id: 'artifact-text', floor: 2 },
          { kind: 'artifact', id: 'artifact-file', floor: 3 },
        ],
      },
    });
    mockedService.loadTodoInteractions.mockResolvedValue([
      event(1, { eventType: 'task.created', actorType: 'human', content: todo.description }),
      artifactEvent(3, artifacts[0], '产出文本：已完成三款产品的定价对比。'),
      artifactEvent(4, artifacts[1], '产出文件：定价与功能矩阵.xlsx'),
      rejectedEvent,
    ]);

    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    fireEvent.click(screen.getByRole('tab', { name: '互动' }));

    const interactionView = screen.getByRole('region', { name: '待办互动' });
    expect(within(interactionView).getByText(rejection)).toBeInTheDocument();
    const quotes = within(interactionView).getByRole('group', { name: '问题引用' });
    expect(within(quotes).getByText('f2')).toBeInTheDocument();
    expect(within(quotes).getByText('文本产物')).toBeInTheDocument();
    expect(within(quotes).getByText('已完成三款产品的定价对比。')).toBeInTheDocument();
    expect(within(quotes).getByText('f3')).toBeInTheDocument();
    expect(within(quotes).getByText('产物')).toBeInTheDocument();
    expect(within(quotes).getByText('定价与功能矩阵.xlsx · xlsx · 2 KB')).toBeInTheDocument();
  });

  it('产物视图使用独立产物接口展示四类产物，并支持文件下载', async () => {
    const createObjectURL = jest.fn(() => 'blob:todo-artifact');
    const revokeObjectURL = jest.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
    const clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });

    fireEvent.click(screen.getByRole('tab', { name: '产物 4' }));
    const artifactView = screen.getByRole('region', { name: '待办产物' });
    expect(within(artifactView).getByText('已完成三款产品的定价对比。')).toBeInTheDocument();
    expect(within(artifactView).getByRole('link', { name: /https:\/\/example\.test\/product/ })).toHaveAttribute(
      'href',
      'https://example.test/product',
    );
    expect(within(artifactView).getByRole('link', { name: /pricing-analysis/ })).toHaveAttribute(
      'href',
      'https://example.test/repo',
    );
    expect(within(artifactView).queryByText('事件产物')).not.toBeInTheDocument();

    fireEvent.click(within(artifactView).getByRole('button', { name: '下载' }));
    await waitFor(() =>
      expect(mockedService.downloadTodoArtifactContent).toHaveBeenCalledWith('todo-1', 'artifact-file', 'attachment'),
    );
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(clickSpy).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:todo-artifact');
    expect(within(artifactView).queryByText('产物文件读取失败')).not.toBeInTheDocument();
    clickSpy.mockRestore();
  });

  it('返回列表', async () => {
    render(<TodoDetailPage />);
    fireEvent.click(await screen.findByRole('button', { name: '返回待办列表' }));
    expect(mockedHistory.push).toHaveBeenCalledWith('/lab/todos');
  });
  it('使用紧凑的阶段进度和验收操作', async () => {
    render(<TodoDetailPage />);
    const progressView = await screen.findByRole('region', { name: '执行阶段进度' });
    const stageName = within(progressView).getByText('需求分析');
    const acceptanceHeading = screen.getByRole('heading', { name: '待你验收 · Bot 已完成处理' });
    const acceptance = screen.getByRole('region', { name: '待办验收' });
    const rejectButton = within(acceptance).getByRole('button', { name: '验收不通过' });

    expect(stageName).toHaveClass('text-sm', 'font-medium');
    expect(within(progressView).getByText('数据分析助手')).toBeInTheDocument();
    expect(within(progressView).getAllByText('等待处理')).toHaveLength(2);
    expect(acceptanceHeading).toHaveClass('text-sm', 'font-semibold');
    expect(rejectButton).toHaveClass('h-8', 'rounded-lg', 'px-4', 'text-xs');
  });

  it('非待处理待办不显示验收操作', async () => {
    mockedService.loadTodo.mockResolvedValue({ ...todo, status: 'completed' });
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    expect(screen.queryByRole('region', { name: '待办验收' })).not.toBeInTheDocument();
  });

  it('使用紧凑产物与互动层级', async () => {
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });

    fireEvent.click(screen.getByRole('tab', { name: '产物 4' }));
    const artifactView = screen.getByRole('region', { name: '待办产物' });
    const artifactTitle = within(artifactView).getByText('执行摘要');
    const artifactMeta = within(artifactView).getByText('文本产物');
    const artifactCard = artifactTitle.closest('article');

    expect(artifactTitle).toHaveClass('text-sm', 'font-medium');
    expect(artifactMeta).toHaveClass('text-xs');
    expect(artifactCard).toHaveClass('rounded-xl', 'border', 'border-border', 'bg-card');
    expect(within(artifactView).getByText('已完成三款产品的定价对比。')).toHaveClass('text-xs', 'leading-5');

    fireEvent.click(screen.getByRole('tab', { name: '进度' }));
    fireEvent.click(
      within(screen.getByRole('region', { name: '待办验收' })).getByRole('button', { name: '验收不通过' }),
    );
    const interactionView = screen.getByRole('region', { name: '待办互动' });
    const interactionTitle = within(interactionView).getByRole('heading', { name: '互动' });
    const feedback = within(interactionView).getByText('请补充价格来源');
    const feedbackFloor = feedback.closest('li');
    const marker = within(feedbackFloor as HTMLElement).getByRole('button', { name: '标记 f8 为问题点' });
    const rejectButton = within(interactionView).getByRole('button', { name: '提交并退回返工' });

    expect(interactionTitle).toHaveClass('text-lg', 'font-semibold');
    expect(feedback).toHaveClass('text-sm', 'leading-6');
    expect(feedbackFloor).toHaveClass('grid', 'grid-cols-[4px_minmax(0,1fr)]', 'gap-4');
    expect(marker).toHaveAttribute('aria-pressed', 'false');
    expect(marker).toHaveClass('bg-border');
    expect(marker).not.toHaveClass('bg-transparent');
    fireEvent.click(marker);
    expect(marker).toHaveAttribute('aria-pressed', 'true');
    expect(rejectButton).toHaveClass('h-8', 'rounded-lg', 'px-4', 'text-xs');
  });

  it('使用居中紧凑详情阅读列', async () => {
    render(<TodoDetailPage />);
    const heading = await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    const backButton = screen.getByRole('button', { name: '返回待办列表' });
    const contentColumn = document.querySelector("div[class*='max-w-[1200px]'][class*='lg:flex-row']");
    const summary = screen.getByLabelText('待办概要');
    const card = screen.getByRole('group', { name: '待办详情视图' }).closest('.rounded-xl');
    const detailSidebar = screen.getByRole('complementary', { name: '待办详情' });

    expect(backButton).toHaveClass('size-8', 'rounded-lg');
    expect(backButton.querySelector('svg')).toHaveClass('size-4');
    expect(heading).toHaveClass('text-[18px]', 'font-semibold');
    expect(document.querySelector('header[class*="px-4 pt-6 pb-4"]')).toBeInTheDocument();
    expect(contentColumn).toHaveClass('max-w-[1200px]', 'gap-6', 'lg:flex-row');
    expect(summary.querySelector('p')).toHaveClass('text-sm', 'leading-relaxed');
    expect(within(detailSidebar).getByRole('heading', { name: '详情' })).toHaveClass('text-sm', 'font-semibold');
    expect(card).toHaveClass('rounded-xl');
  });
});

describe('Todo Product interaction', () => {
  beforeEach(() => {
    mockedService.loadTodoProgress.mockResolvedValue(productProgress);
    mockedService.loadTodoProductInteractionPage.mockResolvedValue({
      items: [productInteraction()],
      canSend: true,
    });
    mockedService.addTodoProductInteraction.mockResolvedValue(
      productInteraction({ interactionId: 'pi-2', requestMessage: '补充价格来源' }),
    );
  });

  it('Product 待办展示平台互动并提交到 Product 互动接口', async () => {
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    fireEvent.click(screen.getByRole('tab', { name: '互动' }));

    const interactionView = await screen.findByRole('region', { name: 'Product 待办互动' });
    expect(within(interactionView).getByText('请补充价格来源')).toBeInTheDocument();
    expect(within(interactionView).getByText('已接受补充要求，正在重做。')).toBeInTheDocument();
    expect(within(interactionView).getByText('RE_DO')).toBeInTheDocument();

    fireEvent.change(within(interactionView).getByLabelText('继续当前互动'), {
      target: { value: ' 补充价格来源 ' },
    });
    fireEvent.click(within(interactionView).getByRole('button', { name: '发送回复' }));

    await waitFor(() =>
      expect(mockedService.addTodoProductInteraction).toHaveBeenCalledWith('todo-1', {
        message: '补充价格来源',
        replyToInteractionId: 'pi-1',
      }),
    );
    expect(mockedService.addTodoInteraction).not.toHaveBeenCalled();
  });

  it('最外层评论会发起新的 Product 互动会话', async () => {
    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    fireEvent.click(screen.getByRole('tab', { name: '互动' }));

    const composer = screen.getByRole('region', { name: '发起 Product 待办互动' });
    fireEvent.change(within(composer).getByLabelText('评论，与待办助手对话'), {
      target: { value: ' 新开一个验收问题 ' },
    });
    fireEvent.click(within(composer).getByRole('button', { name: '发送评论' }));

    await waitFor(() =>
      expect(mockedService.addTodoProductInteraction).toHaveBeenCalledWith('todo-1', {
        message: '新开一个验收问题',
      }),
    );
  });

  it('平台未返回前显示停止入口并禁止继续发送', async () => {
    mockedService.loadTodoProductInteractionPage.mockResolvedValue({
      items: [productInteraction({
        interactionId: 'pi-blocking',
        deliveryState: 'delivery_unknown',
        response: undefined,
      })],
      canSend: false,
      blockingInteractionId: 'pi-blocking',
    });
    mockedService.stopTodoProductInteraction.mockResolvedValue(
      productInteraction({ interactionId: 'pi-blocking', deliveryState: 'cancel_requested' }),
    );

    render(<TodoDetailPage />);
    await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    fireEvent.click(screen.getByRole('tab', { name: '互动' }));

    const interactionView = await screen.findByRole('region', { name: 'Product 待办互动' });
    expect(within(interactionView).getByText('结果确认中')).toBeInTheDocument();
    expect(
      within(interactionView).getByText('待办助手正在处理，返回前暂不能继续发送。'),
    ).toBeInTheDocument();
    expect(within(interactionView).getByLabelText('继续当前互动')).toBeDisabled();
    expect(screen.getByLabelText('评论，与待办助手对话')).toBeDisabled();

    fireEvent.click(within(interactionView).getByRole('button', { name: '终止' }));
    await waitFor(() =>
      expect(mockedService.stopTodoProductInteraction).toHaveBeenCalledWith('todo-1', 'pi-blocking'),
    );
  });

  it('通知深链打开时直接进入互动视图', async () => {
    (globalThis as { __todoFocusQuery?: string }).__todoFocusQuery = '?focus=interaction';
    try {
      render(<TodoDetailPage />);
      expect(await screen.findByRole('region', { name: 'Product 待办互动' })).toBeInTheDocument();
    } finally {
      (globalThis as { __todoFocusQuery?: string }).__todoFocusQuery = undefined;
    }
  });
});

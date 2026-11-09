/** @jest-environment jsdom */
import { useCommunityHelpMock } from '@/hooks/useCommunityHelpMock';
import { useCommunityHelpMockStore } from '@/stores/communityHelpMockStore';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
beforeEach(() => {
  useCommunityHelpMockStore.getState().reset();
  jest.clearAllMocks();
  let counter = 0;
  Object.defineProperty(crypto, 'randomUUID', { configurable: true, value: () => `test-${++counter}` });
});

describe('useCommunityHelpMock', () => {
  test('搜索、来源、状态组合筛选；清除恢复全部', () => {
    const { result } = renderHook(() => useCommunityHelpMock());
    act(() => {
      result.current.setStatus('running');
      result.current.setSource('Dima');
      result.current.setQuery('DIMA-DEMO-1042');
    });
    expect(result.current.tasks).toHaveLength(1);
    expect(result.current.tasks[0].id).toBe('mock-help-1');
    act(() => result.current.setQuery('不会匹配'));
    expect(result.current.tasks).toHaveLength(0);
    expect(result.current.boardTasks).toHaveLength(4);
    act(() => result.current.clearFilters());
    expect(result.current.tasks).toHaveLength(4);
  });

  test('无效配置和任务不写入 Store，显示错误', () => {
    const { result } = renderHook(() => useCommunityHelpMock());
    act(() => {
      expect(
        result.current.saveBoard({
          name: ' ',
          description: '',
          collaborationMode: 'selected',
          members: [],
          sources: [],
          sourceConfig: result.current.board.sourceConfig,
        }),
      ).toBe(false);
    });
    act(() => {
      expect(
        result.current.createTask({
          title: ' ',
          description: '描述',
          source: 'Dima',
          sourceRef: '',
          syncToDima: false,
          dimaProjectSpace: '',
        }),
      ).toBe(false);
    });
    expect(result.current.boards).toHaveLength(2);
    expect(result.current.tasks).toHaveLength(4);
    expect(toast.error).toHaveBeenCalledTimes(2);
    expect(toast.error).toHaveBeenCalledWith('请填写模版名称');
  });

  test('创建多个 AiCoding 模版，保存配置会清除筛选并切换模版', () => {
    const { result } = renderHook(() => useCommunityHelpMock());
    act(() => result.current.setQuery('旧筛选'));
    act(() => {
      expect(
        result.current.saveBoard({
          name: ' 前端协作 ',
          collaborationMode: 'selected',
          description: '页面修复',
          members: [{ botId: 'mock-coder', roles: ['代码', 'verify'] }],
          sources: ['Task Discover'],
          sourceConfig: result.current.board.sourceConfig,
        }),
      ).toBe(true);
    });
    expect(toast.success).toHaveBeenCalledWith('模版已创建（Mock）');
    expect(result.current.boards).toHaveLength(3);
    expect(result.current.board.name).toBe('前端协作');
    expect(result.current.board.type).toBe('AiCoding');
    expect(result.current.tasks).toHaveLength(0);
    expect(result.current.query).toBe('');
    act(() => result.current.selectBoard('mock-aicoding'));
    expect(result.current.tasks).toHaveLength(4);
  });

  test('任务创建后打开副屏、清除过滤，修改模版不重写已有任务快照', () => {
    const { result } = renderHook(() => useCommunityHelpMock());
    act(() => {
      result.current.setStatus('done');
      result.current.setQuery('不存在');
    });
    act(() => {
      expect(
        result.current.createTask({
          title: '新增任务',
          description: '说明',
          source: 'Task Discover',
          sourceRef: 'TD-1',
          syncToDima: false,
          dimaProjectSpace: '',
        }),
      ).toBe(true);
    });
    const task = result.current.selectedTask!;
    expect(task.source).toBe('Task Discover');
    expect(result.current.tasks[0].id).toBe(task.id);
    expect(result.current.status).toBe('all');
    act(() =>
      result.current.saveBoard(
        { ...result.current.board, sources: ['Dima'], members: [{ botId: 'mock-pr', roles: ['pr'] }] },
        result.current.board.id,
      ),
    );
    expect(result.current.boardTasks.find((item) => item.id === task.id)).toEqual(task);
    act(() => {
      expect(
        result.current.createTask({
          title: '拒绝来源',
          description: '说明',
          source: 'Task Discover',
          sourceRef: '',
          syncToDima: false,
          dimaProjectSpace: '',
        }),
      ).toBe(false);
    });
    act(() =>
      result.current.createTask({
        title: '新配置任务',
        description: '说明',
        source: 'Dima',
        sourceRef: '',
        syncToDima: false,
        dimaProjectSpace: '',
      }),
    );
    expect(result.current.selectedTask?.stages.map((stage) => stage.role)).toEqual(['pr']);
  });

  test('HTTP 预览环境没有 randomUUID 时仍可新增模版和任务', () => {
    Object.defineProperty(crypto, 'randomUUID', { configurable: true, value: undefined });
    const { result } = renderHook(() => useCommunityHelpMock());
    act(() => {
      expect(
        result.current.saveBoard({
          name: 'HTTP Mock 模版',
          collaborationMode: 'selected',
          description: '',
          members: [{ botId: 'mock-coder', roles: ['代码'] }],
          sources: ['Dima'],
          sourceConfig: result.current.board.sourceConfig,
        }),
      ).toBe(true);
    });
    expect(result.current.board.id).toMatch(/^mock-board-/);
    act(() => {
      expect(
        result.current.createTask({
          title: 'HTTP 任务',
          description: '说明',
          source: 'Dima',
          sourceRef: '',
          syncToDima: false,
          dimaProjectSpace: '',
        }),
      ).toBe(true);
    });
    expect(result.current.selectedTask?.id).toMatch(/^mock-help-/);
    expect(result.current.selectedTask?.boardId).toBe(result.current.board.id);
  });

  test('副屏推进结果同步到列表；关闭和切换模版清理选择', () => {
    const { result } = renderHook(() => useCommunityHelpMock());
    act(() => result.current.selectTask('mock-help-2'));
    act(() => result.current.advanceTask());
    expect(result.current.selectedTask?.status).toBe('running');
    expect(result.current.tasks.find((task) => task.id === 'mock-help-2')?.status).toBe('running');
    act(() => result.current.selectTask(null));
    expect(result.current.selectedTask).toBeNull();
    act(() => result.current.selectTask('mock-help-1'));
    act(() => result.current.selectBoard('mock-aicoding'));
    expect(result.current.selectedTask).toBeNull();
  });
  test('保存开放模版清除固定成员，不修改输入草稿', () => {
    const { result } = renderHook(() => useCommunityHelpMock());
    const draft = { ...result.current.board, name: '开放协作', collaborationMode: 'open' as const };
    act(() => expect(result.current.saveBoard(draft)).toBe(true));
    expect(result.current.board.collaborationMode).toBe('open');
    expect(result.current.board.members).toEqual([]);
    expect(draft.members).toHaveLength(6);
    act(() =>
      result.current.createTask({
        title: '开放任务',
        description: '说明',
        source: 'Dima',
        sourceRef: '',
        syncToDima: false,
        dimaProjectSpace: '',
      }),
    );
    expect(result.current.selectedTask?.collaborationMode).toBe('open');
    expect(result.current.selectedTask?.stages.every((stage) => stage.botNames.length === 0)).toBe(true);
  });

  test('模版协作范围双向变更不影响已有任务，新任务使用新范围', () => {
    const { result } = renderHook(() => useCommunityHelpMock());
    const initial = result.current.boardTasks[0];
    act(() =>
      result.current.saveBoard({ ...result.current.board, collaborationMode: 'open' }, result.current.board.id),
    );
    expect(result.current.boardTasks[0]).toEqual(initial);
    expect(result.current.boardTasks[0].collaborationMode).toBe('selected');
    act(() =>
      result.current.createTask({
        title: '开放任务',
        description: '说明',
        source: 'Dima',
        sourceRef: '',
        syncToDima: false,
        dimaProjectSpace: '',
      }),
    );
    const openTask = result.current.selectedTask!;
    act(() =>
      result.current.saveBoard(
        {
          ...result.current.board,
          collaborationMode: 'selected',
          members: [{ botId: 'mock-coder', roles: ['代码'] }],
        },
        result.current.board.id,
      ),
    );
    expect(result.current.boardTasks.find((task) => task.id === openTask.id)).toEqual(openTask);
    expect(openTask.collaborationMode).toBe('open');
    act(() =>
      result.current.createTask({
        title: '指定任务',
        description: '说明',
        source: 'Dima',
        sourceRef: '',
        syncToDima: false,
        dimaProjectSpace: '',
      }),
    );
    expect(result.current.selectedTask?.collaborationMode).toBe('selected');
    expect(result.current.selectedTask?.stages[0].botNames).toEqual(['Coding Bot']);
  });
});

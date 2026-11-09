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
      expect(result.current.saveBoard({ name: ' ', description: '', members: [], sources: [] })).toBe(false);
    });
    act(() => {
      expect(result.current.createTask({ title: ' ', description: '描述', source: 'Dima', sourceRef: '' })).toBe(false);
    });
    expect(result.current.boards).toHaveLength(1);
    expect(result.current.tasks).toHaveLength(4);
    expect(toast.error).toHaveBeenCalledTimes(2);
  });

  test('创建多个 AiCoding 版面，保存配置会清除筛选并切换版面', () => {
    const { result } = renderHook(() => useCommunityHelpMock());
    act(() => result.current.setQuery('旧筛选'));
    act(() => {
      expect(
        result.current.saveBoard({
          name: ' 前端协作 ',
          description: '页面修复',
          members: [{ botId: 'mock-coder', roles: ['代码', 'verify'] }],
          sources: ['Task Discover'],
        }),
      ).toBe(true);
    });
    expect(result.current.boards).toHaveLength(2);
    expect(result.current.board.name).toBe('前端协作');
    expect(result.current.board.type).toBe('AiCoding');
    expect(result.current.tasks).toHaveLength(0);
    expect(result.current.query).toBe('');
    act(() => result.current.selectBoard('mock-aicoding'));
    expect(result.current.tasks).toHaveLength(4);
  });

  test('任务创建后打开副屏、清除过滤，修改版面不重写已有任务快照', () => {
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
        result.current.createTask({ title: '拒绝来源', description: '说明', source: 'Task Discover', sourceRef: '' }),
      ).toBe(false);
    });
    act(() => result.current.createTask({ title: '新配置任务', description: '说明', source: 'Dima', sourceRef: '' }));
    expect(result.current.selectedTask?.stages.map((stage) => stage.role)).toEqual(['pr']);
  });

  test('HTTP 预览环境没有 randomUUID 时仍可新增版面和任务', () => {
    Object.defineProperty(crypto, 'randomUUID', { configurable: true, value: undefined });
    const { result } = renderHook(() => useCommunityHelpMock());
    act(() => {
      expect(
        result.current.saveBoard({
          name: 'HTTP Mock 版面',
          description: '',
          members: [{ botId: 'mock-coder', roles: ['代码'] }],
          sources: ['Dima'],
        }),
      ).toBe(true);
    });
    expect(result.current.board.id).toMatch(/^mock-board-/);
    act(() => {
      expect(
        result.current.createTask({ title: 'HTTP 任务', description: '说明', source: 'Dima', sourceRef: '' }),
      ).toBe(true);
    });
    expect(result.current.selectedTask?.id).toMatch(/^mock-help-/);
    expect(result.current.selectedTask?.boardId).toBe(result.current.board.id);
  });

  test('副屏推进结果同步到列表；关闭和切换版面清理选择', () => {
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
});

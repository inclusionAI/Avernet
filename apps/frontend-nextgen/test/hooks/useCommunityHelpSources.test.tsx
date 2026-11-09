/** @jest-environment jsdom */
import { useCommunityHelpMock } from '@/hooks/useCommunityHelpMock';
import { useCommunityHelpMockStore } from '@/stores/communityHelpMockStore';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
beforeEach(() => {
  useCommunityHelpMockStore.getState().reset();
  jest.clearAllMocks();
});

test('保存来源配置会去空白且复制嵌套对象，不污染其他模版', () => {
  const { result } = renderHook(() => useCommunityHelpMock());
  const other = result.current.boards[1];
  const draft = {
    ...result.current.board,
    sourceConfig: { dima: { projectSpace: ' 新项目 ' }, taskDiscover: { botName: ' 新发现 Bot ' } },
  };
  act(() => expect(result.current.saveBoard(draft, draft.id)).toBe(true));
  expect(result.current.board.sourceConfig).toEqual({
    dima: { projectSpace: '新项目' },
    taskDiscover: { botName: '新发现 Bot' },
  });
  draft.sourceConfig.dima.projectSpace = 'changed';
  draft.sourceConfig.taskDiscover.botName = 'changed';
  expect(result.current.board.sourceConfig.dima.projectSpace).toBe('新项目');
  expect(result.current.board.sourceConfig.taskDiscover.botName).toBe('新发现 Bot');
  expect(result.current.boards[1]).toEqual(other);
});

test('无效来源配置不写入并提示错误，开放不绕过详情校验', () => {
  const { result } = renderHook(() => useCommunityHelpMock());
  const original = result.current.board;
  act(() =>
    expect(
      result.current.saveBoard(
        {
          ...original,
          collaborationMode: 'open',
          sourceConfig: { ...original.sourceConfig, dima: { projectSpace: ' ' } },
        },
        original.id,
      ),
    ).toBe(false),
  );
  expect(result.current.board).toBe(original);
  expect(toast.error).toHaveBeenCalledWith('请填写 Dima 项目空间');
});

test('手动输入可筛选，保存同步目标，后续设置不重写旧任务', () => {
  const { result } = renderHook(() => useCommunityHelpMock());
  act(() =>
    expect(
      result.current.createTask({
        title: '手动任务',
        description: '说明',
        source: '手动输入',
        sourceRef: '',
        syncToDima: true,
        dimaProjectSpace: '同步空间',
      }),
    ).toBe(true),
  );
  const task = result.current.selectedTask!;
  expect(task.dimaProjectSpace).toBe('同步空间');
  act(() =>
    result.current.saveBoard(
      {
        ...result.current.board,
        sourceConfig: { dima: { projectSpace: 'changed' }, taskDiscover: { botName: '新发现 Bot' } },
      },
      result.current.board.id,
    ),
  );
  expect(result.current.boardTasks.find((item) => item.id === task.id)).toEqual(task);
  act(() => result.current.setSource('手动输入'));
  expect(result.current.tasks.map((item) => item.id)).toEqual([task.id]);
});

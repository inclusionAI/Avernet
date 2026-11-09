import { HELP_MOCK_BOTS } from '@/domain/communityHelp/mock';
import { advanceHelpTask, createHelpTask, validateHelpBoard } from '@/domain/communityHelp/model';
import { normalizeHelpSourceConfig } from '@/domain/communityHelp/sources';
import type {
  HelpBoard,
  HelpBoardDraft,
  HelpSource,
  HelpTaskDraft,
  HelpTaskStatus,
} from '@/domain/communityHelp/types';
import { useCommunityHelpMockStore } from '@/stores/communityHelpMockStore';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

// Mock ID 不承载安全语义；HTTP 预览环境可能不提供 randomUUID。
function createMockId(prefix: string): string {
  const id =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}-${id}`;
}

export function useCommunityHelpMock() {
  const state = useCommunityHelpMockStore();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<HelpTaskStatus | 'all'>('all');
  const [source, setSource] = useState<HelpSource | 'all'>('all');
  const board = state.boards.find((item) => item.id === state.selectedBoardId) ?? state.boards[0];
  const boardTasks = state.tasks.filter((task) => task.boardId === board.id);
  const normalizedQuery = query.trim().toLowerCase();
  const tasks = boardTasks.filter(
    (task) =>
      (status === 'all' || task.status === status) &&
      (source === 'all' || task.source === source) &&
      (!normalizedQuery ||
        [task.title, task.description, task.id, task.sourceRef].join(' ').toLowerCase().includes(normalizedQuery)),
  );
  const selectedTask = boardTasks.find((task) => task.id === state.selectedTaskId) ?? null;
  const clearFilters = useCallback(() => {
    setQuery('');
    setStatus('all');
    setSource('all');
  }, []);
  const selectBoard = useCallback(
    (id: string) => {
      state.selectBoard(id);
      clearFilters();
    },
    [state.selectBoard, clearFilters],
  );

  const saveBoard = useCallback(
    (draft: HelpBoardDraft, editingId?: string): boolean => {
      try {
        if (editingId && !state.boards.some((item) => item.id === editingId))
          throw new Error('模版不存在，请重新打开设置');
        const error = validateHelpBoard(draft, state.boards, HELP_MOCK_BOTS, editingId);
        if (error) throw new Error(error);
        const saved: HelpBoard = {
          ...draft,
          id: editingId ?? createMockId('mock-board'),
          type: 'AiCoding',
          name: draft.name.trim(),
          description: draft.description.trim(),
          members:
            draft.collaborationMode === 'open'
              ? []
              : draft.members.map((member) => ({ ...member, roles: [...member.roles] })),
          sources: [...draft.sources],
          sourceConfig: normalizeHelpSourceConfig(draft.sourceConfig),
        };
        state.saveBoard(saved);
        clearFilters();
        toast.success(editingId ? '模版设置已更新（Mock）' : '模版已创建（Mock）');
        return true;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '模版保存失败');
        return false;
      }
    },
    [state.boards, state.saveBoard, clearFilters],
  );

  const createTask = useCallback(
    (draft: HelpTaskDraft): boolean => {
      try {
        const task = createHelpTask(createMockId('mock-help'), board, draft, HELP_MOCK_BOTS, new Date().toISOString());
        state.addTask(task);
        clearFilters();
        toast.success('求助任务已创建，仅为 Mock 演示');
        return true;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '任务创建失败');
        return false;
      }
    },
    [board, state.addTask, clearFilters],
  );

  const advanceTask = useCallback(() => {
    if (!selectedTask || selectedTask.status === 'done') return;
    state.replaceTask(advanceHelpTask(selectedTask, new Date().toISOString()));
    toast.success('已模拟推进任务，未执行真实操作');
  }, [selectedTask, state.replaceTask]);

  return {
    boards: state.boards,
    board,
    bots: HELP_MOCK_BOTS,
    tasks,
    selectedTask,
    boardTasks,
    query,
    setQuery,
    status,
    setStatus,
    source,
    setSource,
    clearFilters,
    selectBoard,
    selectTask: state.selectTask,
    saveBoard,
    createTask,
    advanceTask,
  };
}

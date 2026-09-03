import type { PublicTask } from '../src/domain/collaborationSquare/types';
import { useCollaborationSquareStore } from '../src/stores/collaborationSquareStore';

const makeTask = (id: string): PublicTask => ({
  id,
  name: id,
  goal: `目标 ${id}`,
  acceptanceCriteria: ['验收标准'],
  status: 'pending_claim',
  publisherBotName: '协作助手',
  publishedAt: '2026-08-19T09:00:00Z',
});

describe('collaboration square store', () => {
  beforeEach(() => useCollaborationSquareStore.getState().reset());

  test('分别保存目录、查询和目标级 busy 状态', () => {
    const store = useCollaborationSquareStore.getState();
    store.setBots([
      { id: 'b1', name: '助手', ownerName: 'Owner', description: '', capabilities: [], relationshipStatus: 'none' },
    ]);
    store.setQuery('bot', '需求');
    store.setBusy('bot:b1', true);
    expect(useCollaborationSquareStore.getState()).toMatchObject({ botQuery: '需求', busyKeys: ['bot:b1'] });
    store.setBusy('bot:b1', false);
    expect(useCollaborationSquareStore.getState().busyKeys).toEqual([]);
  });

  test('目标失效时移除列表并清理选中详情', () => {
    const store = useCollaborationSquareStore.getState();
    store.setBots([
      { id: 'b1', name: '助手', ownerName: 'Owner', description: '', capabilities: [], relationshipStatus: 'none' },
    ]);
    store.setSelectedBotId('b1');
    store.removeBot('b1');
    expect(useCollaborationSquareStore.getState()).toMatchObject({ bots: [], selectedBotId: null });
  });

  test('重复原始 Bot ID 时只更新好友申请目标对应的卡片', () => {
    const store = useCollaborationSquareStore.getState();
    store.setBots([
      {
        id: 'default',
        friendRequestBotId: 'default:entity-a',
        name: 'Bot A',
        ownerName: 'Owner A',
        description: '',
        capabilities: [],
        relationshipStatus: 'none',
      },
      {
        id: 'default',
        friendRequestBotId: 'default:entity-b',
        name: 'Bot B',
        ownerName: 'Owner B',
        description: '',
        capabilities: [],
        relationshipStatus: 'none',
      },
    ]);

    store.updateBotRelationship('default:entity-a', 'applying');

    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.relationshipStatus)).toEqual([
      'applying',
      'none',
    ]);
  });
});

describe('collaboration square store task slice', () => {
  beforeEach(() => useCollaborationSquareStore.getState().reset());

  test('任务查询、状态筛选与任务列表可读写', () => {
    const store = useCollaborationSquareStore.getState();
    store.setTaskQuery('路线图');
    store.setTaskStatusFilter('claimed');
    store.setTasks([makeTask('task-1'), makeTask('task-2')]);
    expect(useCollaborationSquareStore.getState()).toMatchObject({
      taskQuery: '路线图',
      taskStatusFilter: 'claimed',
      tasks: [makeTask('task-1'), makeTask('task-2')],
    });
  });

  test('resetTaskFilters 清空关键词与状态筛选', () => {
    const store = useCollaborationSquareStore.getState();
    store.setTaskQuery('路线图');
    store.setTaskStatusFilter('completed');
    store.resetTaskFilters();
    expect(useCollaborationSquareStore.getState()).toMatchObject({
      taskQuery: '',
      taskStatusFilter: 'all',
    });
  });

  test('setSelectedTaskId 写入选中并清理旧详情', () => {
    const store = useCollaborationSquareStore.getState();
    store.setTaskDetail(makeTask('task-1'));
    store.setSelectedTaskId('task-2');
    expect(useCollaborationSquareStore.getState()).toMatchObject({
      selectedTaskId: 'task-2',
      taskDetail: null,
    });
  });

  test('removeTask 移除选中任务并清理详情', () => {
    const store = useCollaborationSquareStore.getState();
    store.setTasks([makeTask('task-1'), makeTask('task-2')]);
    store.setSelectedTaskId('task-1');
    store.setTaskDetail(makeTask('task-1'));
    store.removeTask('task-1');
    expect(useCollaborationSquareStore.getState()).toMatchObject({
      tasks: [makeTask('task-2')],
      selectedTaskId: null,
      taskDetail: null,
    });
  });

  test('removeTask 不影响未选中的其它任务', () => {
    const store = useCollaborationSquareStore.getState();
    store.setTasks([makeTask('task-1'), makeTask('task-2')]);
    store.setSelectedTaskId('task-2');
    store.setTaskDetail(makeTask('task-2'));
    store.removeTask('task-1');
    expect(useCollaborationSquareStore.getState()).toMatchObject({
      tasks: [makeTask('task-2')],
      selectedTaskId: 'task-2',
      taskDetail: makeTask('task-2'),
    });
  });

  test('appendTasks 追加新任务并按 id 去重', () => {
    const store = useCollaborationSquareStore.getState();
    store.setTasks([makeTask('task-1')]);
    store.appendTasks([makeTask('task-2'), makeTask('task-1'), makeTask('task-3')]);
    expect(useCollaborationSquareStore.getState().tasks.map((task) => task.id)).toEqual(['task-1', 'task-2', 'task-3']);
  });

  test('reset() 把任务切片恢复为初始值', () => {
    const store = useCollaborationSquareStore.getState();
    store.setTasks([makeTask('task-1')]);
    store.setTaskQuery('路线图');
    store.setTaskStatusFilter('claimed');
    store.setSelectedTaskId('task-1');
    store.setTaskDetail(makeTask('task-1'));
    useCollaborationSquareStore.getState().reset();
    expect(useCollaborationSquareStore.getState()).toMatchObject({
      tasks: [],
      taskQuery: '',
      taskStatusFilter: 'all',
      selectedTaskId: null,
      taskDetail: null,
    });
  });
});

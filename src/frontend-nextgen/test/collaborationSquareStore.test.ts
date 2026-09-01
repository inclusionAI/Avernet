import { useCollaborationSquareStore } from '../src/stores/collaborationSquareStore';

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

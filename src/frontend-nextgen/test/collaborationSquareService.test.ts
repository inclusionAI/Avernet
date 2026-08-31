import type { CollaborationSquareGateway } from '../src/services/collaborationSquare/collaborationSquareGateway';
import {
  CollaborationSquareError,
  CollaborationSquareService,
} from '../src/services/collaborationSquare/collaborationSquareService';

const humanContext = { actorId: 'human_327325', userId: '327325' };

function createGateway(overrides: Partial<CollaborationSquareGateway> = {}): CollaborationSquareGateway {
  return {
    listBots: jest.fn(async () => []),
    discoverBots: jest.fn(async () => []),
    getBotProfile: jest.fn(async () => ({
      id: 'b1',
      name: '助手',
      ownerName: 'Owner',
      description: '',
      capabilities: [],
    })),
    requestBotFriendship: jest.fn(async () => ({ status: 'applying' as const })),
    openBotConversation: jest.fn(async () => ({ sessionId: 'bot-session' })),
    listGroups: jest.fn(async () => []),
    listGroupMembers: jest.fn(async () => []),
    createGroupSession: jest.fn(async () => ({
      sessionId: 's1',
      memberSource: 'session_temp' as const,
      defaultRole: '参与者',
    })),
    ...overrides,
  };
}

describe('collaboration square service', () => {
  test('好友申请按服务端结果更新关系且范围拒绝不伪造 applying', async () => {
    const denied = new CollaborationSquareService(
      createGateway({
        requestBotFriendship: jest.fn(async () => {
          throw new CollaborationSquareError('scope_not_matched', '当前组织不在该 Bot 的好友申请范围内');
        }),
      }),
    );
    await expect(denied.requestBotFriendship('b1', humanContext)).rejects.toMatchObject({ code: 'scope_not_matched' });

    const accepted = new CollaborationSquareService(
      createGateway({
        requestBotFriendship: jest.fn(async () => ({ status: 'friend' as const })),
      }),
    );
    await expect(accepted.requestBotFriendship('b1', humanContext)).resolves.toEqual({ status: 'friend' });
  });

  test('同目标重复好友申请被锁定', async () => {
    let resolveRequest: ((value: { status: 'applying' }) => void) | undefined;
    const gateway = createGateway({
      requestBotFriendship: jest.fn(
        () =>
          new Promise((resolve) => {
            resolveRequest = resolve;
          }),
      ),
    });
    const service = new CollaborationSquareService(gateway);
    const first = service.requestBotFriendship('b1', humanContext);
    await expect(service.requestBotFriendship('b1', humanContext)).rejects.toMatchObject({ code: 'duplicate_action' });
    resolveRequest?.({ status: 'applying' });
    await expect(first).resolves.toEqual({ status: 'applying' });
  });

  test('已是好友时只消费 Adapter 返回的唯一会话并锁定重复导航', async () => {
    let resolveConversation: ((value: { sessionId: string }) => void) | undefined;
    const gateway = createGateway({
      openBotConversation: jest.fn(
        () =>
          new Promise((resolve) => {
            resolveConversation = resolve;
          }),
      ),
    });
    const service = new CollaborationSquareService(gateway);
    const first = service.openBotConversation('b1', humanContext);
    await expect(service.openBotConversation('b1', humanContext)).rejects.toMatchObject({ code: 'duplicate_action' });
    resolveConversation?.({ sessionId: 'conversation-from-adapter' });
    await expect(first).resolves.toEqual({ sessionId: 'conversation-from-adapter' });
  });

  test('创建群会话只消费 Adapter 返回的临时成员来源和默认角色', async () => {
    const service = new CollaborationSquareService(
      createGateway({
        createGroupSession: jest.fn(async () => ({
          sessionId: 'session-42',
          memberSource: 'session_temp' as const,
          defaultRole: '观察者',
        })),
      }),
    );
    await expect(service.createGroupSession('g1')).resolves.toEqual({
      sessionId: 'session-42',
      memberSource: 'session_temp',
      defaultRole: '观察者',
    });
  });

  test('unsupported 能力显式失败', async () => {
    const service = new CollaborationSquareService(
      createGateway({
        listBots: jest.fn(async () => {
          throw new CollaborationSquareError('unsupported', '公开 Bot 服务暂不可用');
        }),
      }),
    );
    await expect(service.listBots()).rejects.toMatchObject({ code: 'unsupported' });
  });

  test('Bot 搜索查询与取消信号原样交给 Gateway', async () => {
    const listBots = jest.fn(async () => []);
    const service = new CollaborationSquareService(createGateway({ listBots }));
    const signal = new AbortController().signal;

    await service.listBots({ search: 'workflow', page: 2, pageSize: 10 }, humanContext, signal);

    expect(listBots).toHaveBeenCalledWith({ search: 'workflow', page: 2, pageSize: 10 }, humanContext, signal);
  });

  test('Bot Discovery 与公开群查询原样交给 Gateway', async () => {
    const discoverBots = jest.fn(async () => []);
    const listGroups = jest.fn(async () => []);
    const service = new CollaborationSquareService(createGateway({ discoverBots, listGroups }));
    const signal = new AbortController().signal;

    await service.discoverBots(
      { keyword: '会议纪要', topK: 20, minScore: 0.1, runtimeState: 'online' },
      humanContext,
      signal,
    );
    await service.listGroups({ search: '公开', offset: 0, limit: 20 }, signal);

    expect(discoverBots).toHaveBeenCalledWith(
      { keyword: '会议纪要', topK: 20, minScore: 0.1, runtimeState: 'online' },
      humanContext,
      signal,
    );
    expect(listGroups).toHaveBeenCalledWith({ search: '公开', offset: 0, limit: 20 }, signal);
  });
});

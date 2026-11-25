import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { listCollaboratingBots } from '@/services/backendApi/bots/collaboratingBotController';
import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { listFriendConnections } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { teamBotConversationService } from '@/services/workspace/teamBotConversationService';
jest.mock('@/services/backendApi/bots/collaboratingBotController');
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/backendApi/bots/privateBotSessionController');
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController');
const session = {
  session_id: 'session-1',
  agent_id: 'shared',
  model: 'model-a',
  title: '旧标题',
  message_count: 1,
  gmt_create: '2026-10-10',
  gmt_modified: '2026-10-10',
};
let bot: ChatBotView;
beforeEach(async () => {
  jest.clearAllMocks();
  jest.mocked(queryCollaborationBots).mockResolvedValue({ code: 200000, data: { items: [] } });
  jest.mocked(listCollaboratingBots).mockResolvedValue({
    code: 200000,
    data: {
      total: 1,
      items: [
        {
          bot_id: 'shared',
          bot_name: '团队',
          entity_id: 'team-entity',
          owner_id: 'actual-owner',
          bot_desc: '',
          engine: 'openclaw',
          cluster_name: 'ACRA',
          bot_type: 'personal',
          status: 'ACTIVE',
          collaboration: { id: 1, role: 'member', joined_at: '' },
        },
      ],
    },
  });
  const result = await teamBotConversationService.listBots('login-viewer');
  if (!result.ok) throw new Error('fixture failed');
  bot = result.data[0];
  for (const key of ['listBotSessions', 'listFavoriteSessions', 'listBotSessionMessages', 'listBotModels'] as const)
    jest.mocked(ctrl[key]).mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
  jest.mocked(ctrl.createBotSession).mockResolvedValue({ code: 201000, data: session });
  jest.mocked(ctrl.getBotSession).mockResolvedValue({ code: 200000, data: session });
  jest.mocked(ctrl.updateBotSession).mockResolvedValue({ code: 200000, data: session });
  jest
    .mocked(ctrl.favoriteBotSession)
    .mockResolvedValue({ code: 200000, data: { session_id: 'session-1', favorited: true } });
});
const params = { user_id: 'login-viewer', owner_id: 'team-entity' };
test('mine 列表、创建、详情、消息和模型都使用登录用户+实体 owner，无 f_user_id', async () => {
  await botSessionService.listSessionsPage(bot, 'login-viewer');
  await botSessionService.createSession(bot, 'login-viewer');
  await botSessionService.getSessionDetail(bot, 'login-viewer', 'session-1');
  await botSessionService.listMessagesPage(bot, 'login-viewer', 'session-1');
  await botSessionService.listModels(bot, 'login-viewer');
  expect(ctrl.listBotSessions).toHaveBeenCalledWith('shared', { ...params, page: 1, page_size: 10 });
  expect(ctrl.createBotSession).toHaveBeenCalledWith('shared', params, { title: '新会话' });
  expect(ctrl.getBotSession).toHaveBeenCalledWith('shared', 'session-1', params);
  expect(ctrl.listBotSessionMessages).toHaveBeenCalledWith('shared', 'session-1', {
    ...params,
    page: 1,
    page_size: 50,
  });
  expect(ctrl.listBotModels).toHaveBeenCalledWith('shared', { ...params, page: 1, page_size: 50 });
});
test('团队改名/清理/删除/收藏/模型修改必须显式带 entity owner，不可依赖当前登录用户推断', async () => {
  await botSessionService.updateSessionTitle(bot, 'login-viewer', 'session-1', '新标题');
  await botSessionService.clearContext(bot, 'login-viewer', 'session-1');
  await botSessionService.deleteSession(bot, 'login-viewer', 'session-1');
  await botSessionService.toggleFavorite(bot, 'login-viewer', 'session-1', true);
  await botSessionService.updateSessionModel(bot, 'login-viewer', 'session-1', 'model-a');
  expect(ctrl.updateBotSession).toHaveBeenCalledWith('shared', 'session-1', params, { title: '新标题' });
  expect(ctrl.deleteBotSessionMessages).toHaveBeenCalledWith('shared', 'session-1', params);
  expect(ctrl.deleteBotSession).toHaveBeenCalledWith('shared', 'session-1', params);
  expect(ctrl.favoriteBotSession).toHaveBeenCalledWith('shared', 'session-1', params);
  expect(ctrl.updateBotSession).toHaveBeenCalledWith('shared', 'session-1', params, { model: 'model-a' });
});
test('协作 Bot 他人会话加载 Bot 好友目录，使用实体复合身份并归一化好友 ID', async () => {
  jest.mocked(listFriendConnections).mockResolvedValue({
    code: 200000,
    data: {
      items: [{ actor: { type: 'human', id: 'human_reader' }, name: '协作成员' }],
      total: 1,
    },
  });
  const controller = new AbortController();
  expect(await managedBotConversationService.loadFriendUsers(bot, controller.signal)).toEqual({
    ok: true,
    data: [{ userId: 'reader', displayName: '协作成员' }],
  });
  expect(listFriendConnections).toHaveBeenCalledWith(
    { actor_type: 'bot', actor_id: 'shared:team-entity', target_type: 'human', page: 1, page_size: 100 },
    controller.signal,
  );
});

test.each(['personal', 'service'])(
  '协作 %s Bot 他人会话与历史沿用实体 owner、好友过滤和分页，只提供读取',
  async (botType) => {
    const otherBot = { ...bot, botType, runtimeStage: 'online' as const };
    const stage = botType === 'service' ? { stage: 'online' as const } : {};
    jest.mocked(ctrl.listBotSessions).mockResolvedValue({ code: 200000, data: { items: [session], total: 8 } });
    jest.mocked(ctrl.listBotSessionMessages).mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            message_id: 'm2',
            session_id: 'session-1',
            role: 'assistant',
            content: '回复',
            gmt_create: '2026-11-16T10:01:00Z',
          },
          {
            message_id: 'm1',
            session_id: 'session-1',
            role: 'user',
            content: '问题',
            gmt_create: '2026-11-16T10:00:00Z',
          },
        ],
        total: 5,
      },
    });
    const sessions = await managedBotConversationService.listOtherSessions(
      otherBot,
      'human_reader',
      2,
      3,
      'agent:main:key',
    );
    expect(sessions).toEqual({
      ok: true,
      data: expect.objectContaining({
        items: [expect.objectContaining({ sessionId: 'session-1', botId: 'shared:team-entity' })],
        page: 2,
        total: 8,
        hasMore: true,
      }),
    });
    expect(ctrl.listBotSessions).toHaveBeenCalledWith('shared', {
      user_id: 'team-entity',
      owner_id: 'team-entity',
      f_user_id: 'reader',
      ...stage,
      page: 2,
      page_size: 3,
      session_key: 'agent:main:key',
    });
    const messages = await managedBotConversationService.listOtherMessages(otherBot, 'human_reader', 'session-1', 2, 2);
    expect(messages).toEqual({
      ok: true,
      data: {
        messages: [expect.objectContaining({ id: 'm1' }), expect.objectContaining({ id: 'm2' })],
        page: 2,
        total: 5,
        rawCount: 2,
        hasMore: true,
      },
    });
    expect(ctrl.listBotSessionMessages).toHaveBeenCalledWith('shared', 'session-1', {
      user_id: 'team-entity',
      owner_id: 'team-entity',
      f_user_id: 'reader',
      ...stage,
      page: 2,
      page_size: 2,
    });
    for (const method of [
      'createBotSession',
      'updateBotSession',
      'deleteBotSessionMessages',
      'deleteBotSession',
      'favoriteBotSession',
    ] as const) {
      expect(ctrl[method]).not.toHaveBeenCalled();
    }
  },
);

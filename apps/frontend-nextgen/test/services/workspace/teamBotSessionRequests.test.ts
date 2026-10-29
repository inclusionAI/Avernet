import { listCollaboratingBots } from '@/services/backendApi/bots/collaboratingBotController';
import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { listFriendConnections } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { teamBotConversationService } from '@/services/workspace/teamBotConversationService';
jest.mock('@/services/backendApi/bots/collaboratingBotController');
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
test('团队他人功能开发中：旧深链/缓存也不能请求好友目录、会话或历史', async () => {
  const unavailable = {
    ok: false,
    error: { code: 'TEAM_OTHERS_UNAVAILABLE', friendlyMessage: '他人发起的会话功能开发中', canRetry: false },
  };
  expect(await managedBotConversationService.loadFriendUsers(bot)).toEqual(unavailable);
  expect(await managedBotConversationService.listOtherSessions(bot, 'human_reader')).toEqual(unavailable);
  expect(await managedBotConversationService.listOtherMessages(bot, 'human_reader', 'session-1')).toEqual(unavailable);
  expect(listFriendConnections).not.toHaveBeenCalled();
  expect(ctrl.listBotSessions).not.toHaveBeenCalled();
  expect(ctrl.listBotSessionMessages).not.toHaveBeenCalled();
  expect(ctrl.createBotSession).not.toHaveBeenCalled();
});

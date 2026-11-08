import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { botSessionService, resolveChatRuntimeStage, type ChatBotView } from '@/services/workspace/botSessionService';

jest.mock('@/services/backendApi/bots/privateBotSessionController');

const bot: ChatBotView = {
  botId: 'service:owner', realBotId: 'service', ownerId: 'owner', botType: 'service',
  displayName: 'Service Bot', online: true, chatable: true, isTeamBot: true,
};
const session = { session_id: 's', title: '验证', model: 'm', agent_id: '', message_count: 0, gmt_create: '', gmt_modified: '' };

beforeEach(() => {
  jest.resetAllMocks();
  for (const method of ['listBotSessions', 'listFavoriteSessions', 'listBotSessionMessages', 'listBotModels'] as const) {
    jest.mocked(ctrl[method]).mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
  }
  for (const method of ['createBotSession', 'getBotSession', 'updateBotSession'] as const) {
    jest.mocked(ctrl[method]).mockResolvedValue({ code: 200000, data: session });
  }
  jest.mocked(ctrl.deleteBotSession).mockResolvedValue({ code: 200000, data: { deleted: true } });
  jest.mocked(ctrl.deleteBotSessionMessages).mockResolvedValue({ code: 200000, data: { deleted: true } });
  jest.mocked(ctrl.favoriteBotSession).mockResolvedValue({ code: 200000, data: { session_id: 's', favorited: true } });
  jest.mocked(ctrl.unfavoriteBotSession).mockResolvedValue({ code: 200000, data: { session_id: 's', favorited: false } });
});

test.each(['draft', 'verify', 'online'] as const)('团队服务 Bot 的历史 %s 阶段在所有会话请求中统一使用 online', async (stage) => {
  const target = { ...bot, runtimeStage: stage };
  const params = expect.objectContaining({ user_id: 'user', owner_id: 'owner', stage: 'online' });
  await botSessionService.listSessions(target, 'user');
  await botSessionService.listSessionsPage(target, 'user', 2);
  await botSessionService.createSession(target, 'user');
  await botSessionService.getSessionDetail(target, 'user', 's');
  await botSessionService.listMessagesPage(target, 'user', 's', 2);
  await botSessionService.listModels(target, 'user');
  await botSessionService.updateSessionModel(target, 'user', 's', 'new-model');
  await botSessionService.updateSessionTitle(target, 'user', 's', '新标题');
  await botSessionService.clearContext(target, 'user', 's');
  await botSessionService.deleteSession(target, 'user', 's');
  await botSessionService.toggleFavorite(target, 'user', 's', true);
  await botSessionService.toggleFavorite(target, 'user', 's', false);
  await botSessionService.listFavoriteSessionsPage(target, 'user');
  expect(ctrl.listBotSessions).toHaveBeenNthCalledWith(1, 'service', params);
  expect(ctrl.listBotSessions).toHaveBeenNthCalledWith(2, 'service', params);
  expect(ctrl.createBotSession).toHaveBeenCalledWith('service', params, { title: '新会话' });
  expect(ctrl.getBotSession).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.listBotSessionMessages).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.listBotModels).toHaveBeenCalledWith('service', params);
  expect(ctrl.updateBotSession).toHaveBeenNthCalledWith(1, 'service', 's', params, { model: 'new-model' });
  expect(ctrl.updateBotSession).toHaveBeenNthCalledWith(2, 'service', 's', params, { title: '新标题' });
  expect(ctrl.deleteBotSessionMessages).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.deleteBotSession).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.favoriteBotSession).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.unfavoriteBotSession).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.listFavoriteSessions).toHaveBeenCalledWith('service', params);
});

test('团队/好友服务 Bot 使用 online，个人 Bot 省略默认阶段', async () => {
  expect(resolveChatRuntimeStage(bot)).toBe('online');
  expect(resolveChatRuntimeStage({ ...bot, botType: 'personal', runtimeStage: 'online' })).toBe('draft');
  expect(resolveChatRuntimeStage({ ...bot, isFriendBot: true, runtimeStage: 'verify' })).toBe('online');
  expect(resolveChatRuntimeStage({ ...bot, isFriendBot: true, botType: 'personal', runtimeStage: 'online' })).toBe('draft');
  await botSessionService.listSessions({ ...bot, botType: 'personal', runtimeStage: 'online' }, 'user');
  expect(jest.mocked(ctrl.listBotSessions).mock.calls[0][1]).not.toHaveProperty('stage');
});

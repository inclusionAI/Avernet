import { buildChatEngineVersionTarget } from '@/domain/chatEngineVersionTarget';
import type { ChatBotView } from '@/services/workspace/botSessionService';

const bot: ChatBotView = {
  botId: 'bot-1:owner-1',
  realBotId: 'bot-1',
  ownerId: 'owner-1',
  displayName: 'Bot',
  online: true,
  status: 'ACTIVE',
  chatable: true,
  engine: 'OpenClaw',
  actions: ['restart'],
};

const input = {
  bot,
  section: 'managed' as const,
  origin: 'mine' as const,
  sessionId: 'session-1',
  userId: 'caller-1',
};

test('只为在线的本人或团队 Bot 单聊建立版本目标并保留权限依据', () => {
  expect(buildChatEngineVersionTarget(input)).toEqual({
    permissionSource: 'backend',
    key: 'chat:caller-1:managed:bot-1:owner-1:session-1',
    botId: 'bot-1',
    userId: 'caller-1',
    ownerId: 'owner-1',
    engine: 'OpenClaw',
    enabled: true,
    restartPermission: {
      actions: ['restart'],
      disabledActions: undefined,
      serviceBot: false,
      runtimeStage: 'draft',
    },
  });
  expect(buildChatEngineVersionTarget({ ...input, section: 'team' })).toMatchObject({ ownerId: 'owner-1' });
});

test.each([
  ['desktop OpenClaw Bot', { bot: { ...bot, botType: 'desktop', engine: 'openclaw' } }],
  ['desktop Hermes Bot', { bot: { ...bot, botType: 'desktop', engine: 'hermes' } }],
  ['uppercase padded Desktop Bot', { bot: { ...bot, botType: ' DESKTOP ' } }],
  ['friend Bot', { bot: { ...bot, isFriendBot: true } }],
  ['offline Bot', { bot: { ...bot, online: false } }],
  ['hidden Bot', { bot: { ...bot, status: 'HIDDEN' } }],
  ['other owner conversation', { origin: 'others' as const }],
  ['no session', { sessionId: null }],
  ['no owner context', { bot: { ...bot, ownerId: undefined } }],
  ['unsupported engine', { bot: { ...bot, engine: 'teclaw' } }],
])('does not create a version target for %s', (_name, override) => {
  expect(buildChatEngineVersionTarget({ ...input, ...override })).toBeNull();
});

test('保留 Service 计算权限所需的后端动作与阶段字段', () => {
  const unauthorized = buildChatEngineVersionTarget({ ...input, bot: { ...bot, actions: [] } });
  expect(unauthorized?.restartPermission.actions).toEqual([]);

  const onlineService = buildChatEngineVersionTarget({
    ...input,
    bot: { ...bot, botType: 'service', runtimeStage: 'online' },
  });
  expect(onlineService?.permissionSource).toBe('backend');

  const draftService = buildChatEngineVersionTarget({
    ...input,
    bot: { ...bot, botType: 'service', serviceLifecycle: 'draft', runtimeStage: 'draft' },
  });
  expect(draftService?.restartPermission).toMatchObject({ serviceBot: true, runtimeStage: 'draft' });
});

test('仅按后端引擎支持边界判断，不额外排除 OpenClaw/Hermes 模板 Bot', () => {
  expect(buildChatEngineVersionTarget({ ...input, bot: { ...bot, isAgentCodingBot: true } })).not.toBeNull();
});

test.each(['service', 'SERVICE', ' Service '])('服务 Bot %s 没有可信草稿阶段时由后端判定', (botType) => {
  expect(buildChatEngineVersionTarget({ ...input, bot: { ...bot, botType } })?.permissionSource).toBe('backend');
  expect(buildChatEngineVersionTarget({ ...input, bot: { ...bot, botType, runtimeStage: 'verify' } })?.permissionSource).toBe('backend');
});

test('团队引擎请求使用真实 Bot owner，保留实体 ID 作为聊天身份', () => {
  const teamBot = { ...bot, isTeamBot: true, ownerId: 'record-entity', engineOwnerId: 'bot-owner' };
  expect(buildChatEngineVersionTarget({ ...input, section: 'team', bot: teamBot })).toMatchObject({ ownerId: 'bot-owner', botId: 'bot-1', userId: 'caller-1' });
  expect(teamBot.ownerId).toBe('record-entity');
  expect(buildChatEngineVersionTarget({ ...input, section: 'team', bot: { ...teamBot, engineOwnerId: undefined } })).toBeNull();
});

test('团队服务 Bot 的在线对话不触发仅支持 draft 的引擎版本检查', () => {
  expect(
    buildChatEngineVersionTarget({
      ...input,
      section: 'team',
      bot: {
        ...bot,
        botType: 'service',
        isTeamBot: true,
        engineOwnerId: 'owner',
        runtimeStage: 'online',
      },
    }),
  ).toBeNull();
  expect(
    buildChatEngineVersionTarget({ ...input, bot: { ...bot, botType: 'service', runtimeStage: 'draft' } }),
  ).not.toBeNull();
});

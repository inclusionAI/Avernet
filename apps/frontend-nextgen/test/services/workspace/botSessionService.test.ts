/** @jest-environment jsdom */
import type { IdentityView } from '@/domain/collaboration';
import * as botCtrl from '@/services/backendApi/bots/botController';
import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import {
  botSessionService,
  extractSessionUuidFromSessionId,
  listChatBots,
  mapBotSessionSummary,
  splitBotId,
} from '@/services/workspace/botSessionService';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/privateBotSessionController');
jest.mock('@/services/backendApi/bots/botController');
const ownedMocked = botCtrl as unknown as Record<string, jest.Mock<any>>;
const mocked = ctrl as unknown as Record<string, jest.Mock<any>>;

describe('splitBotId', () => {
  it('复合 id 拆分', () => {
    expect(splitBotId('bot-1:2088')).toEqual({ realBotId: 'bot-1', ownerId: '2088' });
  });
  it('无冒号整体作为 realBotId,无 ownerId', () => {
    expect(splitBotId('bot-1')).toEqual({ realBotId: 'bot-1', ownerId: undefined });
  });
  it('首尾空段防御', () => {
    expect(splitBotId(':2088')).toEqual({ realBotId: '', ownerId: '2088' });
    expect(splitBotId('bot-1:')).toEqual({ realBotId: 'bot-1', ownerId: '' });
  });
});

describe('listChatBots', () => {
  const views: IdentityView[] = [
    { id: 'me', kind: 'user', displayName: '我', online: true },
    {
      id: '20260402_ab:2088',
      kind: 'bot',
      displayName: '可聊Bot',
      online: true,
      status: 'online',
      reachability: 'reachable',
      engine: 'OpenClaw',
    },
    { id: 'plain-bot', kind: 'bot', displayName: '不可聊Bot', online: true },
  ];
  it('过滤 user,保留 bot,标注 chatable', () => {
    const bots = listChatBots(views);
    expect(bots).toHaveLength(2);
    expect(bots[0]).toMatchObject({
      botId: '20260402_ab:2088',
      realBotId: '20260402_ab',
      ownerId: '2088',
      chatable: true,
      engine: 'OpenClaw',
    });
    expect(bots[1]).toMatchObject({ botId: 'plain-bot', realBotId: 'plain-bot', ownerId: undefined, chatable: false });
  });
});

describe('botSessionService', () => {
  const bot = {
    botId: '20260402_ab:2088',
    realBotId: '20260402_ab',
    ownerId: '2088',
    displayName: 'B',
    online: true,
    chatable: true,
  };

  it('listOwnedBots 保留 AgentCoding Bot，并保留普通 CC', async () => {
    ownedMocked.listBots.mockResolvedValue({
      data: {
        items: [
          {
            bot_id: 'application:1',
            bot_name: '应用实例',
            engine_type: 'claude_code',
            template_type: 'applicationCoding',
            owner_entity_id: '1',
            status: 'ACTIVE',
          },
          {
            bot_id: 'personal:2',
            bot_name: '个人实例',
            engine_type: 'aicoding',
            template_type: 'personalCoding',
            owner_entity_id: '2',
            status: 'ACTIVE',
          },
          {
            bot_id: 'normal:3',
            bot_name: '普通 CC',
            engine_type: 'claude_code',
            template_type: 'normalCC',
            owner_entity_id: '3',
            status: 'ACTIVE',
            actions: ['restart'],
            disabled_actions: { restart_publish: '仅发布 Bot 支持' },
          },
          {
            bot_id: 'normal-empty:4',
            bot_name: '无模板 CC',
            engine_type: 'claude_code',
            owner_entity_id: '4',
            status: 'ACTIVE',
          },
          {
            bot_id: 'general:5',
            bot_name: 'General Coding Bot',
            engine_type: 'claude_code',
            template_type: 'generalCC',
            owner_entity_id: '5',
            status: 'ACTIVE',
          },
        ],
      },
    });

    const res = await botSessionService.listOwnedBots('human_900003');
    expect(res.ok).toBe(true);
    expect(res.ok && res.data).toEqual([
      expect.objectContaining({
        displayName: '应用实例',
        isAgentCodingBot: true,
        templateType: 'applicationCoding',
      }),
      expect.objectContaining({
        displayName: '个人实例',
        isAgentCodingBot: true,
        templateType: 'personalCoding',
      }),
      expect.objectContaining({
        displayName: '普通 CC',
        isAgentCodingBot: false,
        templateType: 'normalCC',
        actions: ['restart'],
        disabledActions: { restart_publish: '仅发布 Bot 支持' },
        runtimeStage: 'draft',
      }),
      expect.objectContaining({
        displayName: '无模板 CC',
        isAgentCodingBot: false,
        templateType: undefined,
      }),
      expect.objectContaining({
        displayName: 'General Coding Bot',
        isAgentCodingBot: true,
        templateType: 'generalCC',
      }),
    ]);
  });

  it('服务 Bot 的在线目录使用 online，但不从 inventory 扩大操作权限', async () => {
    ownedMocked.listBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'service-1',
            bot_name: 'Service',
            engine: 'OpenClaw',
            bot_type: 'service',
            owner_entity_id: 'owner-1',
            status: 'ACTIVE',
          },
        ],
        total: 1,
      },
    });
    ownedMocked.listBotInventory.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'service-1',
            display_state: 'service_draft',
            owner_entity_id: 'owner-1',
            actions: ['restart'],
          },
        ],
        total: 1,
      },
    });
    expect(await botSessionService.listOwnedBots('viewer')).toMatchObject({
      ok: true,
      data: [
        {
          botType: 'service',
          runtimeStage: 'online',
          actions: [],
          ownerId: 'owner-1',
        },
      ],
    });
  });

  it('listOwnedBots 透传 bots 接口原始 status(视图侧判定 Bot 可用性)', async () => {
    ownedMocked.listBots.mockResolvedValue({
      data: {
        items: [
          {
            bot_id: 'normal:3',
            bot_name: '正常',
            engine_type: 'claude_code',
            template_type: 'normalCC',
            owner_entity_id: '3',
            status: 'ACTIVE',
          },
          {
            bot_id: 'normal:4',
            bot_name: '创建中',
            engine_type: 'claude_code',
            template_type: 'normalCC',
            owner_entity_id: '4',
            status: 'PENDING',
          },
          {
            bot_id: 'normal:5',
            bot_name: '异常',
            engine_type: 'claude_code',
            template_type: 'normalCC',
            owner_entity_id: '5',
            status: 'FAILED',
          },
        ],
      },
    });
    const res = await botSessionService.listOwnedBots('human_900003');
    expect(res.ok).toBe(true);
    const views = (res.ok && res.data) as Array<{ displayName: string; status?: string }>;
    expect(views.map((view) => [view.displayName, view.status])).toEqual([
      ['正常', 'ACTIVE'],
      ['创建中', 'PENDING'],
      ['异常', 'FAILED'],
    ]);
  });

  it('listSessions 调用 controller 并映射为 BotChatSessionView', async () => {
    mocked.listBotSessions.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            session_id: 'sid-2',
            title: '会话2',
            agent_id: '',
            model: '',
            message_count: 3,
            gmt_create: '',
            gmt_modified: '2026-08-14T10:00:00+00:00',
          },
          {
            session_id: 'sid-1',
            title: '',
            agent_id: '',
            model: '',
            message_count: 1,
            gmt_create: '',
            gmt_modified: '2026-08-14T09:00:00+00:00',
          },
        ],
        total: 2,
      },
      message: 'OK',
      request_id: 'r',
    });
    const res = await botSessionService.listSessions(bot, 'human_900003');
    expect(mocked.listBotSessions).toHaveBeenCalledWith('20260402_ab', {
      user_id: '900003',
      owner_id: '2088',
      f_user_id: '900003',
      page: 1,
      page_size: 50,
    });
    expect(res.ok).toBe(true);
    const items = (res as any).data;
    expect(items[0]).toMatchObject({ sessionId: 'sid-2', botId: '20260402_ab:2088', title: '会话2', messageCount: 3 });
    // 空 title 回退「新会话」（与 open-claw 一致）
    expect(items[1].title).toBe('新会话');
  });

  it('createSession 透传 title 并返回新会话', async () => {
    mocked.createBotSession.mockResolvedValue({
      code: 200000,
      data: {
        session_id: 'sid-new',
        title: '新',
        agent_id: '',
        model: '',
        message_count: 0,
        gmt_create: '',
        gmt_modified: 'x',
      },
      message: 'OK',
      request_id: 'r',
    });
    const res = await botSessionService.createSession(bot, 'human_900003', '新');
    expect(mocked.createBotSession).toHaveBeenCalledWith(
      '20260402_ab',
      { user_id: '900003', owner_id: '2088', f_user_id: '900003' },
      { title: '新' },
    );
    expect((res as any).data.sessionId).toBe('sid-new');
  });

  it('createSession 未指定标题时显式传新会话', async () => {
    mocked.createBotSession.mockResolvedValue({
      code: 200000,
      data: {
        session_id: 'sid-default-title',
        title: '新会话',
        agent_id: '',
        model: '',
        message_count: 0,
        gmt_create: '',
        gmt_modified: '',
      },
      message: 'OK',
      request_id: 'r',
    });

    await botSessionService.createSession(bot, 'human_900003');

    expect(mocked.createBotSession).toHaveBeenCalledWith(
      '20260402_ab',
      { user_id: '900003', owner_id: '2088', f_user_id: '900003' },
      { title: '新会话' },
    );
  });

  it('Bot 工坊运维请求保留 stage 且不扩大为 f_user_id 会话主体', async () => {
    const debugBot = { ...bot, botType: 'service', runtimeStage: 'verify' as const };
    mocked.listBotSessions.mockResolvedValue({ data: { items: [], total: 0 } });
    mocked.createBotSession.mockResolvedValue({
      data: { session_id: 'debug', title: 'Debug', message_count: 0, gmt_create: '', gmt_modified: '' },
    });
    mocked.deleteBotSessionMessages.mockResolvedValue({ data: { deleted: true } });
    mocked.listBotModels.mockResolvedValue({ data: { items: [], total: 0 } });

    await botSessionService.listSessions(debugBot, 'human_900003', false);
    await botSessionService.createSession(debugBot, 'human_900003', 'Debug', { subjectScoped: false });
    await botSessionService.clearContext(debugBot, 'human_900003', 'debug', false);
    await botSessionService.listModels(debugBot, 'human_900003', false);

    const params = { user_id: '900003', owner_id: '2088', stage: 'verify' };
    expect(mocked.listBotSessions).toHaveBeenLastCalledWith('20260402_ab', {
      ...params, page: 1, page_size: 50,
    });
    expect(mocked.createBotSession).toHaveBeenLastCalledWith(
      '20260402_ab', params, { title: 'Debug' },
    );
    expect(mocked.deleteBotSessionMessages).toHaveBeenLastCalledWith(
      '20260402_ab', 'debug', { user_id: '900003', stage: 'verify' },
    );
    expect(mocked.listBotModels).toHaveBeenLastCalledWith('20260402_ab', {
      ...params, page: 1, page_size: 50,
    });
  });

  it('Caller 首次准备中时只按专用子码轮询并在就绪后返回会话', async () => {
    jest.useFakeTimers();
    try {
      mocked.createBotSession.mockReset();
      mocked.createBotSession
        .mockRejectedValueOnce(new BackendRequestError('Caller runtime is preparing', {
          status: 409,
          data: { code: 409113, message: 'Caller runtime is preparing' },
          apiPath: '/openapi/v1/bots/b/sessions',
          toastKey: 'preparing',
          alreadyHandled: true,
        }))
        .mockResolvedValueOnce({ data: {
          session_id: 'ready', title: '新会话', message_count: 0, gmt_create: '', gmt_modified: '',
        } });

      const result = botSessionService.createSession(bot, 'human_900003', undefined, {
        friendlyErrors: true,
        waitForCallerRuntime: true,
      });
      await jest.advanceTimersByTimeAsync(3_000);

      await expect(result).resolves.toMatchObject({ ok: true, data: { sessionId: 'ready' } });
      expect(mocked.createBotSession).toHaveBeenCalledTimes(2);
    } finally {
      jest.useRealTimers();
    }
  });

  it('普通设备未就绪 409000 保持原失败语义且不轮询', async () => {
    mocked.createBotSession.mockReset();
    mocked.createBotSession.mockRejectedValueOnce(new BackendRequestError('Bot device is not ready', {
      status: 409,
      data: { code: 409000, message: 'Bot device is not ready' },
      apiPath: '/openapi/v1/bots/b/sessions',
      alreadyHandled: true,
    }));

    const result = await botSessionService.createSession(bot, 'human_900003', undefined, {
      friendlyErrors: true,
      waitForCallerRuntime: true,
    });

    expect(result).toMatchObject({ ok: false, error: { friendlyMessage: 'Bot device is not ready' } });
    expect(mocked.createBotSession).toHaveBeenCalledTimes(1);
  });

  it('deleteSession 调用 controller', async () => {
    mocked.deleteBotSession.mockResolvedValue({
      code: 200000,
      data: { deleted: true },
      message: 'OK',
      request_id: 'r',
    });
    const res = await botSessionService.deleteSession(bot, 'human_900003', 'sid-9');
    expect(mocked.deleteBotSession).toHaveBeenCalledWith('20260402_ab', 'sid-9', {
      user_id: '900003',
      owner_id: '2088',
      f_user_id: '900003',
    });
    expect(res.ok).toBe(true);
  });

  it('listMessages 拉取并映射为 ChatMessage[](旧→新升序,user_id 含冒号取尾段)', async () => {
    mocked.listBotSessionMessages.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            message_id: 'm2',
            session_id: 's',
            role: 'assistant',
            content: 'r',
            gmt_create: '2026-08-14T09:01:00+00:00',
          },
          { message_id: 'm1', session_id: 's', role: 'user', content: 'q', gmt_create: '2026-08-14T09:00:00+00:00' },
        ],
        total: 2,
      },
      message: 'OK',
      request_id: 'r',
    });
    const out = await botSessionService.listMessages(bot, 'me:2088', 's');
    expect(mocked.listBotSessionMessages).toHaveBeenCalledWith('20260402_ab', 's', {
      user_id: '2088',
      owner_id: '2088',
      f_user_id: '2088',
      page: 1,
      page_size: 50,
    });
    expect(out).toHaveLength(2);
    expect(out[0].id).toBe('m1');
  });

  it('listMessages 对 human_ 前缀的 user_id 只传工号(回归:不再带 human_ 前缀)', async () => {
    mocked.listBotSessionMessages.mockResolvedValue({
      code: 200000,
      data: { items: [], total: 0 },
      message: 'OK',
      request_id: 'r',
    });
    await botSessionService.listMessages(bot, 'human_900003', 's');
    expect(mocked.listBotSessionMessages).toHaveBeenCalledWith('20260402_ab', 's', {
      user_id: '900003',
      owner_id: '2088',
      f_user_id: '900003',
      page: 1,
      page_size: 50,
    });
  });

  it('listSessions 失败返回 DomainResult 错误', async () => {
    mocked.listBotSessions.mockRejectedValue(new Error('boom'));
    const res = await botSessionService.listSessions(bot, 'u');
    expect(res.ok).toBe(false);
  });

  it('listModels 调用 GET /bots/:bot_id/models 并映射模型', async () => {
    mocked.listBotModels.mockResolvedValue({
      code: 200000,
      data: { items: [{ model_id: 'openai/gpt-5.3', name: 'GPT-5.3', provider: 'openai' }], total: 1 },
      message: 'OK',
      request_id: 'r',
    });
    const res = await botSessionService.listModels(bot, 'human_900003');
    expect(mocked.listBotModels).toHaveBeenCalledWith('20260402_ab', {
      user_id: '900003',
      owner_id: '2088',
      f_user_id: '900003',
      page: 1,
      page_size: 50,
    });
    expect(res.ok && res.data[0]).toMatchObject({ modelId: 'openai/gpt-5.3', name: 'GPT-5.3', provider: 'openai' });
  });

  it('updateSessionModel 调用 PATCH 并返回更新后的会话', async () => {
    mocked.updateBotSession.mockResolvedValue({
      code: 200000,
      data: {
        session_id: 's1',
        title: '标题',
        agent_id: 'main',
        model: 'openai/gpt-5.3',
        message_count: 1,
        gmt_create: '',
        gmt_modified: '',
      },
      message: 'OK',
      request_id: 'r',
    });
    const res = await botSessionService.updateSessionModel(bot, 'human_900003', 's1', 'openai/gpt-5.3');
    expect(mocked.updateBotSession).toHaveBeenCalledWith(
      '20260402_ab',
      's1',
      { user_id: '900003', f_user_id: '900003' },
      { model: 'openai/gpt-5.3' },
    );
    expect(res.ok && res.data.model).toBe('openai/gpt-5.3');
  });

  it('自有个人 Bot 的历史会话详情与消息使用管理会话接口，保留完整 session key', async () => {
    const sessionId = 'agent:main:session:67a16490-f599-4c18-afcc-575ec508f8f5:user:2088';
    ownedMocked.listBots.mockResolvedValue({ data: { items: [{
      bot_id: bot.realBotId, bot_type: 'personal', owner_entity_id: '2088',
      entity_id: 'existing-locator', bot_name: '个人 Bot', status: 'ACTIVE',
    }] } });
    mocked.getBotSession.mockResolvedValue({ data: { session_id: sessionId } });
    mocked.listBotSessionMessages.mockResolvedValue({ data: { items: [], total: 0 } });
    const result = await botSessionService.listOwnedBots('human_2088');
    if (!result.ok) throw new Error('自有 Bot 目录加载失败');
    const [personalBot] = result.data;
    const params = { user_id: '2088', owner_id: 'existing-locator' };

    await expect(botSessionService.getSessionDetail(personalBot, 'human_2088', sessionId)).resolves.toMatchObject({
      ok: true, data: { sessionId },
    });
    await botSessionService.listMessagesPage(personalBot, 'human_2088', sessionId);

    expect(mocked.getBotSession).toHaveBeenLastCalledWith(bot.realBotId, sessionId, params);
    expect(mocked.listBotSessionMessages).toHaveBeenLastCalledWith(bot.realBotId, sessionId, {
      ...params, page: 1, page_size: 50,
    });
  });

  it.each([
    { label: '好友 Bot', flags: { isFriendBot: true }, userId: '2088' },
    { label: '团队 Bot', flags: { isTeamBot: true }, userId: '2088' },
    { label: '协作 Bot', flags: {}, userId: '900003' },
    { label: 'Caller Bot', flags: { callType: 'caller' }, userId: '2088' },
  ])('$label 继续携带会话主体', async ({ flags, userId }) => {
    const target = { ...bot, botType: 'personal', engineOwnerId: '2088', ...flags };
    mocked.listBotSessionMessages.mockResolvedValue({ data: { items: [], total: 0 } });
    await botSessionService.listMessagesPage(target, userId, 's1');
    expect(mocked.listBotSessionMessages).toHaveBeenLastCalledWith(
      target.realBotId, 's1', expect.objectContaining({ f_user_id: userId }),
    );
  });

  it('好友 Bot 的本人会话接口统一携带 f_user_id', async () => {
    const sessionDto = {
      session_id: 's1',
      title: '标题',
      agent_id: 'main',
      model: 'openai/gpt-5.3',
      message_count: 1,
      gmt_create: '',
      gmt_modified: '',
    };
    const messageDto = { message_id: 'm1', session_id: 's1', role: 'user', content: 'q', gmt_create: '' };
    const friendBot = { ...bot, isFriendBot: true };
    const baseParams = { user_id: '900003', owner_id: '2088', f_user_id: '900003' };

    mocked.listBotSessions.mockResolvedValue({ data: { items: [], total: 0 } });
    mocked.createBotSession.mockResolvedValue({ data: sessionDto });
    mocked.getBotSession.mockResolvedValue({ data: sessionDto });
    mocked.deleteBotSession.mockResolvedValue({ data: { deleted: true } });
    mocked.updateBotSession.mockResolvedValue({ data: sessionDto });
    mocked.deleteBotSessionMessages.mockResolvedValue({ data: { deleted: true } });
    mocked.listBotSessionMessages.mockResolvedValue({ data: { items: [messageDto], total: 1 } });
    mocked.favoriteBotSession.mockResolvedValue({ data: { session_id: 's1', favorited: true } });
    mocked.unfavoriteBotSession.mockResolvedValue({ data: { session_id: 's1', favorited: false } });
    mocked.listFavoriteSessions.mockResolvedValue({ data: { items: [], total: 0 } });
    mocked.listBotModels.mockResolvedValue({ data: { items: [], total: 0 } });

    await botSessionService.listSessionsPage(friendBot, 'human_900003', 1, 10);
    expect(mocked.listBotSessions).toHaveBeenLastCalledWith('20260402_ab', {
      ...baseParams,
      page: 1,
      page_size: 10,
    });

    await botSessionService.createSession(friendBot, 'human_900003');
    expect(mocked.createBotSession).toHaveBeenLastCalledWith('20260402_ab', baseParams, { title: '新会话' });

    await botSessionService.getSessionDetail(friendBot, 'human_900003', 's1');
    expect(mocked.getBotSession).toHaveBeenLastCalledWith('20260402_ab', 's1', baseParams);

    await botSessionService.deleteSession(friendBot, 'human_900003', 's1');
    expect(mocked.deleteBotSession).toHaveBeenLastCalledWith('20260402_ab', 's1', baseParams);

    await botSessionService.updateSessionTitle(friendBot, 'human_900003', 's1', '新标题');
    expect(mocked.updateBotSession).toHaveBeenLastCalledWith('20260402_ab', 's1', baseParams, { title: '新标题' });

    await botSessionService.clearContext(friendBot, 'human_900003', 's1');
    expect(mocked.deleteBotSessionMessages).toHaveBeenLastCalledWith('20260402_ab', 's1', baseParams);

    await botSessionService.listMessages(friendBot, 'human_900003', 's1');
    expect(mocked.listBotSessionMessages).toHaveBeenLastCalledWith('20260402_ab', 's1', {
      ...baseParams,
      page: 1,
      page_size: 50,
    });

    await botSessionService.toggleFavorite(friendBot, 'human_900003', 's1', true);
    expect(mocked.favoriteBotSession).toHaveBeenLastCalledWith('20260402_ab', 's1', baseParams);
    await botSessionService.toggleFavorite(friendBot, 'human_900003', 's1', false);
    expect(mocked.unfavoriteBotSession).toHaveBeenLastCalledWith('20260402_ab', 's1', baseParams);

    await botSessionService.listFavoriteSessionsPage(friendBot, 'human_900003', 1, 10);
    expect(mocked.listFavoriteSessions).toHaveBeenLastCalledWith('20260402_ab', {
      ...baseParams,
      page: 1,
      page_size: 10,
    });

    await botSessionService.listModels(friendBot, 'human_900003');
    expect(mocked.listBotModels).toHaveBeenLastCalledWith('20260402_ab', {
      ...baseParams,
      page: 1,
      page_size: 50,
    });

    await botSessionService.updateSessionModel(friendBot, 'human_900003', 's1', 'openai/gpt-5.3');
    expect(mocked.updateBotSession).toHaveBeenLastCalledWith('20260402_ab', 's1', baseParams, {
      model: 'openai/gpt-5.3',
    });

    mocked.listBotSessions.mockClear();
    await botSessionService.listSessionsPage(bot, 'human_900003', 1, 10);
    expect(mocked.listBotSessions).toHaveBeenLastCalledWith('20260402_ab', {
      user_id: '900003',
      owner_id: '2088',
      f_user_id: '900003',
      page: 1,
      page_size: 10,
    });
  });
});

it('listSessionsPage 与收藏分页复用统一标题映射并透传分页参数', async () => {
  const bot = {
    botId: '20260402_ab:2088',
    realBotId: '20260402_ab',
    ownerId: '2088',
    displayName: 'B',
    online: true,
    chatable: true,
  };
  mocked.listBotSessions.mockResolvedValue({
    code: 200000,
    data: {
      items: [
        {
          session_id: 'sid-1',
          title: '标题_sid-1',
          agent_id: '',
          model: '',
          message_count: 1,
          gmt_create: '',
          gmt_modified: '',
        },
      ],
      total: 12,
    },
  });
  const all = await botSessionService.listSessionsPage(bot, 'human_900003', 2, 10);
  expect(mocked.listBotSessions).toHaveBeenCalledWith('20260402_ab', {
    user_id: '900003',
    owner_id: '2088',
    f_user_id: '900003',
    page: 2,
    page_size: 10,
  });
  expect(all).toMatchObject({ ok: true, data: { total: 12, items: [{ title: '标题' }] } });

  mocked.listFavoriteSessions.mockResolvedValue({
    code: 200000,
    data: {
      items: [{ session_id: 'sid-f', title: '收藏标题_sid-f', message_count: 2, gmt_create: '', gmt_modified: '' }],
      total: 1,
    },
  });
  const favorites = await botSessionService.listFavoriteSessionsPage(bot, 'human_900003', 1, 10);
  expect(favorites).toMatchObject({
    ok: true,
    data: { total: 1, items: [{ sessionId: 'sid-f', title: '收藏标题', favorite: true }] },
  });
});

describe('owned directory engine context stays deferred', () => {
  it.each(['personal', 'service'])('does not scan inventory for %s directory rows', async (bot_type) => {
    ownedMocked.listBots.mockResolvedValue({ data: { items: [{ bot_id: 'deferred', bot_name: 'Bot', owner_entity_id: 'owner', bot_type, status: 'ACTIVE', engine: 'openclaw' }] } });
    ownedMocked.listBotInventory.mockClear();
    const result = await botSessionService.listOwnedBots('caller');
    expect(result).toMatchObject({ ok: true, data: [{ actions: [], chatable: true, runtimeStage: bot_type === 'service' ? 'online' : 'draft' }] });
    expect(ownedMocked.listBotInventory).not.toHaveBeenCalled();
  });
});

it('managed handoff Bot separates BCN address, resource entity and personnel Owner', async () => {
  ownedMocked.listBots.mockResolvedValue({ data: { items: [{
    bot_id: 'service', bot_name: 'Transferred', bot_type: 'service',
    bcn_bot_id: 'service:original', entity_id: 'staff_original',
    owner_entity_id: 'new-owner', status: 'ACTIVE', engine: 'openclaw',
  }], total: 1 } });
  const result = await botSessionService.listOwnedBots('new-owner');
  expect(result).toMatchObject({ ok: true, data: [{
    botId: 'service:original', realBotId: 'service', ownerId: 'staff_original',
    engineOwnerId: 'new-owner',
  }] });
});

describe('instance session lookup error notifications', () => {
  const bot = { botId: 'bot:1', realBotId: 'bot', ownerId: '1', displayName: 'Bot', online: true, chatable: true };
  it.each([404, 401, 403, 500])('only cancels expected probe misses: HTTP %s', async (status) => {
    useErrorNotifyStore.getState().reset();
    useErrorNotifyStore.getState().enqueue({ toastKey: 'probe', message: 'failure' });
    mocked.getBotSession.mockRejectedValueOnce(new BackendRequestError('failure', {
      status, apiPath: '/sessions/s', toastKey: 'probe',
    }));
    const result = await botSessionService.getSessionDetail(bot, '1', 's',
      { deviceUuid: 'device-a', stage: 'online' }, { silentNotFound: true });
    expect(result.ok).toBe(false);
    expect(useErrorNotifyStore.getState().queue[0].cancelled === true).toBe(status === 404);
    expect(result).toMatchObject({ error: { code: status === 404 ? 'NOT_FOUND' : 'BOT_SESSION_FAILED' } });
  });
  it('cancels the notification produced by the real HTTP request chain', async () => {
    const originalFetch = global.fetch;
    const actual = jest.requireActual<typeof ctrl>('@/services/backendApi/bots/privateBotSessionController');
    useErrorNotifyStore.getState().reset();
    mocked.getBotSession.mockImplementationOnce(actual.getBotSession);
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue({
      ok: false, status: 404,
      headers: { get: () => 'application/json' },
      json: async () => ({ code: 404000, message: 'Not Found' }),
    } as unknown as Response);
    try {
      const result = await botSessionService.getSessionDetail(bot, '1', 's',
        { deviceUuid: 'device-a', stage: 'online' }, { silentNotFound: true });
      expect(result).toMatchObject({ ok: false, error: { code: 'NOT_FOUND' } });
      expect(global.fetch).toHaveBeenCalledTimes(1);
      expect(useErrorNotifyStore.getState().queue).toHaveLength(1);
      expect(useErrorNotifyStore.getState().queue[0].cancelled).toBe(true);
    } finally {
      global.fetch = originalFetch;
    }
  });
  it('keeps ordinary session detail 404 notifications', async () => {
    useErrorNotifyStore.getState().reset();
    useErrorNotifyStore.getState().enqueue({ toastKey: 'detail', message: 'not found' });
    mocked.getBotSession.mockRejectedValueOnce(new BackendRequestError('not found', {
      status: 404, apiPath: '/sessions/s', toastKey: 'detail',
    }));
    await botSessionService.getSessionDetail(bot, '1', 's');
    expect(useErrorNotifyStore.getState().queue[0].cancelled).not.toBe(true);
  });
});

// OpenSpec fix-session-title-auto-rename §session-title-normalization：
// adapter 写引擎 label 有两种后缀形态（create `_<裸uuid>` / update `_<完整id>`），
// mapBotSessionSummary 的剥除判据必须锚定本会话自身标识，宁漏剥不误剥。
describe('mapBotSessionSummary 标题后缀剥除（锚定本会话）', () => {
  const uuid = '9a4cadce-44a7-41de-8826-749b3b0b79f7';
  const base = {
    title: '',
    message_count: 0,
    gmt_modified: '',
    gmt_create: '',
  };

  it('update 形态 `_<完整 session_id>`：照旧剥净', () => {
    const sessionId = `agent:main:session:${uuid}:user:900003`;
    expect(
      mapBotSessionSummary({ ...base, session_id: sessionId, title: `标题A_${sessionId}` }).title,
    ).toBe('标题A');
  });

  it('create 形态 `_<裸uuid>`：锚定本会话 id 补齐剥净（完整 AID 与裸 key 两种 id 形态）', () => {
    expect(
      mapBotSessionSummary({
        ...base,
        session_id: `agent:main:session:${uuid}:user:900003`,
        title: `新会话_${uuid}`,
      }).title,
    ).toBe('新会话');
    expect(
      mapBotSessionSummary({
        ...base,
        session_id: `session:${uuid}:user:900003`,
        title: `新对话_${uuid}`,
      }).title,
    ).toBe('新对话');
  });

  it('人类标题自带下划线尾巴不误剥（teclaw 回归锚点）', () => {
    expect(
      mapBotSessionSummary({
        ...base,
        session_id: `agent:main:session:${uuid}:user:900003`,
        title: '技能回放-ODPS_APPROVAL-100008',
      }).title,
    ).toBe('技能回放-ODPS_APPROVAL-100008');
  });

  it('尾段 uuid 非本会话自己：宁漏剥，原样保留', () => {
    expect(
      mapBotSessionSummary({
        ...base,
        session_id: 'solo-id',
        title: `报告_77777777-7777-4777-8777-777777777777`,
      }).title,
    ).toBe(`报告_77777777-7777-4777-8777-777777777777`);
  });

  it('幂等：干净标题重复映射为 no-op；空 title 仍回退「新会话」', () => {
    const sessionId = `agent:main:session:${uuid}:user:900003`;
    expect(mapBotSessionSummary({ ...base, session_id: sessionId, title: '新会话' }).title).toBe('新会话');
    expect(mapBotSessionSummary({ ...base, session_id: sessionId, title: '' }).title).toBe('新会话');
  });
});

describe('extractSessionUuidFromSessionId', () => {
  const uuid = '9a4cadce-44a7-41de-8826-749b3b0b79f7';

  it('裸 key `session:<uuid>:user:<id>` 提取 uuid', () => {
    expect(extractSessionUuidFromSessionId(`session:${uuid}:user:1`)).toBe(uuid);
  });

  it('完整 AID `agent:<provider>:session:<uuid>:user:<id>` 提取 uuid', () => {
    expect(extractSessionUuidFromSessionId(`agent:main:session:${uuid}:user:1`)).toBe(uuid);
  });

  it('未知形态返回 null（宁漏剥不误剥）', () => {
    expect(extractSessionUuidFromSessionId('solo-id')).toBeNull();
    expect(extractSessionUuidFromSessionId('')).toBeNull();
    expect(extractSessionUuidFromSessionId(undefined)).toBeNull();
  });
});

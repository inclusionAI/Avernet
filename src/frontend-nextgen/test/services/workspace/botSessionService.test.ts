/** @jest-environment jsdom */
import type { IdentityView } from '@/domain/collaboration';
import * as botCtrl from '@/services/backendApi/bots/botController';
import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { botSessionService, listChatBots, splitBotId } from '@/services/workspace/botSessionService';
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

  it('listOwnedBots 展示模板名称并兼容历史个人/应用 Coding Bot', async () => {
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
            engine_type: 'claude_code',
            template_type: 'personalCoding',
            owner_entity_id: '2',
            status: 'ACTIVE',
          },
          {
            bot_id: 'architect:3',
            bot_name: '架构实例',
            engine_type: 'claude_code',
            engine_properties: { template_type: 'architect', template_config: { template_name: '架构 Bot' } },
            owner_entity_id: '3',
            status: 'ACTIVE',
          },
        ],
      },
    });

    const res = await botSessionService.listOwnedBots('human_327325');
    expect(ownedMocked.listBots).toHaveBeenCalledWith({ user_id: '327325', page: 1, page_size: 100 });
    expect(res.ok && res.data).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ displayName: '应用实例', isAgentCodingBot: true, templateName: '应用 Bot' }),
        expect.objectContaining({ displayName: '个人实例', isAgentCodingBot: true, templateName: '个人 Coding Bot' }),
        expect.objectContaining({ displayName: '架构实例', isAgentCodingBot: true, templateName: '架构 Bot' }),
      ]),
    );
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
    const res = await botSessionService.listSessions(bot, 'human_327325');
    expect(mocked.listBotSessions).toHaveBeenCalledWith('20260402_ab', {
      user_id: '327325',
      owner_id: '2088',
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
    const res = await botSessionService.createSession(bot, 'human_327325', '新');
    expect(mocked.createBotSession).toHaveBeenCalledWith(
      '20260402_ab',
      { user_id: '327325', owner_id: '2088' },
      { title: '新' },
    );
    expect((res as any).data.sessionId).toBe('sid-new');
  });

  it('deleteSession 调用 controller', async () => {
    mocked.deleteBotSession.mockResolvedValue({
      code: 200000,
      data: { deleted: true },
      message: 'OK',
      request_id: 'r',
    });
    const res = await botSessionService.deleteSession(bot, 'human_327325', 'sid-9');
    expect(mocked.deleteBotSession).toHaveBeenCalledWith('20260402_ab', 'sid-9', {
      user_id: '327325',
      owner_id: '2088',
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
    await botSessionService.listMessages(bot, 'human_327325', 's');
    expect(mocked.listBotSessionMessages).toHaveBeenCalledWith('20260402_ab', 's', {
      user_id: '327325',
      owner_id: '2088',
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
    const res = await botSessionService.listModels(bot, 'human_327325');
    expect(mocked.listBotModels).toHaveBeenCalledWith('20260402_ab', {
      user_id: '327325',
      owner_id: '2088',
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
    const res = await botSessionService.updateSessionModel(bot, 'human_327325', 's1', 'openai/gpt-5.3');
    expect(mocked.updateBotSession).toHaveBeenCalledWith(
      '20260402_ab',
      's1',
      { user_id: '327325' },
      { model: 'openai/gpt-5.3' },
    );
    expect(res.ok && res.data.model).toBe('openai/gpt-5.3');
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
  const all = await botSessionService.listSessionsPage(bot, 'human_327325', 2, 10);
  expect(mocked.listBotSessions).toHaveBeenCalledWith('20260402_ab', {
    user_id: '327325',
    owner_id: '2088',
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
  const favorites = await botSessionService.listFavoriteSessionsPage(bot, 'human_327325', 1, 10);
  expect(favorites).toMatchObject({
    ok: true,
    data: { total: 1, items: [{ sessionId: 'sid-f', title: '收藏标题', favorite: true }] },
  });
});

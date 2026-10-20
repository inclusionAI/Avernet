import { listBotInventory } from '@/services/backendApi/bots/botController';
import { listCollaboratingBots, type CollaboratingBotDto } from '@/services/backendApi/bots/collaboratingBotController';
import { teamBotConversationService } from '@/services/workspace/teamBotConversationService';
jest.mock('@/services/backendApi/bots/collaboratingBotController', () => ({ listCollaboratingBots: jest.fn() }));
jest.mock('@/services/backendApi/bots/botController', () => ({ listBotInventory: jest.fn() }));
const inventory = jest.mocked(listBotInventory);
const list = jest.mocked(listCollaboratingBots);
const dto = (overrides: Partial<CollaboratingBotDto> = {}): CollaboratingBotDto => ({
  bot_id: 'shared',
  bot_name: '团队助手',
  bot_desc: '',
  entity_id: 'team-entity',
  owner_id: 'different-owner',
  engine: 'teclaw',
  cluster_name: 'ANDC',
  bot_type: 'personal',
  status: 'ACTIVE',
  space: { space_id: '42', name: 'Team', kind: 'team' },
  collaboration: { id: 1, role: 'member', joined_at: '2026-09-01T10:30:00' },
  ...overrides,
});
beforeEach(() => {
  list.mockReset();
  inventory.mockReset();
  inventory.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
});

test('entity_id 而不是 owner_id 作为会话 owner 上下文，mine 不标记好友', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [dto()], total: 1 } });
  const result = await teamBotConversationService.listBots('human_viewer');
  expect(list).toHaveBeenCalledWith({ user_id: 'viewer', page: 1, page_size: 100 }, undefined);
  expect(result).toMatchObject({
    ok: true,
    data: [
      {
        botId: 'shared:team-entity',
        realBotId: 'shared',
        ownerId: 'team-entity',
        displayName: '团队助手',
        engine: 'teclaw',
        chatable: true,
      },
    ],
  });
  expect(result.ok && result.data[0].isFriendBot).not.toBe(true);
});

test('团队 Coding Bot 保留模板分类字段，避免行标签退化为 AgentCoding', async () => {
  list.mockResolvedValue({
    code: 200000,
    data: {
      items: [
        dto({
          bot_name: '团队 Coding Bot',
          engine: 'claude_code',
          template_type: 'generalCC',
          template_name: '通用 Coding Bot',
        }),
      ],
      total: 1,
    },
  });

  const result = await teamBotConversationService.listBots('human_viewer');

  expect(result).toMatchObject({
    ok: true,
    data: [
      {
        botId: 'shared:team-entity',
        displayName: '团队 Coding Bot',
        isTeamBot: true,
        isAgentCodingBot: true,
        templateType: 'generalCC',
        templateName: '通用 Coding Bot',
      },
    ],
  });
});

test('从所属团队空间的权威目录读取同 Bot 和实体 owner 的权限', async () => {
  list.mockResolvedValue({
    code: 200000,
    data: { items: [dto({ engine: 'openclaw', space: { space_id: '42', name: 'Team', kind: 'team' } })], total: 1 },
  });
  inventory.mockResolvedValue({
    code: 200000,
    data: {
      items: [
        {
          bot_id: 'shared',
          owner_entity_id: 'team-entity',
          actions: ['engine_restart'],
          disabled_actions: { restart_publish: '无权发布' },
        },
      ],
      total: 1,
    },
  });
  const result = await teamBotConversationService.listBots('human_viewer');
  expect(inventory).toHaveBeenCalledWith({ user_id: 'viewer', page: 1, page_size: 100 }, '42', undefined);
  expect(result.ok && result.data[0]).toMatchObject({
    actions: ['engine_restart'],
    disabledActions: { restart_publish: '无权发布' },
    runtimeStage: 'draft',
    ownerId: 'team-entity',
  });
});

test.each([
  [
    '错误 owner',
    {
      code: 200000,
      data: {
        items: [{ bot_id: 'shared', owner_entity_id: 'different-owner', actions: ['engine_restart'] }],
        total: 1,
      },
    },
  ],
  [
    '业务失败携带权限',
    {
      code: 500000,
      data: { items: [{ bot_id: 'shared', owner_entity_id: 'team-entity', actions: ['engine_restart'] }], total: 1 },
    },
  ],
])('权限来源%s时保留聊天且禁用更新', async (_name, response) => {
  list.mockResolvedValue({ code: 200000, data: { items: [dto()], total: 1 } });
  inventory.mockResolvedValue(response);
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({
    ok: true,
    data: [{ actions: [], chatable: true }],
  });
});

test('权限请求失败不影响团队聊天', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [dto()], total: 1 } });
  inventory.mockRejectedValue(new Error('unavailable'));
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({
    ok: true,
    data: [{ actions: [], chatable: true }],
  });
});

test('团队 Coding Bot 从 template_config 读取模板名，与管理 Bot 目录一致', async () => {
  list.mockResolvedValue({
    code: 200000,
    data: {
      items: [
        dto({
          bot_id: 'architect-coding',
          bot_name: '团队架构 Bot',
          entity_id: 'entity-architect',
          engine: 'claude_code',
          template_type: 'generalCC',
          template_name: undefined,
          template_config: {
            template_type: 'generalCC',
            template_name: '架构 Bot',
            bot_template_config: { template_name: '架构 Bot' },
          },
        }),
        dto({
          bot_id: 'normal-cc',
          bot_name: '团队普通 CC',
          entity_id: 'entity-normal',
          engine: 'claude_code',
          template_type: undefined,
          template_name: undefined,
          template_config: {
            template_type: 'normalCC',
            template_name: undefined,
            bot_template_config: {},
          },
        }),
      ],
      total: 2,
    },
  });

  const result = await teamBotConversationService.listBots('human_viewer');

  expect(result).toMatchObject({
    ok: true,
    data: [
      {
        botId: 'architect-coding:entity-architect',
        isAgentCodingBot: true,
        templateType: 'generalCC',
        templateName: '架构 Bot',
      },
      {
        botId: 'normal-cc:entity-normal',
        isAgentCodingBot: false,
        templateType: 'normalCC',
        templateName: undefined,
      },
    ],
  });
});

test('团队历史 Coding Bot 缺失 template_name 时按模板分类生成可读标签', async () => {
  list.mockResolvedValue({
    code: 200000,
    data: {
      items: [
        dto({
          bot_id: 'application-coding',
          bot_name: '团队应用 Coding Bot',
          entity_id: 'entity-application',
          engine: 'claude_code',
          template_type: 'applicationCoding',
          template_name: undefined,
        }),
        dto({
          bot_id: 'personal-coding',
          bot_name: '团队个人 Coding Bot',
          entity_id: 'entity-personal',
          engine: 'aicoding',
          template_type: 'personalCoding',
          template_name: undefined,
        }),
      ],
      total: 2,
    },
  });

  const result = await teamBotConversationService.listBots('human_viewer');

  expect(result).toMatchObject({
    ok: true,
    data: [
      {
        botId: 'application-coding:entity-application',
        isAgentCodingBot: true,
        templateType: 'applicationCoding',
        templateName: '应用 Bot',
      },
      {
        botId: 'personal-coding:entity-personal',
        isAgentCodingBot: true,
        templateType: 'personalCoding',
        templateName: '个人 Coding Bot',
      },
    ],
  });
});

test('按 total 分页且按完整 bot_id/entity_id 去重，保留后端顺序', async () => {
  list.mockResolvedValueOnce({ code: 200000, data: { items: [dto(), dto({ entity_id: 'other-team' })], total: 101 } });
  list.mockResolvedValueOnce({ code: 200000, data: { items: [dto(), dto({ bot_id: 'last' })], total: 101 } });
  const signal = new AbortController().signal;
  const result = await teamBotConversationService.listBots('viewer', signal);
  expect(list).toHaveBeenNthCalledWith(2, { user_id: 'viewer', page: 2, page_size: 100 }, signal);
  expect(result.ok && result.data.map((b) => b.botId)).toEqual([
    'shared:team-entity',
    'shared:other-team',
    'last:team-entity',
  ]);
});

test('团队 Bot 直接消费接口返回的 Coding 模板', async () => {
  list.mockResolvedValue({
    code: 200000,
    data: {
      items: [dto({ engine: 'claude_code', template_type: 'applicationCoding', template_name: '应用 Bot' })],
      total: 1,
    },
  });

  const result = await teamBotConversationService.listBots('viewer');

  expect(result.ok && result.data[0]).toMatchObject({
    isAgentCodingBot: true,
  });
});

test('历史 aicoding 团队 Bot 缺 template_type 时归一为个人 Coding Bot', async () => {
  list.mockResolvedValue({
    code: 200000,
    data: {
      items: [
        dto({
          bot_name: '历史 AIcoding Bot',
          engine: 'aicoding',
          template_type: undefined,
          template_name: undefined,
        }),
      ],
      total: 1,
    },
  });

  const result = await teamBotConversationService.listBots('human_viewer');

  expect(result).toMatchObject({
    ok: true,
    data: [
      {
        isAgentCodingBot: true,
        templateType: 'personalCoding',
        templateName: '个人 Coding Bot',
      },
    ],
  });
});

test('缺 entity_id 不猜 owner 或 viewer，保留不可聊天行', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [dto({ entity_id: '' })], total: 1 } });
  const result = await teamBotConversationService.listBots('viewer');
  expect(result).toMatchObject({ ok: true, data: [{ chatable: false }] });
  expect(result.ok ? result.data[0].ownerId : null).toBeUndefined();
});

test.each([20000, 403000, undefined])('不把错误/其他方言 code %p 当成空列表', async (code) => {
  list.mockResolvedValue({ code, data: { items: [], total: 0 } });
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({ ok: false });
});
test('空列表成功、网络失败可重试、未登录不请求', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
  expect(await teamBotConversationService.listBots('viewer')).toEqual({ ok: true, data: [] });
  list.mockRejectedValue(new Error('offline'));
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({ ok: false, error: { canRetry: true } });
  list.mockClear();
  expect(await teamBotConversationService.listBots('')).toMatchObject({ ok: false });
  expect(list).not.toHaveBeenCalled();
});

test('服务 Bot 只从草稿卡读取权限，发布卡不能覆盖', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [dto({ bot_type: 'service' })], total: 1 } });
  inventory.mockResolvedValue({
    code: 200000,
    data: {
      items: [
        {
          bot_id: 'shared',
          owner_entity_id: 'team-entity',
          display_state: 'service_draft',
          actions: ['engine_restart'],
        },
        { bot_id: 'shared', owner_entity_id: 'team-entity', display_state: 'service_online', actions: [] },
      ],
      total: 2,
    },
  });
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({
    ok: true,
    data: [{ actions: ['engine_restart'] }],
  });
});

test('跨空间的同名 Bot 不能借用权限', async () => {
  list.mockResolvedValue({
    code: 200000,
    data: {
      items: [
        dto({ space: { space_id: '42', name: 'A', kind: 'team' } }),
        dto({ bot_id: 'other', space: { space_id: '43', name: 'B', kind: 'team' } }),
      ],
      total: 2,
    },
  });
  inventory.mockImplementation(async (_params, spaceId) => ({
    code: 200000,
    data: {
      items:
        spaceId === '43' ? [{ bot_id: 'shared', owner_entity_id: 'team-entity', actions: ['engine_restart'] }] : [],
      total: spaceId === '43' ? 1 : 0,
    },
  }));
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({
    ok: true,
    data: [{ actions: [] }, { actions: [] }],
  });
});

test('未知空间不回退到个人空间权限', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [dto({ space: undefined })], total: 1 } });
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({ ok: true, data: [{ actions: [] }] });
  expect(inventory).not.toHaveBeenCalled();
});

test('传递取消信号并在目标全部匹配后停止分页', async () => {
  const signal = new AbortController().signal;
  list.mockResolvedValue({ code: 200000, data: { items: [dto()], total: 1 } });
  inventory.mockResolvedValue({
    code: 200000,
    data: { items: [{ bot_id: 'shared', owner_entity_id: 'team-entity', actions: ['engine_restart'] }], total: 200 },
  });
  await teamBotConversationService.listBots('viewer', signal);
  expect(inventory).toHaveBeenCalledTimes(1);
  expect(inventory).toHaveBeenCalledWith({ user_id: 'viewer', page: 1, page_size: 100 }, '42', signal);
});

test('缺少实体 owner 的不可聊天记录不触发权限扫描', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [dto({ entity_id: '' })], total: 1 } });
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({
    ok: true,
    data: [{ chatable: false, actions: [] }],
  });
  expect(inventory).not.toHaveBeenCalled();
});

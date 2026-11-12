import type { BotDomain } from '@/services/botWorkshop';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import {
  buildDraftOrPrestableServiceCodingBotChatHref,
  canEnterBotDetail,
  canOpenDraftOrPrestableServiceCodingBot,
  canOpenServiceBotEvalHistory,
  getBotActionAvailability,
  getBotCollaborationMode,
  getBotEntryAvailability,
  getGeneralConfigAvailability,
  getInventoryActionAvailability,
  isDraftOrPrestableServiceCodingBot,
} from '@/services/botWorkshop/botPolicy';

describe('botPolicy', () => {
  test('预发服务 Bot 的运维人员可以进入只读验证；普通查看者不获得编辑入口', () => {
    const bot = mapBotDto({
      bot_id: 'verify-bot', bot_type: 'service', kind: 'service', engine: 'openclaw',
      status: 'ACTIVE', publish: { id: 1, status: 'VALIDATING', version: 1 },
      actions: ['view', 'publish_online', 'restart_publish', 'cancel_staging'],
    }).item;
    expect(getBotEntryAvailability(bot).enabled).toBe(true);
    expect(getInventoryActionAvailability(bot, 'edit')).toMatchObject({ visible: true, enabled: true });
    expect(canEnterBotDetail(bot, 'collaborator')).toBe(true);
    const viewer = { ...bot, actions: ['view'] };
    expect(getInventoryActionAvailability(viewer, 'edit').enabled).toBe(false);
    expect(canEnterBotDetail(viewer, 'visitor')).toBe(false);
    expect(getInventoryActionAvailability(bot, 'chat').enabled).toBe(false);
  });
  test('运行态服务 Bot 的明确协作者可进入只读配置页，但不获得编辑入口', () => {
    const bot = mapBotDto({
      bot_id: 'online-collaborated-bot',
      owner_entity_id: 'owner',
      bot_type: 'service',
      kind: 'service',
      engine: 'openclaw',
      status: 'ACTIVE',
      publish: { id: 1, status: 'SUCCESS', version: 1 },
      actions: ['view', 'view_config', 'chat'],
    }).item;

    expect(canEnterBotDetail(bot, 'collaborator')).toBe(true);
    expect(getInventoryActionAvailability(bot, 'edit')).toMatchObject({ visible: false, enabled: false });
  });
  test('部署中禁止进入、编辑和对话，但保留日志排障', () => {
    const bot = mapBotDto({ bot_id: 'b-1', status: 'PENDING', bot_type: 'service' }).item;
    const actions = getBotActionAvailability(bot, { canEdit: true });

    expect(actions.find((action) => action.action === 'view')?.enabled).toBe(false);
    expect(actions.find((action) => action.action === 'chat')?.enabled).toBe(false);
    expect(actions.find((action) => action.action === 'edit')?.enabled).toBe(false);
    expect(actions.find((action) => action.action === 'edit')?.disabledReason).toContain('部署中');
    expect(actions.find((action) => action.action === 'logs')).toMatchObject({ visible: true, enabled: true });
  });

  test.each([
    ['初始化中的服务 Bot', { status: 'PROVISIONING', publish: null }, '部署中'],
    ['发布失败的服务 Bot', { status: 'ACTIVE', publish: { id: 1, status: 'FAILED', version: 1 } }, '状态异常'],
  ])('%s 禁止从列表进入编辑页', (_label, fields, reason) => {
    const bot = mapBotDto({
      bot_id: 'service-lifecycle-entry',
      bot_type: 'service',
      kind: 'service',
      engine: 'openclaw',
      actions: ['view', 'edit', 'chat'],
      ...fields,
    }).item;

    expect(getBotEntryAvailability(bot)).toMatchObject({ enabled: false, disabledReason: expect.stringContaining(reason) });
    expect(getInventoryActionAvailability(bot, 'edit')).toMatchObject({ visible: true, enabled: false });
  });

  test.each(['deploying', 'prestable', 'offline', 'failed', 'unknown'] as const)(
    '%s 状态禁止从列表进入 Bot 内部',
    (lifecycle) => {
      const base = mapBotDto({
        bot_id: `b-${lifecycle}`,
        status: 'ACTIVE',
        actions: ['view', 'edit', 'chat'],
      }).item;
      const bot = { ...base, lifecycle };

      expect(getBotEntryAvailability(bot).enabled).toBe(false);
      expect(getInventoryActionAvailability(bot, 'view')).toMatchObject({ visible: true, enabled: false });
      expect(getInventoryActionAvailability(bot, 'edit')).toMatchObject({ visible: true, enabled: false });
      expect(getInventoryActionAvailability(bot, 'chat')).toMatchObject({ visible: true, enabled: false });
    },
  );

  test.each(['draft', 'running'] as const)('%s 状态允许进入 Bot 内部', (lifecycle) => {
    const base = mapBotDto({ bot_id: `b-${lifecycle}`, status: 'ACTIVE' }).item;
    expect(getBotEntryAvailability({ ...base, lifecycle }).enabled).toBe(true);
  });

  test('编辑锁不影响日志，离线 Bot 不展示日志', () => {
    const locked = mapBotDto({
      bot_id: 'b-1',
      active_engine: 'openclaw',
      status: 'ACTIVE',
      lock: { status: 'owned-by-other' },
    }).item;
    expect(getBotActionAvailability(locked).find((action) => action.action === 'logs')).toMatchObject({
      visible: true,
      enabled: true,
    });

    const offline = mapBotDto({ bot_id: 'b-2', active_engine: 'openclaw', status: 'OFFLINE' }).item;
    expect(getBotActionAvailability(offline).find((action) => action.action === 'logs')).toMatchObject({
      visible: false,
      enabled: false,
    });
  });

  test('未知引擎不开放写操作', () => {
    const bot = mapBotDto({ bot_id: 'b-1', active_engine: 'not-known' }).item;
    const actions = getBotActionAvailability(bot, { canEdit: true });

    expect(actions.find((action) => action.action === 'edit')?.enabled).toBe(false);
    expect(actions.find((action) => action.action === 'edit')?.disabledReason).toContain('引擎未识别');
  });
});
  test('历史评测记录入口仅对 Owner 或具有服务 Bot 编辑权限的用户开放', () => {
    const serviceBot = mapBotDto({
      bot_id: 'service-history-bot',
      owner_entity_id: 'owner',
      bot_type: 'service',
      kind: 'service',
      engine: 'openclaw',
      publish: { id: 1, status: 'SUCCESS', version: 1 },
      actions: ['view', 'chat', 'restart_publish', 'offline'],
    }).item;

    expect(canOpenServiceBotEvalHistory(serviceBot, 'owner')).toBe(true);
    expect(canOpenServiceBotEvalHistory({ ...serviceBot, actions: ['view', 'edit'] }, 'editor')).toBe(true);
    expect(canOpenServiceBotEvalHistory({ ...serviceBot, actions: ['view', 'view_config'] }, 'editor')).toBe(true);
    expect(canOpenServiceBotEvalHistory(serviceBot, 'space-member')).toBe(false);
    expect(canOpenServiceBotEvalHistory(serviceBot)).toBe(false);
    expect(canOpenServiceBotEvalHistory({ ...serviceBot, serviceMode: 'non-service' }, 'owner')).toBe(false);
  });


describe('服务化 Coding Bot 的发布 ID 映射', () => {
  test('预发 Inventory 卡片的数字 publication_id 映射为 botPublishId', () => {
    const bot = mapBotDto({
      bot_id: 'service-coding-bot',
      bot_name: '服务化 Coding Bot',
      engine: 'claude_code',
      template_type: 'generalCC',
      bot_type: 'service',
      kind: 'service',
      owner_entity_id: 'owner-1',
      status: 'ACTIVE',
      display_state: 'service_prestable',
      internal_status: 'VALIDATING',
      publication_id: 42,
      publication_version: 3,
    }).item;

    expect(bot.lifecycle).toBe('prestable');
    expect(bot.harnessContext?.botPublishId).toBe('42');
    expect(
      buildDraftOrPrestableServiceCodingBotChatHref({
        ...bot,
        runtime: { ...bot.runtime, isAgentCodingBot: true },
      }),
    ).toBe(`/coding/coding-chat?botId=service-coding-bot&ownerId=owner-1&publishId=42&stage=verify`);
  });

  test('嵌套 publish.id 为数字时同样映射为 botPublishId', () => {
    const bot = mapBotDto({
      bot_id: 'legacy-service-coding-bot',
      bot_name: '历史服务化 Coding Bot',
      engine: 'claude_code',
      template_type: 'generalCC',
      bot_type: 'service',
      kind: 'service',
      owner_entity_id: 'owner-1',
      publish: { id: 43, status: 'VALIDATING', version: 2 },
    }).item;

    expect(bot.harnessContext?.botPublishId).toBe('43');
  });
});

describe('服务化 Coding Bot 的草稿/预发独立入口', () => {
  const base = {
    id: 'coding-service-bot',
    ownerId: 'owner-1',
    serviceMode: 'service',
    lifecycle: 'draft' as BotDomain['lifecycle'],
    runtime: { isAgentCodingBot: true },
    actions: ['view'],
  } as unknown as BotDomain;

  test('仅服务化 Coding Bot 的草稿/预发状态命中独立入口', () => {
    expect(isDraftOrPrestableServiceCodingBot(base)).toBe(true);
    expect(isDraftOrPrestableServiceCodingBot({ ...base, lifecycle: 'prestable' })).toBe(true);
    expect(isDraftOrPrestableServiceCodingBot({ ...base, lifecycle: 'running' })).toBe(false);
    expect(isDraftOrPrestableServiceCodingBot({ ...base, serviceMode: 'non-service' })).toBe(false);
    expect(
      isDraftOrPrestableServiceCodingBot({
        ...base,
        runtime: { ...base.runtime, isAgentCodingBot: false },
      }),
    ).toBe(false);
  });

  test('独立入口只对 Owner、协作编辑者或预发验证者开放', () => {
    expect(canOpenDraftOrPrestableServiceCodingBot(base, 'owner-1')).toBe(true);
    expect(canOpenDraftOrPrestableServiceCodingBot({ ...base, actions: ['view', 'edit'] }, 'u-visitor')).toBe(true);
    expect(canOpenDraftOrPrestableServiceCodingBot(base, 'u-visitor')).toBe(false);

    const verifyOperator = {
      ...base,
      lifecycle: 'prestable' as const,
      actions: ['view', 'publish_online'],
    };
    expect(canOpenDraftOrPrestableServiceCodingBot(verifyOperator, 'u-visitor')).toBe(true);
  });

  test('草稿跳转携带 Bot 与 Owner，不选择发布版本', () => {
    expect(
      buildDraftOrPrestableServiceCodingBotChatHref({
        ...base,
        id: 'coding bot',
        harnessContext: { entityType: 'staff', entityId: 'e1', botPublishId: '9' },
      }),
    ).toBe('/coding/coding-chat?botId=coding+bot&ownerId=owner-1');
  });

  test('预发跳转映射为 verify，并携带有效发布 ID', () => {
    expect(
      buildDraftOrPrestableServiceCodingBotChatHref({
        ...base,
        lifecycle: 'prestable',
        harnessContext: { entityType: 'staff', entityId: 'e1', botPublishId: '42' },
      }),
    ).toBe('/coding/coding-chat?botId=coding-service-bot&ownerId=owner-1&publishId=42&stage=verify');
  });

  test('预发缺少有效发布 ID 时保持直连参数，不伪造 verify 选择', () => {
    const href = buildDraftOrPrestableServiceCodingBotChatHref({
      ...base,
      lifecycle: 'prestable',
      harnessContext: { entityType: 'staff', entityId: 'e1', botPublishId: 'not-a-number' },
    });
    expect(href).toBe('/coding/coding-chat?botId=coding-service-bot&ownerId=owner-1');
  });
});

describe('Bot 授权入口门禁', () => {
  test('非 AI Coding 的个人 Bot 在任何空间都不展示授权或申请入口', () => {
    const teamBot = mapBotDto({
      bot_id: 'personal-openclaw-team-bot',
      bot_type: 'personal',
      engine: 'openclaw',
      space: { space_id: '12', kind: 'team' },
      actions: ['view'],
    }).item;
    const personalSpaceBot = { ...teamBot, spaceKind: 'personal' as const };

    expect(getBotCollaborationMode(teamBot, true)).toBeUndefined();
    expect(getBotCollaborationMode(teamBot, false)).toBeUndefined();
    expect(getBotCollaborationMode(personalSpaceBot, true)).toBeUndefined();
    expect(getBotCollaborationMode(personalSpaceBot, false)).toBeUndefined();
  });

  test('AI Coding 个人 Bot 与非个人 Bot 保留团队空间协作入口', () => {
    const codingPersonalBot = mapBotDto({
      bot_id: 'personal-coding-team-bot',
      bot_type: 'personal',
      active_engine: 'claude_code',
      template_type: 'generalCC',
      space: { space_id: '12', kind: 'team' },
      actions: ['view'],
    }).item;
    const serviceBot = mapBotDto({
      bot_id: 'service-openclaw-team-bot',
      bot_type: 'service',
      kind: 'service',
      engine: 'openclaw',
      space: { space_id: '12', kind: 'team' },
      actions: ['view'],
    }).item;

    expect(getBotCollaborationMode(codingPersonalBot, true)).toBe('authorize');
    expect(getBotCollaborationMode(codingPersonalBot, false)).toBe('request');
    expect(getBotCollaborationMode(serviceBot, true)).toBe('authorize');
    expect(getBotCollaborationMode(serviceBot, false)).toBe('request');
  });
});

describe('公开设置菜单项门禁（collab-permission-entry-migration AC-02/AC-03）', () => {
  // 个人 Bot：不受服务 Bot 发布态约束，用以验证 Owner / 编辑锁门禁。
  const personalBot = mapBotDto({ bot_id: 'personal-bot', engine: 'openclaw', actions: ['view'] }).item;
  const publishedServiceBot = {
    ...mapBotDto({
      bot_id: 'service-bot',
      bot_type: 'service',
      kind: 'service',
      engine: 'openclaw',
      actions: ['view'],
    }).item,
    serviceMode: 'service' as const,
    lifecycle: 'running' as const,
  };

  test('Bot 管理员（Owner）且无锁时可打开', () => {
    expect(getGeneralConfigAvailability(personalBot, true, false)).toEqual({ enabled: true });
  });

  test('非 Bot Owner 禁用并说明原因', () => {
    expect(getGeneralConfigAvailability(personalBot, false, false)).toEqual({
      enabled: false,
      disabledReason: '仅 Bot Owner 可变更公开设置',
    });
  });

  test('他人持编辑锁时禁用并说明原因', () => {
    expect(getGeneralConfigAvailability(personalBot, true, true)).toEqual({
      enabled: false,
      disabledReason: '该 Bot 正被他人编辑，请先抢锁',
    });
  });

  test('非 Owner 与持锁同时存在时 Owner 原因优先', () => {
    expect(getGeneralConfigAvailability(personalBot, false, true).disabledReason).toBe('仅 Bot Owner 可变更公开设置');
  });

  test('已发布（运行中）的服务 Bot Owner 可打开', () => {
    expect(getGeneralConfigAvailability(publishedServiceBot, true, false)).toEqual({ enabled: true });
  });

  test.each(['draft', 'deploying', 'prestable', 'offline', 'failed', 'unknown'] as const)(
    '服务 Bot 未发布（lifecycle=%s）时 Owner 也禁用，并说明原因',
    (lifecycle) => {
      const bot = { ...publishedServiceBot, lifecycle };
      expect(getGeneralConfigAvailability(bot, true, false)).toEqual({
        enabled: false,
        disabledReason: '服务 Bot 未发布前暂不可变更公开设置',
      });
    },
  );

  test('未发布的个人 Bot 不受服务 Bot 发布态约束，仍可打开', () => {
    const draftPersonalBot = { ...personalBot, lifecycle: 'draft' as const };
    expect(getGeneralConfigAvailability(draftPersonalBot, true, false)).toEqual({ enabled: true });
  });

  test('未发布服务 Bot 的非 Owner 命中 Owner 原因优先', () => {
    const draftServiceBot = { ...publishedServiceBot, lifecycle: 'draft' as const };
    expect(getGeneralConfigAvailability(draftServiceBot, false, false).disabledReason).toBe('仅 Bot Owner 可变更公开设置');
  });
});

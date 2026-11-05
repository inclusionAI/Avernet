import type { BotDomain } from '@/services/botWorkshop';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import {
  buildDraftOrPrestableServiceCodingBotChatHref,
  canEnterBotDetail,
  canOpenDraftOrPrestableServiceCodingBot,
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

describe('Bot 授权入口跟随空间类型', () => {
  test('团队空间 Owner 可授权，个人空间即使 Bot 标记为团队归属也不展示', () => {
    const teamBot = mapBotDto({
      bot_id: 'team-bot',
      engine: 'openclaw',
      space: { space_id: '12', kind: 'team' },
      actions: ['view'],
    }).item;
    const personalBot = {
      ...teamBot,
      spaceKind: 'personal' as const,
      ownership: 'team' as const,
    };

    expect(getBotCollaborationMode(teamBot, true)).toBe('authorize');
    expect(getBotCollaborationMode(teamBot, false)).toBe('request');
    expect(getBotCollaborationMode(personalBot, true)).toBeUndefined();
    expect(getBotCollaborationMode(personalBot, false)).toBeUndefined();
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

  test('非 Bot 管理员禁用并说明原因', () => {
    expect(getGeneralConfigAvailability(personalBot, false, false)).toEqual({
      enabled: false,
      disabledReason: '仅 Bot 管理员可变更公开设置',
    });
  });

  test('他人持编辑锁时禁用并说明原因', () => {
    expect(getGeneralConfigAvailability(personalBot, true, true)).toEqual({
      enabled: false,
      disabledReason: '该 Bot 正被他人编辑，请先抢锁',
    });
  });

  test('非管理员与持锁同时存在时管理员原因优先', () => {
    expect(getGeneralConfigAvailability(personalBot, false, true).disabledReason).toBe('仅 Bot 管理员可变更公开设置');
  });

  test('已发布（运行中）的服务 Bot 管理员可打开', () => {
    expect(getGeneralConfigAvailability(publishedServiceBot, true, false)).toEqual({ enabled: true });
  });

  test.each(['draft', 'deploying', 'prestable', 'offline', 'failed', 'unknown'] as const)(
    '服务 Bot 未发布（lifecycle=%s）时管理员也禁用，并说明原因',
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

  test('未发布服务 Bot 的非管理员命中管理员原因优先', () => {
    const draftServiceBot = { ...publishedServiceBot, lifecycle: 'draft' as const };
    expect(getGeneralConfigAvailability(draftServiceBot, false, false).disabledReason).toBe('仅 Bot 管理员可变更公开设置');
  });
});

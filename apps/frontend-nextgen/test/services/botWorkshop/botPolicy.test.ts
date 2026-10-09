import { mapBotDto } from '@/services/botWorkshop/botMapper';
import {
  getBotActionAvailability,
  getBotCollaborationMode,
  getBotEntryAvailability,
  getGeneralConfigAvailability,
  getInventoryActionAvailability,
} from '@/services/botWorkshop/botPolicy';

describe('botPolicy', () => {
  test('部署中禁止进入、编辑和对话，但保留日志排障', () => {
    const bot = mapBotDto({ bot_id: 'b-1', status: 'PENDING', bot_type: 'service' }).item;
    const actions = getBotActionAvailability(bot, { canEdit: true });

    expect(actions.find((action) => action.action === 'view')?.enabled).toBe(false);
    expect(actions.find((action) => action.action === 'chat')?.enabled).toBe(false);
    expect(actions.find((action) => action.action === 'edit')?.enabled).toBe(false);
    expect(actions.find((action) => action.action === 'edit')?.disabledReason).toContain('部署中');
    expect(actions.find((action) => action.action === 'logs')).toMatchObject({ visible: true, enabled: true });
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

describe('通用配置菜单项门禁（collab-permission-entry-migration AC-02/AC-03）', () => {
  test('Bot 管理员（Owner）且无锁时可打开', () => {
    expect(getGeneralConfigAvailability(true, false)).toEqual({ enabled: true });
  });

  test('非 Bot 管理员禁用并说明原因', () => {
    expect(getGeneralConfigAvailability(false, false)).toEqual({
      enabled: false,
      disabledReason: '仅 Bot 管理员可变更通用配置',
    });
  });

  test('他人持编辑锁时禁用并说明原因', () => {
    expect(getGeneralConfigAvailability(true, true)).toEqual({
      enabled: false,
      disabledReason: '该 Bot 正被他人编辑，请先抢锁',
    });
  });

  test('非管理员与持锁同时存在时管理员原因优先', () => {
    expect(getGeneralConfigAvailability(false, true).disabledReason).toBe('仅 Bot 管理员可变更通用配置');
  });
});

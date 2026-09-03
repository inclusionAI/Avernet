import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { getBotActionAvailability, getBotCollaborationMode } from '@/services/botWorkshop/botPolicy';

describe('botPolicy', () => {
  test('部署中只允许查看和日志', () => {
    const bot = mapBotDto({ bot_id: 'b-1', status: 'PENDING', bot_type: 'service' }).item;
    const actions = getBotActionAvailability(bot, { canEdit: true });

    expect(actions.find((action) => action.action === 'view')?.enabled).toBe(true);
    expect(actions.find((action) => action.action === 'edit')?.enabled).toBe(false);
    expect(actions.find((action) => action.action === 'edit')?.disabledReason).toContain('部署中');
    expect(actions.find((action) => action.action === 'logs')).toMatchObject({ visible: true, enabled: true });
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

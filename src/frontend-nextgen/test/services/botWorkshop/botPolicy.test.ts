import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { getBotActionAvailability } from '@/services/botWorkshop/botPolicy';

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

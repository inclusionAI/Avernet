import { mapBotDto } from '@/services/bots/botMapper';
import { resolveBotLifecycle, resolveServiceEngineLifecycle } from '@/services/bots/botLifecycleResolver';
import { getBotEntryAvailability, getInventoryActionAvailability } from '@/services/botWorkshop/botPolicy';

describe('服务 Bot 发布记录与草稿实例状态隔离', () => {
  const source = {
    bot_id: 'service-upgrade', bot_type: 'service', kind: 'service', engine: 'openclaw',
    owner_entity_id: 'owner', status: 'PENDING',
  };

  test('同一 Bot 升级时草稿部署中，线上版本继续运行且保留后端授予的对话入口', () => {
    const draft = mapBotDto({
      ...source, card_id: 'service:service-upgrade:2',
      publish: { id: 2, status: 'draft', version: 2 }, actions: ['view', 'edit'],
    }).item;
    const online = mapBotDto({
      ...source, card_id: 'service:service-upgrade:1',
      publish: { id: 1, status: 'success', version: 1 }, actions: ['view', 'chat'],
    }).item;

    expect(draft.lifecycle).toBe('deploying');
    expect(getBotEntryAvailability(draft).enabled).toBe(false);
    expect(getInventoryActionAvailability(draft, 'edit').enabled).toBe(false);
    expect(online.lifecycle).toBe('running');
    expect(getBotEntryAvailability(online).enabled).toBe(true);
    expect(getInventoryActionAvailability(online, 'chat')).toMatchObject({ visible: true, enabled: true });
    expect(getInventoryActionAvailability({ ...online, actions: ['view'] }, 'chat').enabled).toBe(false);
  });

  test.each(['PENDING', 'PROVISIONING', 'REACTIVATING', 'FAILED', 'ERROR', 'OFFLINE', 'RELEASED', 'RELEASING', 'RECYCLED'])(
    '源 Bot 为 %s 时非草稿记录仍按自己的发布阶段解析', (status) => {
      const stages = {
        success: 'running', validating: 'prestable', failed: 'failed', released: 'offline',
        upgraded: 'offline', building: 'deploying', built: 'deploying',
        validate_pub: 'deploying', online_pub: 'deploying',
      };
      for (const [publishStatus, expected] of Object.entries(stages)) {
        const dto = { ...source, status, publish: { status: publishStatus }, display_state: 'service_draft' };
        expect(resolveBotLifecycle(dto, true, [])).toBe(expected);
        expect(resolveServiceEngineLifecycle(dto)).toBe(expected);
      }
    },
  );

  test.each([
    ['PENDING', 'deploying'], ['PROVISIONING', 'deploying'], ['REACTIVATING', 'deploying'],
    ['FAILED', 'failed'], ['ERROR', 'failed'], ['RECYCLED', 'offline'], ['ACTIVE', 'draft'],
  ])('草稿和尚无发布阶段的服务 Bot 保留实体 %s 的处理', (status, expected) => {
    for (const publish of [{ status: 'draft' }, null]) {
      expect(resolveBotLifecycle({ ...source, status, publish }, true, [])).toBe(expected);
    }
  });

  test('个人 Bot 的状态映射不受服务发布记录隔离影响', () => {
    expect(mapBotDto({ bot_id: 'personal', bot_type: 'personal', status: 'PENDING', publish_status: 'success' }).item.lifecycle)
      .toBe('deploying');
  });
});

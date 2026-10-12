import { mapBrowseSubscriptionDto } from '@/domain/lab/mapper';

describe('mapBrowseSubscriptionDto', () => {
  it('snake_case → camelCase，保留 mode/note', () => {
    expect(
      mapBrowseSubscriptionDto({ bot_id: 'b1', owner_user_id: 'u1', mode: 'framework', note: '每周扫一遍' }),
    ).toEqual({ botId: 'b1', ownerUserId: 'u1', mode: 'framework', note: '每周扫一遍' });
  });

  it('note 缺省归一为 null（UI 按空串展示）', () => {
    expect(mapBrowseSubscriptionDto({ bot_id: 'b2', owner_user_id: 'u2' }).note).toBeNull();
  });

  it('未提供 mode 时保留 undefined（enabled 由调用方推导，不依赖该字段）', () => {
    expect(mapBrowseSubscriptionDto({ bot_id: 'b3', owner_user_id: 'u3' }).mode).toBeUndefined();
  });
});

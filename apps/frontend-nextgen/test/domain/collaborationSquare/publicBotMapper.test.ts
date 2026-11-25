/** @jest-environment jsdom */
import type { PublicBotCatalogTransport } from '@/domain/collaborationSquare/mapper';
import { mapPublicBotCatalogDto, parseSquareDeepLink } from '@/domain/collaborationSquare/mapper';

const baseTransport: PublicBotCatalogTransport = {
  bot_id: 'bot-1',
  bot_uuid: 'bot-1:900001',
  entity_id: '900001',
  name: '示例 Bot',
  owner_name: 'Owner',
  description: '描述',
};

describe('mapPublicBotCatalogDto 公开受众类型派生', () => {
  test('仅对用户公开（user_visibility=public 且 visibility 非 public）派生 audience=user', () => {
    const bot = mapPublicBotCatalogDto({ ...baseTransport, user_visibility: 'public', visibility: 'private' });
    expect(bot?.audience).toBe('user');
  });

  test('仅对 Bot 公开（visibility=public 且 user_visibility 非 public）派生 audience=bot', () => {
    const bot = mapPublicBotCatalogDto({ ...baseTransport, visibility: 'public', user_visibility: 'private' });
    expect(bot?.audience).toBe('bot');
  });

  test('两类均公开或可见性字段缺失时不携带 audience（任意身份可见，无需干预）', () => {
    const bothPublic = mapPublicBotCatalogDto({
      ...baseTransport,
      visibility: 'public',
      user_visibility: 'public',
    });
    expect(bothPublic?.audience).toBeUndefined();
    const missingFields = mapPublicBotCatalogDto({ ...baseTransport });
    expect(missingFields?.audience).toBeUndefined();
  });
});

describe('parseSquareDeepLink audience 解析', () => {
  test('解析 audience=user 参数并忽略非法取值', () => {
    const deepLink = parseSquareDeepLink('?resource=bot&id=bot%3Atarget&name=x&audience=user', 'bot');
    expect(deepLink?.audience).toBe('user');
    const invalid = parseSquareDeepLink('?resource=bot&id=bot%3Atarget&audience=other', 'bot');
    expect(invalid?.audience).toBeUndefined();
    const absent = parseSquareDeepLink('?resource=bot&id=bot%3Atarget', 'bot');
    expect(absent?.audience).toBeUndefined();
  });
});

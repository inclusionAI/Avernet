import { generateBotAvatar, generateBotAvatarBatch } from '@/services/botWorkshop/botAvatarGenerator';
import { resolveBotAvatarUrl } from '@/domain/botAvatar';

describe('botAvatarGenerator', () => {
  it('保留自定义图片，历史缺失头像与管理页同 ID 同图，不依赖名称或刷新次数', () => {
    expect(resolveBotAvatarUrl(' /avatar.svg ', 'b1')).toBe('/avatar.svg');
    for (const missing of [undefined, null, '', '  ']) {
      expect(resolveBotAvatarUrl(missing, ' b1 ')).toBe(generateBotAvatar('b1'));
    }
    expect(resolveBotAvatarUrl(null, 'b1')).not.toBe(resolveBotAvatarUrl(null, 'b2'));
  });
  it('uses the management Bot ID rather than its collaboration ownership suffix for default avatars', () => {
    for (const id of ['b1', 'b1:owner-1', 'b1:owner-2', ' b1:owner-1 ']) {
      expect(resolveBotAvatarUrl(undefined, id)).toBe(generateBotAvatar('b1'));
    }
    expect(resolveBotAvatarUrl('/saved.svg', 'b1:owner-1')).toBe('/saved.svg');
  });

  it('uses the DiceBear Bottts data URI for a stable seed', () => {
    expect(generateBotAvatar('bot-1')).toMatch(/^data:image\/svg\+xml/);
    expect(generateBotAvatar('bot-1')).toBe(generateBotAvatar('bot-1'));
    expect(generateBotAvatar('bot-1')).not.toBe(generateBotAvatar('bot-2'));
  });

  it('generates eight distinct variants in each batch', () => {
    expect([...new Set(generateBotAvatarBatch('bot-1', 0))]).toHaveLength(8);
  });
});

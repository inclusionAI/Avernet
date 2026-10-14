import { generateBotAvatar, generateBotAvatarBatch } from '@/services/botWorkshop/botAvatarGenerator';

describe('botAvatarGenerator', () => {
  it('uses the DiceBear Bottts data URI for a stable seed', () => {
    expect(generateBotAvatar('bot-1')).toMatch(/^data:image\/svg\+xml/);
    expect(generateBotAvatar('bot-1')).toBe(generateBotAvatar('bot-1'));
    expect(generateBotAvatar('bot-1')).not.toBe(generateBotAvatar('bot-2'));
  });

  it('generates eight distinct variants in each batch', () => {
    expect(new Set(generateBotAvatarBatch('bot-1', 0))).toHaveSize(8);
  });
});

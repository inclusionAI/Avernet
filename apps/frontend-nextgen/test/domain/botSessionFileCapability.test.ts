import { supportsBotSessionFiles } from '@/domain/botSessionFileCapability';

it.each(['teclaw', ' TeClaw '])('禁用 %s 会话文件', (engine) => {
  expect(supportsBotSessionFiles({ engine })).toBe(false);
});
it.each(['openclaw', 'hermes', 'claude_code', 'aicoding', undefined])('保留 %s 原有文件能力', (engine) => {
  expect(supportsBotSessionFiles({ engine })).toBe(true);
});
it('无 Bot 或桌面 Bot 不支持会话文件', () => {
  expect(supportsBotSessionFiles(null)).toBe(false);
  expect(supportsBotSessionFiles({ engine: 'openclaw', botType: 'desktop' })).toBe(false);
});

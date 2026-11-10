import { formatBotSessionFileTime } from '@/services/workspace/sessionFileUtils';
import { describe, expect, it } from '@jest/globals';

describe('Bot Session File 北京时间', () => {
  it.each([
    ['2026-11-10T09:48:35Z', '11/10 17:48'],
    ['2026-11-10T17:48:35Z', '11/11 01:48'],
    ['2026-12-31T16:00:00Z', '01/01 00:00'],
    ['2026-11-10T17:48:35+08:00', '11/10 17:48'],
    [null, ''],
    [undefined, ''],
    ['', ''],
    ['invalid', ''],
  ])('%s → %s（不依赖浏览器本地时区）', (input, expected) => {
    expect(formatBotSessionFileTime(input)).toBe(expected);
  });
});

// 引擎徽章色彩口径（共享 presentation helper，见 src/components/BotEngineBadgeTone）：
// 对话侧 Bot 行徽章使用——OpenClaw 蓝(primary)、TEClaw 绿(success)、Hermes 黄(warning)、
// ClaudeCode 紫(purple)，未知引擎中性兜底(neutral)（含大小写变体）。
// Bot 管理「标签」列已统一中性底色，不再消费本映射。
import { getBotEngineBadgeTone } from '@/components/BotEngineBadgeTone';
import { expect, it } from '@jest/globals';

it('引擎 → 徽章 tone 映射：五类已知引擎各归其位，未知引擎中性兜底', () => {
  expect(getBotEngineBadgeTone('openclaw')).toBe('primary');
  expect(getBotEngineBadgeTone('teclaw')).toBe('success');
  expect(getBotEngineBadgeTone('hermes')).toBe('warning');
  expect(getBotEngineBadgeTone('claude_code')).toBe('purple');
  expect(getBotEngineBadgeTone('claude-code')).toBe('purple');
});

it('大小写变体一致归位（后端枚举大小写不统一时观感不漂移）', () => {
  expect(getBotEngineBadgeTone('OpenClaw')).toBe('primary');
  expect(getBotEngineBadgeTone('TEClaw')).toBe('success');
  expect(getBotEngineBadgeTone('TECLAW')).toBe('success');
  expect(getBotEngineBadgeTone('CLAUDE_CODE')).toBe('purple');
});

it('未知引擎与缺省引擎均回中性 tone（不给未识别 Bot 涂装误导色）', () => {
  expect(getBotEngineBadgeTone('unknown_engine')).toBe('neutral');
  expect(getBotEngineBadgeTone('')).toBe('neutral');
  expect(getBotEngineBadgeTone(undefined)).toBe('neutral');
});
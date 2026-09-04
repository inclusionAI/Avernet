import type { BotRuntime, BotRuntimeSource } from './types';

const DEFAULT_ENGINE = 'unknown';
const DEFAULT_BOT_ID_PATTERNS = [/^default[-_]/i, /^system[-_]/i];
const NORMAL_CC_TEMPLATE_TYPES = new Set(['normal', 'normalcc']);

export function resolveBotRuntime(source: BotRuntimeSource): BotRuntime {
  const sourceEngine = source.engine?.trim() || DEFAULT_ENGINE;
  const templateType = source.templateType?.trim();
  const normalizedTemplateType = templateType?.toLowerCase().replace(/[\s_-]/g, '');
  const botId = source.botId;
  const isAgentCodingBot =
    sourceEngine === 'aicoding' ||
    ((sourceEngine === 'claude_code' || sourceEngine === 'claudeCode') &&
      Boolean(templateType && !NORMAL_CC_TEMPLATE_TYPES.has(normalizedTemplateType ?? '')));

  return {
    engine: sourceEngine,
    isAgentCodingBot,
    templateType: source.templateType,
    templateName: source.templateName,
    botType: source.botType,
    // 默认 Bot 的派生规则收口在运行时解析层，页面不再直接判断 botId。
    isDefaultBot: Boolean(botId && DEFAULT_BOT_ID_PATTERNS.some((pattern) => pattern.test(botId))),
  };
}

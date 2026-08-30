import type { BotRuntime, BotRuntimeSource } from './types';

const DEFAULT_ENGINE = 'unknown';
const DEFAULT_BOT_ID_PATTERNS = [/^default[-_]/i, /^system[-_]/i];

export function resolveBotRuntime(source: BotRuntimeSource): BotRuntime {
  const engine = source.engine?.trim() || DEFAULT_ENGINE;

  return {
    engine,
    templateType: source.templateType,
    botType: source.botType,
    // 默认 Bot 的派生规则收口在运行时解析层，页面不再直接判断 botId。
    isDefaultBot: Boolean(source.botId && DEFAULT_BOT_ID_PATTERNS.some((pattern) => pattern.test(source.botId!))),
  };
}

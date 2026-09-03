const BOT_ENGINE_LABELS: Record<string, string> = {
  openclaw: 'OpenClaw',
  claude_code: 'ClaudeCode',
  claudecode: 'ClaudeCode',
  hermes: 'Hermes',
  teclaw: 'TEClaw',
};

const NON_ENGINE_LABELS = new Set(['teamclaw网关', 'teamclaw gateway']);

/** 将 bots 接口返回的 engine 枚举统一为身份列表中的可读标签。 */
export function getBotEngineLabel(engine?: string): string | undefined {
  const normalized = engine?.trim();
  if (!normalized || NON_ENGINE_LABELS.has(normalized.toLowerCase())) return undefined;
  return BOT_ENGINE_LABELS[normalized.toLowerCase()] ?? normalized;
}

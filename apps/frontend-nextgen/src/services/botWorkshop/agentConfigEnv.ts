/**
 * AgentCoding 容器侧约定的配置环境变量。
 * 创建时将用户填写的模板字段连同 Bot 名称放入一个 JSON env，保持与旧版运行时协议一致。
 */
export const AGENT_CONFIG_ENV_KEY = 'AGENT_CONFIG';

type Config = Record<string, unknown>;

type Envs = Record<string, string>;

export function buildAgentConfigEnvConfig(
  baseEnvs: unknown,
  formValues: Config | undefined,
  botName?: string,
): { envs?: Envs } {
  const next: Envs =
    baseEnvs && typeof baseEnvs === 'object' && !Array.isArray(baseEnvs)
      ? (Object.fromEntries(
          Object.entries(baseEnvs as Record<string, unknown>).filter(([, value]) => typeof value === 'string'),
        ) as Envs)
      : {};
  const payload: Config = { ...(formValues ?? {}) };
  const trimmedBotName = botName?.trim();
  if (trimmedBotName) payload.bot_name = trimmedBotName;

  if (Object.keys(payload).length > 0) {
    next[AGENT_CONFIG_ENV_KEY] = JSON.stringify(payload);
  } else {
    delete next[AGENT_CONFIG_ENV_KEY];
  }
  return Object.keys(next).length > 0 ? { envs: next } : {};
}

export function getAgentConfigFormValues(config: { envs?: unknown } | undefined): Config | null {
  const envs = config?.envs;
  const raw =
    envs && typeof envs === 'object' && !Array.isArray(envs)
      ? (envs as Record<string, unknown>)[AGENT_CONFIG_ENV_KEY]
      : undefined;
  if (typeof raw !== 'string') return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Config) : null;
  } catch {
    return null;
  }
}

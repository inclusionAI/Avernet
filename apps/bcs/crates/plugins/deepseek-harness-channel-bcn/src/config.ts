import Schema from '@deepseek-ai/schemastery';

export const DEFAULT_ONBOARDING_TOKEN_REF = 'BCN_ONBOARDING_TOKEN';
export const DEFAULT_BOT_SESSION_REF = 'BCN_BOT_SESSION';

export interface Config {
  enabled: boolean;
  /** HTTP(S) base URL whose path is the BCN API prefix, for example https://bcn.example.com/bcn/. */
  endpoint: string;
  /** Bot display name used only for first registration. */
  botName: string;
  summary: string;
  domains: string[];
  skills: string[];
  scopes: string[];
  onboardingTokenRef: string;
  botSessionRef: string;
  connectionTimeoutMs: number;
  heartbeatIntervalMs: number;
  reconnectInitialMs: number;
  reconnectMaxMs: number;
}

export const Config: Schema<Config> = Schema.object({
  enabled: Schema.boolean().default(false),
  endpoint: Schema.string()
    .default('')
    .description('Public BCN HTTP(S) base endpoint.'),
  botName: Schema.string()
    .default('')
    .description('BCN Bot display name, required for first registration.'),
  summary: Schema.string()
    .default('DeepSeek Harness bot connected through the Avernet BCN channel.'),
  domains: Schema.array(Schema.string()).default([]),
  skills: Schema.array(Schema.string()).default([]),
  scopes: Schema.array(Schema.string()).default([]),
  onboardingTokenRef: Schema.string().default(DEFAULT_ONBOARDING_TOKEN_REF),
  botSessionRef: Schema.string().default(DEFAULT_BOT_SESSION_REF),
  connectionTimeoutMs: Schema.natural().min(1_000).default(10_000),
  heartbeatIntervalMs: Schema.natural().min(1_000).default(30_000),
  reconnectInitialMs: Schema.natural().min(100).default(1_000),
  reconnectMaxMs: Schema.natural().min(1_000).default(30_000),
});

export function normalizeConfig(config: Config): Config {
  const result: Config = {
    ...config,
    endpoint: config.endpoint.trim(),
    botName: config.botName.trim(),
    summary: config.summary.trim(),
    domains: normalizeStringList(config.domains, 'domains'),
    skills: normalizeStringList(config.skills, 'skills'),
    scopes: normalizeStringList(config.scopes, 'scopes'),
    onboardingTokenRef: config.onboardingTokenRef.trim(),
    botSessionRef: config.botSessionRef.trim(),
  };

  if (result.botName && (Array.from(result.botName).length < 2 || Array.from(result.botName).length > 64)) {
    throw new Error('botName must contain 2-64 characters');
  }
  if (!result.summary) throw new Error('summary must not be empty');
  if (result.reconnectMaxMs < result.reconnectInitialMs) {
    throw new Error('reconnectMaxMs must be greater than or equal to reconnectInitialMs');
  }
  return result;
}

function normalizeStringList(values: string[], field: string): string[] {
  const normalized: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const item = value.trim();
    if (!item) throw new Error(`${field} must not contain empty values`);
    if (seen.has(item)) continue;
    seen.add(item);
    normalized.push(item);
  }
  return normalized;
}

import { readFileSync } from 'node:fs';

const MODEL_PROVIDER_ENV_KEYS = new Set([
  'ANTHROPIC_API_KEY',
  'ANTHROPIC_AUTH_TOKEN',
  'ANTHROPIC_BASE_URL',
  'ANTHROPIC_MODEL',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_REASONING_MODEL',
  'ANTHROPIC_SMALL_FAST_MODEL',
  'API_TIMEOUT_MS',
]);

/**
 * Read only the provider settings needed by the Claude SDK / CLI from a
 * singlebox-supplied settings file. The source path is local-only, values are
 * never logged, and the role-specific Claude config directory remains the
 * owner of session and profile state.
 */
export function loadRelayModelProviderEnv(): Record<string, string> {
  const sourcePath = process.env.RELAY_MODEL_SETTINGS_SOURCE?.trim();
  if (!sourcePath) return {};

  try {
    const parsed = JSON.parse(readFileSync(sourcePath, 'utf8')) as { env?: unknown };
    if (!parsed.env || typeof parsed.env !== 'object' || Array.isArray(parsed.env)) return {};

    return Object.fromEntries(
      Object.entries(parsed.env).filter(
        ([ key, value ]) => MODEL_PROVIDER_ENV_KEYS.has(key) && typeof value === 'string' && value.trim(),
      ),
    );
  } catch {
    return {};
  }
}

import { asNonEmptyString, asRecord, type ProviderRegistration } from './protocol.js';

export interface ProviderTokenScope {
  providerId: string;
  owner: string;
}

/** Inspect routing hints only. BCS authenticates the signature and authorizes registration. */
export function registrationTokenScope(token: string): ProviderTokenScope | undefined {
  let payload: Record<string, unknown> | undefined;
  try {
    if (!/^[A-Za-z0-9_-]+$/.test(token) || token.length > 16_384) throw new Error();
    const bytes = Buffer.from(token, 'base64url');
    if (bytes.length <= 32 || bytes.toString('base64url') !== token) throw new Error();
    payload = asRecord(JSON.parse(bytes.subarray(0, -32).toString('utf8')));
  } catch {
    throw new Error('BCN onboarding token has an invalid format');
  }
  if (!payload || typeof payload.id !== 'string' || !/^human_.+/.test(payload.id)
    || typeof payload.exp !== 'number' || !Number.isSafeInteger(payload.exp) || payload.exp <= Date.now() / 1_000) {
    throw new Error('BCN onboarding token is expired or incomplete');
  }
  if (payload.v === 1) return undefined;
  const providerId = asNonEmptyString(payload.provider_id);
  if (payload.v !== 2 || payload.purpose !== 'provider_bot_registration' || !providerId || providerId !== payload.provider_id
    || !Array.isArray(payload.allowed_modes) || !payload.allowed_modes.includes('plugin')) {
    throw new Error('BCN onboarding token does not authorize Provider plugin registration');
  }
  if (payload.auth_mode !== undefined && payload.auth_mode !== 'static_bearer') {
    throw new Error('DSH Provider registration requires static_bearer authentication');
  }
  return { providerId, owner: payload.id };
}

export function validateRegistrationResponse(
  value: unknown,
  expected: { scope: ProviderTokenScope; providerBotRef: string },
): ProviderRegistration {
  const record = asRecord(value);
  if (record?.provider_id !== expected.scope.providerId || record.provider_bot_ref !== expected.providerBotRef
    || record.mode !== 'plugin' || record.auth_mode !== 'static_bearer'
    || record.webhook_url != null || record.effective_webhook_url != null) {
    throw new Error('BCN registration returned mismatched Provider identity or connection mode');
  }
  return { providerId: expected.scope.providerId, providerBotRef: expected.providerBotRef,
    mode: 'plugin', authMode: 'static_bearer' };
}

import { getBotRegistrationToken } from '@/services/backendApi/collaboration/collaborationRegistrationController';
import type { DomainError, DomainResult } from './identityService';

export interface BotRegistrationTokenView {
  token: string;
  expiresAt: number;
  note?: string;
}

interface AvernetRuntimeConfig {
  BCS_ENDPOINT_PRE?: string;
  BCS_ENDPOINT_PROD?: string;
}

export function selectBcsEndpoint({
  env,
  hostname,
  preEndpoint,
  prodEndpoint,
}: {
  env?: string;
  hostname?: string;
  preEndpoint?: string;
  prodEndpoint?: string;
}): string | null {
  const host = hostname ?? (typeof window !== 'undefined' ? window.location.hostname : '');
  const currentEnv = env ?? (typeof TEAMCLAW_DEV_ENV !== 'undefined' ? TEAMCLAW_DEV_ENV : undefined);
  const isProd = currentEnv
    ? currentEnv === 'PROD'
    : Boolean(host) &&
      !host.includes('-pre') &&
      !host.includes('-dev') &&
      !host.includes('localhost') &&
      !host.includes('127.0.0.1');
  const endpoint = isProd ? prodEndpoint : preEndpoint;
  return endpoint?.trim() || null;
}

export function resolveBcsEndpoint(): string | null {
  const runtimeConfig =
    typeof window === 'undefined'
      ? {}
      : (window as { AVERNET_RUNTIME_CONFIG?: AvernetRuntimeConfig }).AVERNET_RUNTIME_CONFIG ?? {};
  const preEndpoint =
    runtimeConfig.BCS_ENDPOINT_PRE || (typeof BCS_ENDPOINT_PRE !== 'undefined' ? BCS_ENDPOINT_PRE : '');
  const prodEndpoint =
    runtimeConfig.BCS_ENDPOINT_PROD || (typeof BCS_ENDPOINT_PROD !== 'undefined' ? BCS_ENDPOINT_PROD : '');
  return selectBcsEndpoint({ preEndpoint, prodEndpoint });
}

function toDomainError(friendlyMessage: string, canRetry = true): DomainError {
  return { code: 'BOT_REGISTRATION_FAILED', friendlyMessage, canRetry };
}

function isAborted(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : (error as { name?: string })?.name === 'AbortError';
}

function mapToken(data: unknown): BotRegistrationTokenView | null {
  const record = typeof data === 'object' && data !== null ? (data as Record<string, unknown>) : null;
  const token = typeof record?.token === 'string' ? record.token.trim() : '';
  const expiresAt = typeof record?.expires_at === 'number' ? record.expires_at : undefined;
  if (!token || expiresAt === undefined) return null;
  return {
    token,
    expiresAt,
    note: typeof record?.note === 'string' && record.note.trim() ? record.note.trim() : undefined,
  };
}

export const botRegistrationService = {
  async getRegistrationToken(signal?: AbortSignal): Promise<DomainResult<BotRegistrationTokenView>> {
    try {
      const response = await getBotRegistrationToken(signal);
      if (response.code !== 20000) {
        return { ok: false, error: toDomainError(response.message || '获取接入 Token 失败，请稍后重试。') };
      }
      const token = mapToken(response.data);
      if (!token) return { ok: false, error: toDomainError('接入 Token 返回格式不正确，请稍后重试。') };
      return { ok: true, data: token };
    } catch (error) {
      if (isAborted(error)) {
        return { ok: false, error: toDomainError('已取消获取接入 Token。', false) };
      }
      return { ok: false, error: toDomainError('获取接入 Token 失败，请稍后重试。') };
    }
  },
};

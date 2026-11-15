import {
  getBotRegistrationToken,
  getRegistrationProvider,
} from '@/services/backendApi/collaboration/collaborationRegistrationController';
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

function browserSameOrigin(): string {
  return typeof window === 'undefined' ? '' : window.location?.origin ?? '';
}

/**
 * 运行时 endpoint 优先，但「键存在且为空串」是 bcs-only 部署的显式信号：
 * 容器未配置 BCS_ENDPOINT_PRE/PROD 时回退浏览器同源，绝不能落回编译期常量
 * （其缺省是 http://127.0.0.1:21000，会把 Bot 接入命令指向访问者本机）。
 * 键不存在（本地 dev 未注入 runtime-config.js）时保持既有编译期回退。
 */
function resolveRuntimeEndpoint(
  runtimeConfig: AvernetRuntimeConfig,
  key: keyof AvernetRuntimeConfig,
  buildTimeEndpoint: string,
): string {
  if (!Object.prototype.hasOwnProperty.call(runtimeConfig, key)) return buildTimeEndpoint;
  return runtimeConfig[key]?.trim() || browserSameOrigin();
}

export function resolveBcsEndpoint(): string | null {
  const runtimeConfig =
    typeof window === 'undefined'
      ? {}
      : (window as { AVERNET_RUNTIME_CONFIG?: AvernetRuntimeConfig }).AVERNET_RUNTIME_CONFIG ?? {};
  const preEndpoint = resolveRuntimeEndpoint(
    runtimeConfig,
    'BCS_ENDPOINT_PRE',
    typeof BCS_ENDPOINT_PRE !== 'undefined' ? BCS_ENDPOINT_PRE : '',
  );
  const prodEndpoint = resolveRuntimeEndpoint(
    runtimeConfig,
    'BCS_ENDPOINT_PROD',
    typeof BCS_ENDPOINT_PROD !== 'undefined' ? BCS_ENDPOINT_PROD : '',
  );
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

function mapToken(data: unknown, providerId?: string): BotRegistrationTokenView | null {
  const record = typeof data === 'object' && data !== null ? (data as Record<string, unknown>) : null;
  const token = typeof record?.token === 'string' ? record.token.trim() : '';
  const expiresAt = typeof record?.expires_at === 'number' ? record.expires_at : undefined;
  if (!token || expiresAt === undefined || !Number.isFinite(expiresAt)) return null;
  const registration =
    typeof record?.registration === 'object' && record.registration !== null
      ? (record.registration as Record<string, unknown>)
      : null;
  if (
    providerId &&
    (registration?.token_version !== 2 ||
      registration.provider_id !== providerId ||
      !Array.isArray(registration.allowed_modes) ||
      !registration.allowed_modes.includes('plugin'))
  )
    return null;
  return {
    token,
    expiresAt,
    note: typeof record?.note === 'string' && record.note.trim() ? record.note.trim() : undefined,
  };
}

export const botRegistrationService = {
  async getRegistrationToken(
    signal?: AbortSignal,
    providerSlug?: string,
  ): Promise<DomainResult<BotRegistrationTokenView>> {
    try {
      let providerId: string | undefined;
      if (providerSlug) {
        const provider = await getRegistrationProvider(providerSlug, signal);
        if (!provider.enabled || provider.slug !== providerSlug || !provider.provider_id?.trim()) {
          return { ok: false, error: toDomainError('本地 Bot 接入方尚未启用，请联系管理员。') };
        }
        providerId = provider.provider_id;
      }
      const response = await getBotRegistrationToken(signal, providerId);
      if (response.code !== 20000) {
        return { ok: false, error: toDomainError(response.message || '获取接入 Token 失败，请稍后重试。') };
      }
      const token = mapToken(response.data, providerId);
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

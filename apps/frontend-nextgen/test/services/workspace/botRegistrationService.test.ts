import {
  getBotRegistrationToken,
  getRegistrationProvider,
  type BotRegistrationTokenDto,
} from '@/services/backendApi/collaboration/collaborationRegistrationController';
import type { BackendApiEnvelope } from '@/services/backendApi/types';
import {
  botRegistrationService,
  resolveBcsEndpoint,
  selectBcsEndpoint,
} from '@/services/workspace/botRegistrationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/collaborationRegistrationController');

const getBotRegistrationTokenMock = getBotRegistrationToken as jest.MockedFunction<typeof getBotRegistrationToken>;

const getProviderMock = getRegistrationProvider as jest.MockedFunction<typeof getRegistrationProvider>;

function successResponse(data: BotRegistrationTokenDto) {
  return { code: 20000, message: 'OK', data } satisfies BackendApiEnvelope<BotRegistrationTokenDto>;
}

describe('botRegistrationService', () => {
  beforeEach(() => {
    getBotRegistrationTokenMock.mockReset();
    getProviderMock.mockReset();
  });

  it('selects BCS endpoint by runtime environment', () => {
    const input = {
      preEndpoint: 'https://pre.example.com',
      prodEndpoint: 'https://prod.example.com',
    };

    expect(selectBcsEndpoint({ ...input, env: 'PRE' })).toBe(input.preEndpoint);
    expect(selectBcsEndpoint({ ...input, env: 'DEV' })).toBe(input.preEndpoint);
    expect(selectBcsEndpoint({ ...input, env: 'LOCAL' })).toBe(input.preEndpoint);
    expect(selectBcsEndpoint({ ...input, env: 'PROD' })).toBe(input.prodEndpoint);
    expect(selectBcsEndpoint({ ...input, hostname: 'teamclaw-pre.example.com' })).toBe(input.preEndpoint);
    expect(selectBcsEndpoint({ ...input, hostname: 'teamclaw.example.com' })).toBe(input.prodEndpoint);
  });

  it('prefers deployment runtime config over build-time placeholders', () => {
    const runtimeWindow = {
      AVERNET_RUNTIME_CONFIG: {
        BCS_ENDPOINT_PRE: 'https://runtime-pre.example.com',
        BCS_ENDPOINT_PROD: 'https://runtime-prod.example.com',
      },
      location: { hostname: 'localhost' },
    };
    const globalWithOptionalWindow = globalThis as unknown as { window?: unknown };
    globalWithOptionalWindow.window = runtimeWindow;

    try {
      expect(resolveBcsEndpoint()).toBe('https://runtime-pre.example.com');
    } finally {
      Reflect.deleteProperty(globalWithOptionalWindow, 'window');
    }
  });

  it('falls back to the browser same origin when deployment emits empty runtime endpoints', () => {
    // bcs-only 部署入口脚本把未配置的 BCS_ENDPOINT_PRE/PROD 写成空串（键存在）：
    // 此时必须回退浏览器同源，绝不能落回编译期常量 http://127.0.0.1:21000
    // （那会让真实部署生成指向访问者本机的 Bot 接入命令）。
    const runtimeWindow = {
      AVERNET_RUNTIME_CONFIG: { BCS_ENDPOINT_PRE: '', BCS_ENDPOINT_PROD: '' },
      location: { hostname: 'discovery.example.com', origin: 'https://discovery.example.com' },
    };
    const globalWithOptionalWindow = globalThis as unknown as { window?: unknown };
    globalWithOptionalWindow.window = runtimeWindow;

    try {
      expect(resolveBcsEndpoint()).toBe('https://discovery.example.com');
      expect(resolveBcsEndpoint()).not.toBe('http://127.0.0.1:21000');
    } finally {
      Reflect.deleteProperty(globalWithOptionalWindow, 'window');
    }
  });

  it('resolves each environment endpoint independently from its own runtime key', () => {
    const runtimeWindow = {
      AVERNET_RUNTIME_CONFIG: { BCS_ENDPOINT_PRE: '', BCS_ENDPOINT_PROD: 'https://bcs.example.com' },
      location: { hostname: 'teamclaw.example.com', origin: 'https://teamclaw.example.com' },
    };
    const globalWithOptionalWindow = globalThis as unknown as { window?: unknown };
    globalWithOptionalWindow.window = runtimeWindow;

    try {
      // 生产环境取到非空 runtime 值就原样使用，不被同源回退干扰。
      expect(resolveBcsEndpoint()).toBe('https://bcs.example.com');
    } finally {
      Reflect.deleteProperty(globalWithOptionalWindow, 'window');
    }
  });

  it('keeps the compile-time endpoint when the runtime key is absent', () => {
    const runtimeWindow = {
      AVERNET_RUNTIME_CONFIG: {},
      location: { hostname: 'localhost', origin: 'http://localhost:8000' },
    };
    const globalWithOptionalWindow = globalThis as unknown as { window?: unknown };
    const globalWithBuildTime = globalThis as unknown as Record<string, unknown>;
    globalWithOptionalWindow.window = runtimeWindow;
    // jest 无 umi define：用全局变量模拟编译期常量注入（typeof 判定的同一解析路径）。
    globalWithBuildTime.BCS_ENDPOINT_PRE = 'https://build-pre.example.com';
    globalWithBuildTime.BCS_ENDPOINT_PROD = 'https://build-prod.example.com';

    try {
      expect(resolveBcsEndpoint()).toBe('https://build-pre.example.com');
    } finally {
      Reflect.deleteProperty(globalWithOptionalWindow, 'window');
      Reflect.deleteProperty(globalWithBuildTime, 'BCS_ENDPOINT_PRE');
      Reflect.deleteProperty(globalWithBuildTime, 'BCS_ENDPOINT_PROD');
    }
  });

  it('returns null for empty runtime endpoints without a browser origin', () => {
    // 非浏览器环境安全处理：没有 origin 可用时不得抛错，也不得泄漏编译期回环地址。
    const runtimeWindow = {
      AVERNET_RUNTIME_CONFIG: { BCS_ENDPOINT_PRE: '', BCS_ENDPOINT_PROD: '' },
      location: { hostname: 'localhost' },
    };
    const globalWithOptionalWindow = globalThis as unknown as { window?: unknown };
    globalWithOptionalWindow.window = runtimeWindow;

    try {
      expect(resolveBcsEndpoint()).toBeNull();
    } finally {
      Reflect.deleteProperty(globalWithOptionalWindow, 'window');
    }
  });

  it('maps token response to domain view', async () => {
    getBotRegistrationTokenMock.mockResolvedValue(
      successResponse({
        token: ' token-1 ',
        expires_at: 1788272686000,
        note: ' Use this token for bot registration within 6 hours ',
      }),
    );

    const result = await botRegistrationService.getRegistrationToken();

    expect(result).toEqual({
      ok: true,
      data: {
        token: 'token-1',
        expiresAt: 1788272686000,
        note: 'Use this token for bot registration within 6 hours',
      },
    });
  });

  it('resolves the local slug before requesting its provider-scoped token', async () => {
    getProviderMock.mockResolvedValue({ provider_id: 'prv_local_test', slug: 'local', enabled: true });
    getBotRegistrationTokenMock.mockResolvedValue(
      successResponse({
        token: 'scoped-token',
        expires_at: 1788272686000,
        registration: {
          token_version: 2,
          provider_id: 'prv_local_test',
          allowed_modes: ['plugin'],
        },
      }),
    );
    const signal = new AbortController().signal;
    const result = await botRegistrationService.getRegistrationToken(signal, 'local');
    expect(result.ok).toBe(true);
    expect(getProviderMock).toHaveBeenCalledWith('local', signal);
    expect(getBotRegistrationTokenMock).toHaveBeenCalledWith(signal, 'prv_local_test');
  });

  it.each([
    undefined,
    { token_version: 1, provider_id: 'prv_local_test', allowed_modes: ['plugin'] },
    { token_version: 2, provider_id: 'prv_other', allowed_modes: ['plugin'] },
    { token_version: 2, provider_id: 'prv_local_test', allowed_modes: ['gateway'] },
  ])('rejects an incompatible Codex token: %j', async (scope) => {
    getProviderMock.mockResolvedValue({ provider_id: 'prv_local_test', slug: 'local', enabled: true });
    getBotRegistrationTokenMock.mockResolvedValue(
      successResponse({
        token: 'incompatible-token',
        expires_at: 1788272686000,
        registration: scope,
      }),
    );
    const result = await botRegistrationService.getRegistrationToken(undefined, 'local');
    expect(result.ok).toBe(false);
  });

  it('does not request a token for a disabled local provider', async () => {
    getProviderMock.mockResolvedValue({ provider_id: 'prv_local_test', slug: 'local', enabled: false });
    expect((await botRegistrationService.getRegistrationToken(undefined, 'local')).ok).toBe(false);
    expect(getBotRegistrationTokenMock).not.toHaveBeenCalled();
  });

  it('rejects malformed token data', async () => {
    getBotRegistrationTokenMock.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: { token: '', expires_at: 1788272686000 },
    } as never);

    const result = await botRegistrationService.getRegistrationToken();

    expect(result.ok).toBe(false);
    if (result.ok) throw new Error('Expected a failed result');
    expect(result.error.code).not.toBeUndefined();
  });

  it('rejects non-success business responses', async () => {
    getBotRegistrationTokenMock.mockResolvedValue({
      code: 40000,
      message: 'Failed',
      data: null,
    } as never);

    const result = await botRegistrationService.getRegistrationToken();

    expect(result.ok).toBe(false);
    if (result.ok) throw new Error('Expected a failed result');
    expect(result.error.friendlyMessage).toBe('Failed');
  });
});

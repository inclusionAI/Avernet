import {
  getBotRegistrationToken,
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

function successResponse(data: BotRegistrationTokenDto) {
  return { code: 20000, message: 'OK', data } satisfies BackendApiEnvelope<BotRegistrationTokenDto>;
}

describe('botRegistrationService', () => {
  beforeEach(() => {
    getBotRegistrationTokenMock.mockReset();
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

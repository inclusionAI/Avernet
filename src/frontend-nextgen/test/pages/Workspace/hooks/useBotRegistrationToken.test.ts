/** @jest-environment jsdom */
import { useBotRegistrationToken } from '@/pages/Workspace/hooks/useBotRegistrationToken';
import { botRegistrationService, resolveBcsEndpoint } from '@/services/workspace/botRegistrationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botRegistrationService');

const getRegistrationTokenMock = botRegistrationService.getRegistrationToken as jest.MockedFunction<
  typeof botRegistrationService.getRegistrationToken
>;
const resolveBcsEndpointMock = resolveBcsEndpoint as jest.MockedFunction<typeof resolveBcsEndpoint>;

describe('useBotRegistrationToken', () => {
  beforeEach(() => {
    getRegistrationTokenMock.mockReset();
    resolveBcsEndpointMock.mockReset();
    resolveBcsEndpointMock.mockReturnValue('http://127.0.0.1:21000');
  });

  it('fetches token when opened and clears it when closed', async () => {
    getRegistrationTokenMock.mockResolvedValue({
      ok: true,
      data: { token: 'token-1', expiresAt: 1788272686000, note: 'Use this token' },
    });

    const { result, rerender } = renderHook(({ open }) => useBotRegistrationToken(open), {
      initialProps: { open: false },
    });

    expect(getRegistrationTokenMock).not.toHaveBeenCalled();

    rerender({ open: true });
    expect(getRegistrationTokenMock).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(result.current.token).toBe('token-1'));
    expect(result.current.bcsEndpoint).toBe('http://127.0.0.1:21000');

    rerender({ open: false });
    expect(result.current.token).toBeNull();
  });

  it('exposes retry for failed requests', async () => {
    const responses = [
      { ok: false as const, error: { code: 'BOT_REGISTRATION_FAILED', friendlyMessage: 'failed', canRetry: true } },
      { ok: true as const, data: { token: 'token-2', expiresAt: 1788272686000 } },
    ];
    getRegistrationTokenMock
      .mockImplementationOnce(async () => responses[0])
      .mockImplementationOnce(async () => responses[1]);

    const { result } = renderHook(() => useBotRegistrationToken(true));
    await waitFor(() => expect(result.current.error).toBe('failed'));

    result.current.retry();
    await waitFor(() => expect(result.current.token).toBe('token-2'));
    expect(getRegistrationTokenMock).toHaveBeenCalledTimes(2);
  });
});

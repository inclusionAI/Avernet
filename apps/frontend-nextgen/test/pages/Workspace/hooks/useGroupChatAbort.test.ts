/** @jest-environment jsdom */
import { useGroupChatAbort } from '@/pages/Workspace/hooks/useGroupChatAbort';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('sonner');

const mockedToast = toast as unknown as Record<string, jest.Mock>;

function createProvider(abortBot: jest.Mock): GroupChatProvider {
  return { abortBot } as unknown as GroupChatProvider;
}

describe('useGroupChatAbort', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('asks the user to restart only when BCS identifies an unsupported plugin', async () => {
    const abortBot = jest.fn<() => Promise<never>>().mockRejectedValue(
      Object.assign(new Error('unsupported'), {
        code: 'chat_abort_not_supported',
        abortedRunIds: [],
        failures: [],
      }),
    );
    const { result } = renderHook(() => useGroupChatAbort(createProvider(abortBot), 'session-1'));

    await act(async () => {
      await result.current.abortBot('bot-1');
    });

    expect(result.current.unsupportedAbortBotId).toBe('bot-1');
    expect(mockedToast.error).not.toHaveBeenCalled();

    act(() => result.current.dismissAbortUnsupported());
    expect(result.current.unsupportedAbortBotId).toBeNull();
  });

  it('keeps ordinary transport failures on the existing toast path', async () => {
    const abortBot = jest.fn<() => Promise<never>>().mockRejectedValue(
      Object.assign(new Error('timeout'), {
        code: 'chat_abort_timeout',
        abortedRunIds: [],
        failures: [],
      }),
    );
    const { result } = renderHook(() => useGroupChatAbort(createProvider(abortBot), 'session-1'));

    await act(async () => {
      await result.current.abortBot('bot-1');
    });

    expect(result.current.unsupportedAbortBotId).toBeNull();
    expect(mockedToast.error).toHaveBeenCalledWith('终止请求超时，请稍后重试');
  });
});

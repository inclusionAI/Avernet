import { toGroupChatAbortErrorView } from '@/services/workspace/groupChatAbortService';
import { describe, expect, it } from '@jest/globals';

describe('toGroupChatAbortErrorView', () => {
  it('maps partial successes without exposing downstream failure details', () => {
    const error = Object.assign(new Error('internal endpoint timeout'), {
      code: 'chat_abort_partial_failure',
      abortedRunIds: ['run-1'],
      failures: [{ runId: 'run-2', message: 'private detail' }],
    });
    expect(toGroupChatAbortErrorView(error)).toEqual({
      message: '部分输出终止失败，可稍后重试',
      partial: true,
      abortedCount: 1,
      restartRequired: false,
    });
  });

  it('requires a Bot restart only for an explicit unsupported capability', () => {
    expect(
      toGroupChatAbortErrorView(
        Object.assign(new Error('unsupported'), {
          code: 'chat_abort_not_supported',
          abortedRunIds: [],
          failures: [],
        }),
      ),
    ).toEqual({
      message: '当前 Bot 使用的插件版本不支持终止输出',
      partial: false,
      abortedCount: 0,
      restartRequired: true,
    });

    expect(
      toGroupChatAbortErrorView(
        Object.assign(new Error('partial'), {
          code: 'chat_abort_partial_failure',
          abortedRunIds: ['run-1'],
          failures: [{ runId: 'run-2', code: 'chat_abort_not_supported' }],
        }),
      ).restartRequired,
    ).toBe(true);
  });

  it('maps authorization and unknown errors to safe user-facing messages', () => {
    expect(
      toGroupChatAbortErrorView(
        Object.assign(new Error('raw'), {
          code: 'forbidden',
          abortedRunIds: [],
          failures: [],
        }),
      ).message,
    ).toBe('你当前无权终止该会话中的输出');
    expect(toGroupChatAbortErrorView(new Error('wss://private.example/token')).message).toBe(
      '终止失败，请检查网络后重试',
    );
    expect(
      toGroupChatAbortErrorView(
        Object.assign(new Error('timeout'), {
          code: 'chat_abort_timeout',
          abortedRunIds: [],
          failures: [],
        }),
      ).restartRequired,
    ).toBe(false);
  });
});

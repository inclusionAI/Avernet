import { rsiErrorMessage } from '@/services/rsi/errors';

test('503 explains the deployment gap without claiming an unknown submission failed', () => {
  const cause = Object.assign(new Error('private upstream detail'), { status: 503 });
  const message = rsiErrorMessage(cause, 'fallback');
  expect(message).toContain('服务未接通或暂不可用');
  expect(message).toContain('无法确认任务状态');
  expect(message).not.toContain('private upstream detail');
});

test.each([undefined, null, 'failure', new Error('private detail'), { status: 503 }])(
  'unknown failures preserve a safe caller-specific message: %p',
  (cause) => {
    expect(rsiErrorMessage(cause, 'safe fallback')).toBe('safe fallback');
  },
);


test.each([[401, '登录凭证'], [403, 'Bot'], [409, '操作冲突']])(
  'HTTP %i is distinguished without leaking server details', (status, expected) => {
    const cause = Object.assign(new Error('private upstream detail'), { status });
    const message = rsiErrorMessage(cause, 'fallback');
    expect(message).toContain(expected);
    expect(message).toContain(String(status));
    expect(message).not.toContain('private upstream detail');
  },
);

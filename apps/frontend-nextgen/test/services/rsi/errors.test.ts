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

test('inaccessible Bot is distinguished from a missing Genome baseline', () => {
  const error = (code: string) => Object.assign(new Error('private upstream detail'), {
    status: 409,
    data: { error: { code } },
  });
  expect(rsiErrorMessage(error('evolution_app_not_granted'), 'fallback')).toContain('检查 evolution 应用');
  expect(rsiErrorMessage(error('no_genome_baseline'), 'fallback')).toContain('没有基因组基线');
});

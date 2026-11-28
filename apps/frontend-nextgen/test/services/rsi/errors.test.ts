import { RsiRequestKeyError } from '@/utils/rsiRequestKey';
import { isRsiBindingCheckRejected, rsiErrorMessage } from '@/services/rsi/errors';

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

test('Genome errors do not promise an idempotent retry or misattribute a Backend failure to Evolution', () => {
  const { genomeErrorMessage } = require('@/services/rsi/errors');
  const auth = genomeErrorMessage(Object.assign(new Error('private'), { status: 401 }), 'fallback');
  expect(auth).toContain('401'); expect(auth).not.toContain('幂等');
  expect(genomeErrorMessage(Object.assign(new Error('private'), { status: 503 }), 'Genome state unknown'))
    .toBe('Genome state unknown');
});


test('only a typed pre-request failure is reported as unsent', () => {
  expect(rsiErrorMessage(new RsiRequestKeyError(), 'unknown')).toContain('本次操作尚未发送');
  expect(rsiErrorMessage(new TypeError('crypto.randomUUID is not a function'), 'unknown')).toBe('unknown');
});


test('exact binding rejection has an actionable safe message, without upstream details', () => {
  const cause = Object.assign(new Error('private upstream detail'), {
    status: 422,
    data: { error: { code: 'binding_check_failed', details: ['private binding details'] } },
  });
  expect(isRsiBindingCheckRejected(cause)).toBe(true);
  const message = rsiErrorMessage(cause, 'unknown');
  expect(message).toContain('绑定校验失败');
  expect(message).toContain('本次提交未创建任务');
  expect(message).not.toContain('private');
});

test.each([
  [422, undefined], [422, 'other_error'], [500, 'binding_check_failed'],
  [503, 'binding_check_failed'], [409, 'binding_check_failed'],
])('HTTP %s / %s is not a definite pre-Run binding rejection', (status, code) => {
  const cause = Object.assign(new Error('private'), { status, data: { error: { code } } });
  expect(isRsiBindingCheckRejected(cause)).toBe(false);
  expect(rsiErrorMessage(cause, 'unknown')).not.toContain('本次提交未创建任务');
});

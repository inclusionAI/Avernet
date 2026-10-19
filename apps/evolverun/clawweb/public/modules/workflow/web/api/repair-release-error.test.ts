import { expect, it } from 'vitest'
import { repairReleaseError } from './repair-release-error'
it('shows explicit release recovery reasons without leaking raw responses', () => {
  expect(repairReleaseError({ status: 409, body: JSON.stringify({ code: 'STATE_CONFLICT', error: '线上基线已变化，请先重新审阅', requestId: 'private-id' }) })).toBe('线上基线已变化，请先重新审阅')
  expect(repairReleaseError({ status: 500, body: '<html>secret</html>' })).not.toContain('secret')
})
it('distinguishes read-only preparation failure from a possibly submitted publication', () => {
  const failure = { status: 500, body: JSON.stringify({ code: 'INTERNAL_ERROR', error: 'git secret stderr', requestId: 'read-failed-123' }) }
  expect(repairReleaseError(failure, 'prepare')).toContain('尚未提交发布')
  expect(repairReleaseError(failure, 'prepare')).toContain('read-failed-123')
  expect(repairReleaseError(failure, 'prepare')).not.toContain('secret')
  expect(repairReleaseError(failure, 'prepare')).not.toContain('操作结果尚未确认')
})
it('preserves a typed publication rejection and its diagnostic id instead of masking it as a lost receipt', () => {
  expect(repairReleaseError({ status: 409, body: JSON.stringify({ code: 'SOURCE_CHANGED', error: '生效版本已变化，请基于当前版本生成候选', requestId: 'reject-123' }) }))
    .toContain('生效版本已变化，请基于当前版本生成候选')
})

import { expect, it } from 'vitest'
import { repairReleaseError } from './repair-release-error'
it('shows explicit release recovery reasons without leaking raw responses', () => {
  expect(repairReleaseError({ status: 409, body: JSON.stringify({ code: 'STATE_CONFLICT', error: '线上基线已变化，请先重新审阅', requestId: 'private-id' }) })).toBe('线上基线已变化，请先重新审阅')
  expect(repairReleaseError({ status: 500, body: '<html>secret</html>' })).not.toContain('secret')
})

import { expect, it } from 'vitest'
import { repairActionFailure } from './repair-action-error'

it.each([
  [{ code: 'STATE_CONFLICT', recoveryAction: 'retry-result', reasonCode: 'RESULT_READ_UNKNOWN' }, 'retry-result', '核对原结果'],
  [{ code: 'STATE_CONFLICT', recoveryAction: 'retry-result', reasonCode: 'EXECUTION_RUNNING' }, 'retry-result', '上一轮仍在执行'],
  [{ code: 'STATE_CONFLICT', reasonCode: 'RESULT_RECOVERED' }, 'refresh-task', '原候选已恢复'],
  [{ code: 'SOURCE_CHANGED' }, 'refresh-sources', '建议来源已更新'],
])('maps structured recovery instructions without exposing the response body', (body, action, label) => {
  const result = repairActionFailure({ status: 409, body: JSON.stringify({ ...body, error: 'private upstream' }) })
  expect(result.action).toBe(action)
  expect(result.message).toContain(label)
  expect(result.message).not.toContain('private upstream')
})
it('keeps unknown write outcomes distinct from a confirmed result-read conflict', () => {
  expect(repairActionFailure(new Error('private network diagnostics'))).toMatchObject({ action: 'refresh-task' })
  expect(repairActionFailure({ status: 502, body: '<html>private proxy</html>' }).message).not.toContain('private')
})

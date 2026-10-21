import { expect, it } from 'vitest'
import { repairActionFailure } from './repair-action-error'

it('does not send a confirmed pre-creation baseline rejection into the check/retry loop', () => {
  const result = repairActionFailure({ status: 503, body: JSON.stringify({ code: 'CAPABILITY_UNAVAILABLE',
    reasonCode: 'BASELINE_READ_FAILED', stage: 'baseline', outcome: 'not_created', error: 'private git diagnostic' }) })
  expect(result.action).toBeNull()
  expect(result.message).toContain('未创建')
  expect(result.message).not.toContain('private')
  const unknown = repairActionFailure({ status: 500, body: JSON.stringify({ reasonCode: 'BASELINE_READ_FAILED' }) })
  expect(unknown.action).toBe('refresh-task')
  expect(unknown.message).not.toContain('未创建')
})

it.each([
  [{ code: 'STATE_CONFLICT', recoveryAction: 'retry-result', reasonCode: 'RESULT_READ_UNKNOWN' }, 'retry-result', '核对原结果'],
  [{ code: 'STATE_CONFLICT', recoveryAction: 'retry-result', reasonCode: 'EXECUTION_RUNNING' }, 'retry-result', '上一轮仍在执行'],
  [{ code: 'STATE_CONFLICT', reasonCode: 'RESULT_RECOVERED' }, 'refresh-task', '原候选已恢复'],
  [{ code: 'SOURCE_CHANGED' }, 'refresh-sources', '逐项核对已选建议'],
  [{ code: 'SOURCE_CHANGED', reasonCode: 'PLAN_SELECTION_INCOMPLETE' }, null, '成员已变化'],
  [{ code: 'STATE_CONFLICT', reasonCode: 'BASELINE_STALE' }, null, '生效版本在准备期间再次变化'],
  [{ code: 'INVALID_INPUT', reasonCode: 'PLAN_SCOPE_INCOMPLETE' }, null, '缺少明确修改范围'],
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
it('preserves the real baseline error without treating it as source mutation', () => {
  const result = repairActionFailure({ status: 409, body: JSON.stringify({ error: '原建议基线已变化，请重新评估', code: 'SOURCE_CHANGED', requestId: '20023274-6e68-4535-a90a-e36a7c10df49' }) })
  expect(result.action).toBeNull()
  expect(result.message).toContain('原建议基线已变化')
  expect(result.message).toContain('保留')
})

it('identifies frozen core size errors with safe sizes and request identity',()=>{
 const result=repairActionFailure({status:413,body:JSON.stringify({code:'PAYLOAD_TOO_LARGE',stage:'frozen_batch',limit:1048576,actualBytes:1112074,requestId:'1dd0fc30-37da-49bd-b65c-599f704d2eb4',error:'private payload'})});
 expect(result.message).toContain('核心输入');
 expect(result.message).toContain('1,112,074');
 expect(result.message).toContain('1,048,576');
 expect(result.message).toContain('1dd0fc30-37da-49bd-b65c-599f704d2eb4');
 expect(result.message).not.toContain('缩小范围');
 expect(result.message).not.toContain('private payload');
});

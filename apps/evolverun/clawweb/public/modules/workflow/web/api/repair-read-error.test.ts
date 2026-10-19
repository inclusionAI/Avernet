import { expect, it } from 'vitest'
import { repairReadError } from './repair-read-error'

it.each([
  [{ status: 401 }, '登录身份未确认（401）'],
  [{ status: 403 }, '访问被拒绝（403）'],
  [{ status: 500, body: '<html>private upstream failure</html>' }, '服务异常（500）'],
  [new Error('读取超时，请重试；其他内容仍可查看。'), '读取超时'],
  [{ status: 413, body: JSON.stringify({ code: 'PAYLOAD_TOO_LARGE', stage: 'request_body', actualBytes: 300000, limit: 262144, privateText: 'private upstream' }) }, '请求内容过大（413）'],
  [{ status: 413, body: '<html>private upstream</html>' }, '无法确认限制来源（413）'],
])('classifies repair read failures without showing raw upstream bodies', (error, label) => {
  expect(repairReadError(error)).toContain(label)
  expect(repairReadError(error)).not.toContain('private upstream')
})

it('shows safe stage and byte diagnostics for a bounded read response', () => {
  const result = repairReadError({ status: 413, body: JSON.stringify({ code: 'PAYLOAD_TOO_LARGE', stage: 'candidates_response', actualBytes: 5000000, limit: 4194304 }) })
  expect(result).toContain('列表响应过大（413）')
  expect(result).toContain('5000000 / 4194304 字节')
})

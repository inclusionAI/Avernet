import { expect, it } from 'vitest'
import { repairReadError } from './repair-read-error'

it.each([
  [{ status: 401 }, '登录身份未确认（401）'],
  [{ status: 403 }, '访问被拒绝（403）'],
  [{ status: 500, body: '<html>private upstream failure</html>' }, '服务异常（500）'],
  [new Error('读取超时，请重试；其他内容仍可查看。'), '读取超时'],
])('classifies repair read failures without showing raw upstream bodies', (error, label) => {
  expect(repairReadError(error)).toContain(label)
  expect(repairReadError(error)).not.toContain('private upstream')
})

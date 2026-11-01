import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { assertDeploymentAvailable, clearConfirmedDeployment, markDeploymentPending } from '../deploymentGuard'

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

it('stores only a workflow-scoped marker, blocks replacement, and clears on confirmed success', () => {
  markDeploymentPending('workflow/a')
  expect(localStorage.getItem('clawweb.autoheal.review-blocked.v1.workflow%2Fa')).toBe('1')
  expect(() => markDeploymentPending('workflow/a')).toThrow(/未确认/)
  expect(() => assertDeploymentAvailable('workflow/b')).not.toThrow()
  clearConfirmedDeployment('workflow/a')
  expect(() => assertDeploymentAvailable('workflow/a')).not.toThrow()
})

it('fails closed if reading or writing storage throws or silently fails', () => {
  const read = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
  expect(() => markDeploymentPending('workflow')).toThrow(/浏览器存储/)
  read.mockRestore()
  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {})
  expect(() => markDeploymentPending('workflow')).toThrow(/部署未发送/)
  write.mockRestore()
  markDeploymentPending('workflow')
  const remove = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {})
  expect(() => clearConfirmedDeployment('workflow')).toThrow(/无法清除/)
  remove.mockRestore()
  expect(() => assertDeploymentAvailable('workflow')).toThrow(/未确认/)
})

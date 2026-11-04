import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { assertDeploymentAvailable, clearConfirmedDeployment, clearReconciledDeployment, markDeploymentPending } from '../deploymentGuard'

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

it('stores a workflow-scoped marker, blocks replacement, and clears on confirmed success', () => {
  markDeploymentPending('workflow/a')
  expect(localStorage.getItem('clawweb.autoheal.review-blocked.v1.workflow%2Fa')).toBe('1')
  expect(() => markDeploymentPending('workflow/a')).toThrow(/未确认/)
  expect(() => assertDeploymentAvailable('workflow/b')).not.toThrow()
  clearConfirmedDeployment('workflow/a')
  expect(() => assertDeploymentAvailable('workflow/a')).not.toThrow()
})

it('explicit reconciliation clears only the requested workflow marker', () => {
  markDeploymentPending('workflow/a')
  markDeploymentPending('workflow/b')
  clearReconciledDeployment('workflow/a', { gitAndDatabaseConfirmed: true })
  expect(localStorage.getItem('clawweb.autoheal.review-blocked.v1.workflow%2Fa')).toBeNull()
  expect(localStorage.getItem('clawweb.autoheal.review-blocked.v1.workflow%2Fb')).toBe('1')
  expect(() => assertDeploymentAvailable('workflow/a')).not.toThrow()
  expect(() => assertDeploymentAvailable('workflow/b')).toThrow(/未确认/)
})

it('does not explicitly reconcile cross-workflow markers', () => {
  markDeploymentPending('workflow/a')
  markDeploymentPending('workflow/b')
  expect(() => clearReconciledDeployment('workflow/other', { gitAndDatabaseConfirmed: true })).not.toThrow()
  expect(localStorage.getItem('clawweb.autoheal.review-blocked.v1.workflow%2Fa')).toBe('1')
  expect(localStorage.getItem('clawweb.autoheal.review-blocked.v1.workflow%2Fb')).toBe('1')
})

it('keeps a failed deployment blocked until manual reconciliation is explicitly confirmed', () => {
  markDeploymentPending('workflow/a')
  expect(() => clearReconciledDeployment('workflow/a', {} as never)).toThrow(/请先确认已人工核对/)
  expect(() => clearReconciledDeployment('workflow/a', { gitAndDatabaseConfirmed: false } as never)).toThrow(/请先确认已人工核对/)
  expect(() => assertDeploymentAvailable('workflow/a')).toThrow(/未确认/)
  clearReconciledDeployment('workflow/a', { gitAndDatabaseConfirmed: true })
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
  expect(() => clearReconciledDeployment('workflow', { gitAndDatabaseConfirmed: true })).toThrow(/无法清除/)
  remove.mockRestore()
  expect(() => assertDeploymentAvailable('workflow')).toThrow(/未确认/)
})

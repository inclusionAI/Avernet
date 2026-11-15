import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
const api = vi.hoisted(() => ({ capabilities: vi.fn() }))
vi.mock('../../api/workflow-ai', () => ({ workflowAiApi: api }))
vi.mock('../../services/workflow-ai-draft', async original => ({ ...await original<typeof import('../../services/workflow-ai-draft')>(), generateWorkflowAiDraft: vi.fn() }))
import WorkflowAiDrawer from '../WorkflowAiDrawer'
import { generateWorkflowAiDraft } from '../../services/workflow-ai-draft'
import { WorkflowAiRecoveryError } from '../../services/workflow-ai-lifecycle'

const props = { open: true, mode: 'modify' as const, spec: { id: 'flow', title: 'Flow', version: '1', nodes: [] }, onClose: vi.fn(), onApply: vi.fn() }
beforeEach(() => { api.capabilities.mockReset(); vi.mocked(generateWorkflowAiDraft).mockReset() })
afterEach(cleanup)

it.each([
  ['AUTO_HEAL_BAAS_NOT_CONFIGURED', '配置缺失或未启用'] as const,
  ['AUTO_HEAL_BOT_UNAVAILABLE', 'BaaS Bot 是否存在、可用且绑定有效'] as const,
])('maps capability reason %s to actionable text without submitting a job', async (reason, guidance) => {
  api.capabilities.mockResolvedValueOnce({ available: false, reason }).mockResolvedValueOnce({ available: true })
  render(<WorkflowAiDrawer {...props} />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '需求' } })
  const status = await screen.findByRole('status')
  expect(status).toHaveTextContent('AI 草稿服务不可用')
  expect(status).toHaveTextContent(reason)
  expect(status).toHaveTextContent(guidance)
  expect(screen.getByRole('button', { name: '生成 YAML 草稿' })).toBeDisabled()
  fireEvent.submit(screen.getByRole('button', { name: '生成 YAML 草稿' }).closest('form')!)
  expect(generateWorkflowAiDraft).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '重试能力检查' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '生成 YAML 草稿' })).toBeEnabled())
  expect(generateWorkflowAiDraft).not.toHaveBeenCalled()
})

it('still explains server autoHeal.botId / BaaS when the reason is blank', async () => {
  api.capabilities.mockResolvedValueOnce({ available: false })
  render(<WorkflowAiDrawer {...props} />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '需求' } })
  const status = await screen.findByRole('status')
  expect(status).toHaveTextContent('AI 草稿服务不可用')
  expect(status).toHaveTextContent('autoHeal.botId')
  expect(status).toHaveTextContent('BaaS')
  expect(status).toHaveTextContent('服务器未返回具体原因')
  expect(screen.getByRole('button', { name: '生成 YAML 草稿' })).toBeDisabled()
  expect(generateWorkflowAiDraft).not.toHaveBeenCalled()
})

it('does not call a capability transport failure proof that server config is missing', async () => {
  api.capabilities.mockRejectedValueOnce(new Error('network down'))
  render(<WorkflowAiDrawer {...props} />)
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('无法确认 AI 草稿服务能力')
  expect(alert).toHaveTextContent('autoHeal.botId')
  expect(alert).toHaveTextContent('BaaS')
  expect(alert).toHaveTextContent('不证明服务器 autoHeal.botId / BaaS 配置缺失')
  expect(alert).toHaveTextContent('network down')
  expect(screen.getByRole('button', { name: '生成 YAML 草稿' })).toBeDisabled()
  expect(generateWorkflowAiDraft).not.toHaveBeenCalled()
})

it('offers recovery of BaaS dispatch uncertainty rather than a new generation action', async () => {
  api.capabilities.mockResolvedValue({ available: true })
  const attempt = { requestId: 'fixed-request', draftId: 'existing-draft' }
  vi.mocked(generateWorkflowAiDraft).mockRejectedValue(new WorkflowAiRecoveryError(
    'BaaS 派发结果不确定：BaaS 可能已接收消息，远端任务可能继续。请恢复查询同一任务或取消；不会再次派发。',
    attempt,
  ))
  render(<WorkflowAiDrawer {...props} />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '需求' } })
  await waitFor(() => expect(screen.getByRole('button', { name: '生成 YAML 草稿' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: '生成 YAML 草稿' }))
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('BaaS 可能已接收消息')
  expect(alert).toHaveTextContent('远端任务可能继续')
  expect(alert).toHaveTextContent('不会再次派发')
  expect(screen.queryByRole('button', { name: '重试生成' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '恢复查询本次请求' }))
  await waitFor(() => expect(generateWorkflowAiDraft).toHaveBeenCalledTimes(2))
  expect(vi.mocked(generateWorkflowAiDraft).mock.calls[1][4]).toBe(attempt)
})

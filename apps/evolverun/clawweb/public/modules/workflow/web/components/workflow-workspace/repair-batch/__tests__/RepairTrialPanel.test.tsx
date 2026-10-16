import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
const api = vi.hoisted(() => ({ trials: vi.fn(), trialOptions: vi.fn(), startTrial: vi.fn() }))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: api }))
import RepairTrialPanel from '../RepairTrialPanel'
const revision = { revision: 1, phase: 'review', stateVersion: 1, draft: { candidateCommit: 'a'.repeat(40) } } as never
const trial = { id: 'trial', requestId: 'r', flowId: 'real-flow', candidateCommit: 'a'.repeat(40), revision: 1,
  status: 'waiting', input: { token: '[REDACTED]' }, target: { label: 'Production Bot' }, createdAtMs: 1, output: '<script>alert(1)</script>' }
beforeEach(() => {
  vi.clearAllMocks()
  api.trials.mockResolvedValue({ configured: true, trials: [] })
  api.trialOptions.mockResolvedValue({ inputSchema: { type: 'object', required: ['query'], properties: { query: { type: 'string' } } },
    inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
})
const panel = (rev = revision) => <MemoryRouter><RepairTrialPanel taskId="task" revision={rev} enabled open onClose={() => {}} onTrials={() => {}} /></MemoryRouter>
it('never selects a Bot or executes until explicit action, sends typed input and deduplicates double click', async () => {
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  const select = await screen.findByRole('combobox', { name: '执行 Bot' })
  expect(select).toHaveValue('')
  expect(api.startTrial).not.toHaveBeenCalled()
  await userEvent.selectOptions(select, 'prod:owner:bot')
  await userEvent.type(screen.getByRole('textbox', { name: 'query' }), 'hello')
  await userEvent.dblClick(screen.getByRole('button', { name: '执行此候选' }))
  expect(api.startTrial).toHaveBeenCalledOnce()
  expect(api.startTrial).toHaveBeenCalledWith('task', expect.objectContaining({ revision: 1, input: { query: 'hello' }, targetId: 'prod:owner:bot' }))
})
it('reloads persistent results, labels old candidates and renders untrusted output as text', async () => {
  api.trials.mockResolvedValue({ configured: true, trials: [trial] })
  render(panel({ ...revision as object, draft: { candidateCommit: 'b'.repeat(40) } } as never))
  expect(await screen.findByText('该结果属于旧候选')).toBeInTheDocument()
  expect(screen.getByText(/等待审批或人工输入/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /运行详情/ })).toHaveAttribute('href', '/runs/real-flow')
  expect(document.querySelector('script')).toBeNull()
  expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument()
})
it('recovers lost response with the same request and never swaps Bot/input', async () => {
  api.startTrial.mockRejectedValueOnce(new Error('connection lost')).mockResolvedValueOnce(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox'), 'prod:owner:bot')
  await userEvent.type(screen.getByRole('textbox', { name: 'query' }), 'first')
  await userEvent.click(screen.getByRole('button', { name: '执行此候选' }))
  await screen.findByRole('button', { name: '核对并重试原请求' })
  expect(screen.getByRole('textbox', { name: 'query' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '核对并重试原请求' }))
  await waitFor(() => expect(api.startTrial).toHaveBeenCalledTimes(2))
  expect(api.startTrial.mock.calls[1]).toEqual(api.startTrial.mock.calls[0])
})

it('shows a useful JSON validation error and allows correction without dispatch', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: null, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox'), 'prod:owner:bot')
  const input = screen.getByRole('textbox', { name: '输入参数 JSON' })
  await userEvent.clear(input); await userEvent.type(input, 'invalid')
  await userEvent.click(screen.getByRole('button', { name: '执行此候选' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('参数 JSON 格式无效')
  expect(api.startTrial).not.toHaveBeenCalled(); expect(input).toBeEnabled()
})

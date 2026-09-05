import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const { applyBatch } = vi.hoisted(() => ({ applyBatch: vi.fn() }))

vi.mock('../../../api/hooks', () => ({
  useEligibleBotsForSuggestion: () => ({
    data: { bots: [{ botId: 'bot-1', botName: '修复 Bot', env: 'pre' }] },
    isLoading: false,
    error: null,
  }),
  useApplySuggestionsBatch: () => ({ mutateAsync: applyBatch }),
}))

import type { EvolveSuggestion, SuggestionApplyTask } from '../../../api/client'
import { ApplySuggestionModal } from '../EvolutionTab'

const failedSuggestion: EvolveSuggestion = {
  id: 'SG-1',
  diagnosisId: 'DG-1',
  weakNode: 'report',
  signature: 'output-contract:report',
  failureMode: 'output-contract',
  kind: 'workflow_patch',
  impactRuns: 1,
  evidenceRuns: ['flow-1'],
  description: '将输出契约改为 object',
  status: 'failed',
}

const previousTask: SuggestionApplyTask = {
  taskId: 'EV-OLD',
  stepId: 'EV-OLD-step-apply',
  suggestionId: 'SG-1',
  status: 'failed',
  summary: null,
  botId: 'bot-1',
  botName: '修复 Bot',
  botEnv: 'pre',
  errorMessage: '部署失败',
  retryable: true,
  proposalDigest: null,
  proposal: null,
  applicationSpec: '上次修复要求',
  appliedAt: null,
  createdAt: 1,
  updatedAt: 2,
}

describe('ApplySuggestionModal', () => {
  it('keeps the actions visible in a viewport-bounded dialog', () => {
    render(
      <ApplySuggestionModal
        suggestions={[failedSuggestion]}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )

    expect(screen.getByRole('dialog')).toHaveClass('max-h-[calc(100vh-2rem)]', 'overflow-hidden')
    expect(screen.getByTestId('apply-suggestion-modal-body')).toHaveClass('overflow-y-auto')
    expect(screen.getByTestId('apply-suggestion-modal-footer')).toHaveClass('shrink-0')
    expect(screen.getByRole('button', { name: /确认应用|重新应用/ })).toBeVisible()
  })

  it('retries a failed application as a new request with editable instructions and the previous Bot', async () => {
    applyBatch.mockResolvedValueOnce({ ok: true, status: 'running', taskId: 'EV-NEW' })
    const onApplied = vi.fn()
    render(
      <ApplySuggestionModal
        suggestions={[failedSuggestion]}
        previousTask={previousTask}
        onClose={vi.fn()}
        onApplied={onApplied}
      />,
    )

    expect(screen.getByLabelText('本次修复要求')).toHaveValue('上次修复要求')
    expect(screen.getByRole('radio', { name: /修复 Bot/ })).toBeChecked()
    await userEvent.clear(screen.getByLabelText('本次修复要求'))
    await userEvent.type(screen.getByLabelText('本次修复要求'), '本次只调整 report 输出契约')
    await userEvent.click(screen.getByRole('button', { name: '重新应用' }))

    expect(applyBatch).toHaveBeenCalledWith({
      suggestionIds: ['SG-1'],
      botId: 'bot-1',
      botEnv: 'pre',
      applicationSpec: '本次只调整 report 输出契约',
    })
    expect(onApplied).toHaveBeenCalledWith(['SG-1'])
  })
})

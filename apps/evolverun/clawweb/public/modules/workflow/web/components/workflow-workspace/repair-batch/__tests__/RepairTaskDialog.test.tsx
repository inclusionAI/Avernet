import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const repairApi = vi.hoisted(() => ({
  task: vi.fn(),
  candidates: vi.fn(),
  retryDispatch: vi.fn(),
  retryGeneration: vi.fn(),
  retryResult: vi.fn(),
  cancel: vi.fn(),
  revise: vi.fn(),
  diff: vi.fn(),
  verification: vi.fn(),
}))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: repairApi }))

import RepairTaskDialog from '../RepairTaskDialog'

function taskDetail(phase: 'drafting' | 'review', summary?: string, itemIds: string[] = []): RepairTaskDetail {
  const revision = {
    workflowId: 'wf-1', taskId: 'FIX-1', revision: 1, phase, stateVersion: 1,
    requestId: 'request-1', requestDigest: 'f'.repeat(64),
    input: {
      schemaVersion: 'workflow-repair/v2', taskId: 'FIX-1', revision: 1,
      baseline: { workflowId: 'wf-1', packId: 'pack', releaseRevision: 1, activeDeployNumber: null,
        specDigest: 'a'.repeat(64), repoId: 'repo', specPath: 'workflow.yaml', packCommit: 'b'.repeat(40), packDigest: 'c'.repeat(40) },
      items: itemIds.map((itemId, index) => ({
        itemId, groupKey: 'group-1', proposalKey: String(index + 1).repeat(64), contentRevision: 1,
        previousItemId: null, proposal: { summary: `修复建议 ${index + 1}` }, instruction: '', sources: [],
      })), excludedSources: { count: 0, digest: '0'.repeat(64) }, instructions: '',
      parentCandidateCommit: null, feedback: '', previousReportRef: null, taskBranch: 'repair/FIX-1',
    },
    draft: summary ? { candidateCommit: 'd'.repeat(40), summary } : null,
    checks: summary ? {} : null, candidateDigest: summary ? '1'.repeat(64) : null,
    checksDigest: summary ? '2'.repeat(64) : null, error: null, createdAtMs: 1, updatedAtMs: 2,
  } as RepairTaskDetail['latestAttempt']
  return {
    workflowId: 'wf-1', taskId: 'FIX-1',
    capabilities: { generation: true, diff: true, publication: false, reason: null },
    execution: phase === 'drafting'
      ? { status: 'running', errorCode: null, jobId: 'job-1', attempt: 1, executionId: 'exec-1', rawStatus: 'running' }
      : { status: 'succeeded', errorCode: null, jobId: 'job-1', attempt: 1, executionId: 'exec-1', rawStatus: 'success' },
    latestAttempt: revision, latestSuccessful: phase === 'review' ? revision : null, revisions: [revision],
  }
}

const candidates = () => ({
  schemaVersion: 'workflow-repair/v2' as const, workflowId: 'wf-1', inputDigest: 'c'.repeat(64), canEdit: true,
  items: [{
    itemId: 'item-3', groupKey: 'group-1', proposalKey: '3'.repeat(64), contentRevision: 1,
    previousItemId: null, proposal: { summary: '新出现的修复建议' }, instruction: '', sources: [],
    workflowId: 'wf-1', episodeKey: 'current', state: 'pending' as const, stateVersion: 0, activeTaskId: null,
    activeRevision: null, disposition: null, updatedAtMs: 3, sourceAvailable: true,
  }], tasks: [], capabilities: { generation: true, diff: true, publication: false as const, reason: null },
  includeHistorical: false, activeLookbackDays: 30, repairSignatureKeys: ['group-1'],
  counts: { pending: 1, processing: 0, awaiting_verification: 0, closed: 0, no_action: 0, all: 1 },
  page: { page: 1, pageSize: 20, total: 1, totalPages: 1 }, limits: { maxItems: 100, maxRequestBytes: 65536 },
})

it('does not reread the same historical scope when the parent history flag changes', async () => {
  const detail = taskDetail('review', '候选', ['item-1'])
  detail.latestAttempt.input.items[0].context = { targetSnapshot: { kind: 'deployment', workflowId: 'wf-1', workflowVersion: 1, deployNumber: 1, configDigest: 'a'.repeat(64) } }
  repairApi.task.mockResolvedValue(detail)
  repairApi.candidates.mockResolvedValue(candidates())
  const props = { workflowId: 'wf-1', taskId: 'FIX-1', canEdit: true, onClose() {}, onChanged() {} }
  const view = render(<RepairTaskDialog {...props} includeHistorical={false} />)
  await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求', exact: true }))
  await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
  view.rerender(<RepairTaskDialog {...props} includeHistorical />)
  await act(async () => {})
  expect(repairApi.candidates).toHaveBeenCalledTimes(1)
  expect(repairApi.candidates).toHaveBeenCalledWith('wf-1', expect.objectContaining({ includeHistorical: true, versionScope: 'all', includeTasks: false }))
})

it('shows one primary action, durable failed deployment status and Git links instead of prominent hashes', async () => {
  const detail = taskDetail('review', '候选已生成')
  detail.capabilities.publication = true
  detail.release = { repositoryWebUrl: 'https://example.com/workflows/packs',
    approval: { id: 'approved', revision: 1, status: 'approved', target: { id: 'prod', environment: 'prod', label: '生产 Bot' } },
    deployments: [{ id: 'failed', approvalId: 'approved', revision: 1, status: 'failed', error: '发布入口不存在' }] } as never
  repairApi.task.mockResolvedValue(detail)
  render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />)
  expect(await screen.findByRole('button', { name: '发布此版本' })).toBeEnabled()
  expect(screen.getAllByLabelText('当前修复步骤与下一步')).toHaveLength(1)
  expect(screen.queryByRole('button', { name: '进入候选确认' })).not.toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: '查看修复版本' })).toHaveDisplayValue('v1 · 部署失败')
  expect(screen.getByRole('link', { name: /查看 Git 提交/ })).toHaveAttribute('href', `https://example.com/workflows/packs/commit/${'d'.repeat(40)}`)
  expect(screen.getByText('d'.repeat(40))).not.toBeVisible()
})

beforeEach(() => { sessionStorage.clear(); Object.values(repairApi).forEach(mock => mock.mockReset()); repairApi.diff.mockResolvedValue({ files: [], truncated: false }) })
afterEach(() => vi.useRealTimers())

it('requests only the displayed candidate page when adjusting scope, not all workflow metadata pages', async () => {
  repairApi.task.mockResolvedValue(taskDetail('review', '候选', ['item-1'])); repairApi.candidates.mockResolvedValue(candidates());
  render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />);
  await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求', exact: true }));
  await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' });
  expect(repairApi.candidates).toHaveBeenCalledWith('wf-1', expect.objectContaining({ page: 1, pageSize: 20, metadataPage: 1 }));
});

it('allows editing frozen choices while suggestions load and preserves edits when the response arrives', async () => {
  repairApi.task.mockResolvedValue(taskDetail('review', '候选', ['item-1', 'item-2']));
  let finish!: (value: ReturnType<typeof candidates>) => void;
  repairApi.candidates.mockImplementation(() => new Promise(resolve => { finish = resolve }));
  render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />);
  await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求', exact: true }));
  const original = screen.getByRole('checkbox', { name: '选择修订项 修复建议 1' });
  expect(original).toBeEnabled();
  await userEvent.click(original);
  await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '保留已有约束');
  expect(screen.getByRole('button', { name: '生成新版本' })).toBeDisabled();
  await act(async () => { finish(candidates()) });
  expect(original).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '选择修订项 新出现的修复建议' })).not.toBeChecked();
  expect(screen.getByRole('textbox', { name: '修改反馈' })).toHaveValue('保留已有约束');
  expect(screen.getByRole('button', { name: '生成新版本' })).toBeEnabled();
  expect(repairApi.revise).not.toHaveBeenCalled();
});

it('shows no execution record for a historical failure when only the latest execution is available', async () => {
  const detail = taskDetail('review', '最新成功候选')
  detail.latestAttempt.revision = 6
  const failed = { ...detail.latestAttempt, revision: 5, phase: 'failed', draft: null, checks: null,
    error: { code: 'AIS_FAILED', message: 'Failed to submit task, Please resubmit!' } } as RepairTaskDetail['latestAttempt']
  detail.revisions = [detail.latestAttempt, failed]
  repairApi.task.mockResolvedValue(detail)
  render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit={false} onClose={() => {}} onChanged={() => {}} />)
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '查看修复版本' }), '5')
  expect(screen.getByText('所选版本 v5：生成失败')).toBeVisible()
  expect(screen.getByText('生成执行记录 · v5')).toBeVisible()
  await userEvent.click(screen.getByText('生成执行记录 · v5'))
  expect(screen.getByText('该版本暂无执行记录。')).toBeVisible()
  expect(screen.queryByText(/生成执行记录 · v6/)).not.toBeInTheDocument()
  expect(screen.getByText(/AIS 任务执行或提交失败/)).toBeVisible()
  expect(screen.queryByText('当前查看的是历史候选，请切回最新可用候选。')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '发布此版本' })).not.toBeInTheDocument()
})

it('makes failure without any successful candidate a recovery state rather than a review target', async () => {
  const detail = taskDetail('drafting')
  detail.latestAttempt.phase = 'failed'; detail.latestAttempt.error = { code: 'AIS_FAILED', message: 'Failed to submit task' }
  repairApi.task.mockResolvedValue(detail)
  render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />)
  expect(await screen.findByText('首次生成失败，尚无候选。')).toBeVisible()
  expect(screen.queryByText('当前查看的是历史候选，请切回最新可用候选。')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '发布此版本' })).not.toBeInTheDocument()
})

it('explains the latest no-change attempt without calling it historical or asking for publication', async () => {
  const detail = taskDetail('review', '原候选')
  const next = { ...detail.latestAttempt, revision: 2, phase: 'no_change', draft: { summary: '已有修改覆盖此建议' } } as RepairTaskDetail['latestAttempt']
  detail.latestAttempt = next; detail.revisions = [next, detail.latestSuccessful!]
  repairApi.task.mockResolvedValue(detail)
  render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit={false} onClose={() => {}} onChanged={() => {}} />)
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '查看修复版本' }), '2')
  expect(screen.getByText('所选版本 v2：无需变更')).toBeVisible()
  expect(screen.getByRole('heading', { name: '本轮无需重新发布' })).toBeVisible()
  expect(screen.queryByText('当前查看的是历史候选，请切回最新可用候选。')).not.toBeInTheDocument()
})

it('lets a read-only user revisit a published scope and candidate without editing or publishing', async () => {
  const detail = taskDetail('review', '已审阅的候选', ['item-1'])
  detail.capabilities.publication = true
  detail.release = { deployments: [{ id: 'deploy-1', revision: 1, status: 'succeeded', baselineDeployNumber: 157, receipt: { deployNumber: 160 } }] } as never
  repairApi.task.mockResolvedValue(detail)
  const scroll = vi.fn()
  Element.prototype.scrollIntoView = scroll
  repairApi.verification.mockResolvedValue({ evidence: null, conclusions: [] })
  render(<QueryClientProvider client={new QueryClient()}><RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit={false} onClose={() => {}} onChanged={() => {}} /></QueryClientProvider>)
  await screen.findByRole('region', { name: '当前候选' })
  expect(await screen.findByRole('link', { name: '查看版本对比' })).toHaveAttribute('href', '/workflows/workspace?workflowId=wf-1&tab=overview&view=version-comparison&baseline=157&current=160')
  await userEvent.click(screen.getByRole('tab', { name: '优化目标' }))
  expect(screen.getByRole('heading', { name: '本版优化目标' })).toBeVisible()
  expect(screen.queryByRole('button', { name: '生成新版本' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: '改动与发布' }))
  expect(screen.getByRole('heading', { name: '本版改动' })).toBeVisible()
  expect(scroll).not.toHaveBeenCalled()
  expect(repairApi.candidates).not.toHaveBeenCalled()
  expect(repairApi.revise).not.toHaveBeenCalled()
  expect(repairApi.retryDispatch).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: '发布此版本' })).not.toBeInTheDocument()
})

describe('RepairTaskDialog', () => {
  it('refreshes a read-only failed task without invoking result recovery mutations', async () => {
    const failed = taskDetail('drafting', undefined, ['item-1'])
    failed.latestAttempt.phase = 'failed'; failed.latestAttempt.error = { code: 'GIT_RESULT_UNAVAILABLE' }
    repairApi.task.mockResolvedValue(failed); repairApi.candidates.mockResolvedValue(candidates())
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit={false} onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('tab', { name: '优化目标' }))
    expect(screen.queryByRole('button', { name: '核对原结果', exact: true })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '核对任务状态', exact: true }))
    expect(repairApi.task).toHaveBeenCalledTimes(2)
    expect(repairApi.retryResult).not.toHaveBeenCalled()
    expect(repairApi.revise).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '生成新版本' })).not.toBeInTheDocument()
  })

  it('resumes polling after a lost revision response recovers a drafting attempt', async () => {
    let current = taskDetail('review', '旧候选', ['item-1'])
    repairApi.task.mockImplementation(async () => current)
    repairApi.candidates.mockResolvedValue(candidates())
    repairApi.revise.mockImplementation(async () => {
      const next = { ...taskDetail('drafting', undefined, ['item-1']).latestAttempt, revision: 2 }
      current = { ...current, latestAttempt: next, revisions: [...current.revisions, next] }
      throw new Error('response lost')
    })
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求', exact: true }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }))
    const recovery = await screen.findByRole('button', { name: '核对任务状态', exact: true })
    vi.useFakeTimers()
    await act(async () => { recovery.click() })
    expect(screen.getByText(/最近一次生成 v2：生成中/)).toBeVisible()
    const completed = { ...taskDetail('review', '新候选').latestAttempt, revision: 2 }
    current = { ...current, latestAttempt: completed, latestSuccessful: completed, revisions: [current.revisions[0], completed] }
    await act(async () => { await vi.runOnlyPendingTimersAsync() })
    expect(screen.getByText('新候选')).toBeVisible()
    expect(repairApi.revise).toHaveBeenCalledTimes(1)
  })

  it('clears stale revision selections after rereading sources while retaining text', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '候选', ['item-1']))
    repairApi.candidates.mockResolvedValue(candidates())
    repairApi.revise.mockRejectedValueOnce({ status: 409, body: JSON.stringify({ code: 'SOURCE_CHANGED' }) }).mockResolvedValue({})
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求', exact: true }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    await userEvent.type(screen.getByRole('textbox', { name: '修复要求' }), '保留原分支')
    await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '补充边界')
    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }))
    repairApi.candidates.mockResolvedValue({ ...candidates(), inputDigest: 'f'.repeat(64) })
    await userEvent.click(await screen.findByRole('button', { name: '重新读取建议', exact: true }))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 1' })).not.toBeChecked())
    expect(screen.getByRole('button', { name: '生成新版本' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: '修复要求' })).toHaveValue('保留原分支')
    expect(screen.getByRole('textbox', { name: '修改反馈' })).toHaveValue('补充边界')
    await userEvent.click(screen.getByRole('checkbox', { name: '选择修订项 新出现的修复建议' }))
    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }))
    expect(repairApi.revise.mock.calls[1][1]).toMatchObject({ itemIds: ['item-3'], inputDigest: 'f'.repeat(64) })
  })

  it('regenerates a first failure without requiring feedback or reading current candidates', async () => {
    const failed = taskDetail('drafting', undefined, ['item-1']);
    failed.latestAttempt.phase = 'failed'; failed.latestAttempt.error = { code: 'AIS_FAILED' };
    failed.execution!.status = 'failed';
    repairApi.task.mockResolvedValue(failed); repairApi.retryGeneration.mockResolvedValue({});
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '重试生成' }));
    expect(repairApi.retryGeneration).toHaveBeenCalledWith('FIX-1', expect.objectContaining({ expectedRevision: 1, requestId: expect.any(String) }));
    expect(repairApi.candidates).not.toHaveBeenCalled(); expect(repairApi.cancel).not.toHaveBeenCalled();
  });
  it('keeps the last candidate visible after failure and separates its review from retry', async () => {
    const detail = taskDetail('review', '保留的候选', ['item-1']);
    detail.latestAttempt = { ...detail.latestAttempt, revision: 2, phase: 'failed', draft: null, checks: null, error: { code: 'AIS_FAILED' } };
    detail.revisions.push(detail.latestAttempt); repairApi.task.mockResolvedValue(detail);
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />);
    expect(await screen.findByText('保留的候选')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试生成' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '返回上一成功候选审核' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '调整建议与要求' })).toBeEnabled();
  });
  it('polls an active generation until the task becomes reviewable', async () => {
    vi.useFakeTimers()
    repairApi.task.mockResolvedValueOnce(taskDetail('drafting')).mockResolvedValueOnce(taskDetail('review', '候选已生成'))

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await act(async () => {})
    expect(screen.getByRole('region', { name: '当前候选' })).toHaveTextContent('生成中')

    await act(async () => { await vi.runOnlyPendingTimersAsync() })
    expect(screen.getByText('候选已生成')).toBeInTheDocument()
    expect(repairApi.task).toHaveBeenCalledTimes(2)
  })

  it('switches to a newer successful feedback revision after polling', async () => {
    vi.useFakeTimers()
    const first = taskDetail('review', '旧候选')
    const draftingRevision = {
      ...taskDetail('drafting').latestAttempt,
      revision: 2,
      input: { ...taskDetail('drafting').latestAttempt.input, revision: 2, feedback: '请调整' },
    }
    const drafting = { ...first, latestAttempt: draftingRevision, revisions: [first.latestAttempt, draftingRevision],
      execution: { ...taskDetail('drafting').execution!, jobId: 'job-2', executionId: 'exec-2' } }
    const successfulRevision = {
      ...draftingRevision,
      phase: 'review' as const,
      draft: { candidateCommit: 'e'.repeat(40), summary: '新候选' },
      checks: {}, candidateDigest: '3'.repeat(64), checksDigest: '4'.repeat(64),
    }
    const completed = { ...drafting, latestAttempt: successfulRevision, latestSuccessful: successfulRevision,
      revisions: [first.latestAttempt, successfulRevision], execution: { ...first.execution!, jobId: 'job-2', executionId: 'exec-2' } }
    repairApi.task.mockResolvedValueOnce(drafting).mockResolvedValueOnce(completed)

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await act(async () => {})
    expect(screen.getByText('旧候选')).toBeInTheDocument()

    await act(async () => { await vi.runOnlyPendingTimersAsync() })
    expect(screen.getByText('新候选')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '查看修复版本' })).toHaveValue('2')
  })

  it('keeps polling an active task after a transient read failure', async () => {
    vi.useFakeTimers()
    repairApi.task.mockResolvedValueOnce(taskDetail('drafting'))
      .mockRejectedValueOnce(new Error('temporary unavailable'))
      .mockResolvedValueOnce(taskDetail('review', '恢复后候选已生成'))

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await act(async () => {})
    await act(async () => { await vi.runOnlyPendingTimersAsync() })
    await act(async () => { await vi.runOnlyPendingTimersAsync() })

    expect(screen.getByText('恢复后候选已生成')).toBeInTheDocument()
    expect(repairApi.task).toHaveBeenCalledTimes(3)
  })

  it('retries when the initial task read fails', async () => {
    vi.useFakeTimers()
    repairApi.task.mockRejectedValueOnce(new Error('temporary unavailable'))
      .mockResolvedValueOnce(taskDetail('drafting'))
      .mockResolvedValueOnce(taskDetail('review', '首次失败后已恢复'))

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await act(async () => {})
    await act(async () => { await vi.runOnlyPendingTimersAsync() })
    await act(async () => { await vi.runOnlyPendingTimersAsync() })

    expect(screen.getByText('首次失败后已恢复')).toBeInTheDocument()
    expect(repairApi.task).toHaveBeenCalledTimes(3)
  })

  it('lets feedback revisions reconfirm which frozen items remain selected', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '候选已生成', ['item-1', 'item-2']))
    repairApi.candidates.mockResolvedValue(candidates())
    repairApi.revise.mockResolvedValueOnce({})

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求' }))
    expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 1' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' })).toBeChecked()
    const newItem = await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    expect(newItem).not.toBeChecked()

    await userEvent.click(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' }))
    await userEvent.click(newItem)
    await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '只保留第一项')
    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }))

    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledWith('FIX-1', expect.objectContaining({ itemIds: ['item-1', 'item-3'] })))
  })

  it('reuses a request ID only while the revision payload is unchanged', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '候选已生成', ['item-1']))
    repairApi.candidates.mockResolvedValue(candidates())
    repairApi.revise.mockRejectedValueOnce(new Error('response lost'))
      .mockRejectedValueOnce(new Error('response lost again'))
      .mockResolvedValueOnce({})

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求' }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    let feedback = screen.getByRole('textbox', { name: '修改反馈' })
    await userEvent.type(feedback, '第一次反馈')

    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }))
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledTimes(1))
    const firstId = repairApi.revise.mock.calls[0][1].requestId
    await userEvent.click(screen.getByRole('button', { name: '核对任务状态', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: '调整建议与要求', exact: true }))
    await waitFor(() => expect(screen.getByRole('button', { name: '生成新版本' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }))
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledTimes(2))
    expect(repairApi.revise.mock.calls[1][1].requestId).toBe(firstId)

    await userEvent.click(screen.getByRole('button', { name: '核对任务状态', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: '调整建议与要求', exact: true }))
    feedback = screen.getByRole('textbox', { name: '修改反馈' })
    await userEvent.type(feedback, '，调整范围')
    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }))
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledTimes(3))
    expect(repairApi.revise.mock.calls[2][1].requestId).not.toBe(firstId)
  })

  it('rejects an oversized feedback revision before posting it', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '候选已生成', ['item-1']))
    repairApi.candidates.mockResolvedValue({ ...candidates(), limits: { maxItems: 100, maxRequestBytes: 300 } })

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求' }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '修'.repeat(100))
    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }))

    expect(screen.getByRole('alert')).toHaveTextContent('请求内容过大，请缩短说明或减少选择。')
    expect(repairApi.revise).not.toHaveBeenCalled()
  })
  it('generates after a first failure with both optional text fields empty', async () => {
    const failed = taskDetail('drafting', undefined, ['item-1']);
    failed.latestAttempt.phase = 'failed'; failed.latestAttempt.error = { code: 'AIS_FAILED' };
    repairApi.task.mockResolvedValue(failed); repairApi.candidates.mockResolvedValue(candidates());
    repairApi.revise.mockResolvedValue({});
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求' }));
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' });
    expect(screen.getByRole('button', { name: '生成新版本' })).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }));
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledWith('FIX-1', expect.objectContaining({ instructions: '', feedback: '', parentCandidateCommit: null, itemIds: ['item-1'] })));
  });

  it('navigates between scope and review without losing edits or submitting', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '候选已生成', ['item-1', 'item-2']));
    repairApi.candidates.mockResolvedValue(candidates());
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求', exact: true }));
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' });
    await userEvent.type(screen.getByRole('textbox', { name: '修复要求' }), '保留原分支');
    await userEvent.click(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' }));
    await userEvent.click(screen.getByRole('button', { name: /返回审核 Pack/ }));
    expect(screen.getByText('候选已生成')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: '调整建议与要求', exact: true }));
    expect(screen.getByRole('textbox', { name: '修复要求' })).toHaveValue('保留原分支');
    expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' })).not.toBeChecked();
    expect(screen.getByRole('button', { name: /返回审核 Pack/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '自动验证', exact: true })).not.toBeInTheDocument();
    expect(repairApi.revise).not.toHaveBeenCalled();
  });

  it('offers a result-only recovery after a conflict and preserves the unsubmitted changes', async () => {
    let current = taskDetail('drafting', undefined, ['item-1']);
    current.latestAttempt.phase = 'failed'; current.latestAttempt.error = { code: 'AIS_FAILED' };
    repairApi.task.mockImplementation(async () => current);
    repairApi.candidates.mockResolvedValue(candidates());
    repairApi.revise.mockRejectedValue(Object.assign(new Error('API 409: private upstream body'), {
      status: 409, body: JSON.stringify({ code: 'STATE_CONFLICT', recoveryAction: 'retry-result', reasonCode: 'RESULT_READ_UNKNOWN' }),
    }));
    repairApi.retryResult.mockImplementation(async () => { current = taskDetail('review', '恢复的原候选', ['item-1']); return { ok: true }; });
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '调整建议与要求' }));
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' });
    await userEvent.type(screen.getByRole('textbox', { name: '修复要求' }), '我的调整');
    await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '修改说明');
    await userEvent.click(screen.getByRole('button', { name: '生成新版本' }));
    const recover = await screen.findByRole('button', { name: '核对原结果', exact: true });
    expect(screen.queryByText(/private upstream/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成新版本' })).toBeDisabled();
    await userEvent.click(recover);
    expect(await screen.findByText('恢复的原候选')).toBeVisible();
    expect(repairApi.retryResult).toHaveBeenCalledWith('FIX-1', 1);
    expect(repairApi.retryGeneration).not.toHaveBeenCalled();
    expect(repairApi.revise).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole('button', { name: '调整建议与要求', exact: true }));
    expect(screen.getByRole('textbox', { name: '修复要求' })).toHaveValue('我的调整');
  });

  it('explains read-only generation while allowing inspection of the frozen scope', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '只读候选', ['item-1']));
    repairApi.candidates.mockResolvedValue(candidates());
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit={false} onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('tab', { name: '优化目标' }));
    expect(screen.getByRole('heading', { name: '本版优化目标' })).toBeVisible();
    expect(screen.getByText('当前只有查看权限。')).toBeVisible();
    expect(screen.queryByRole('textbox', { name: '修复要求' })).not.toBeInTheDocument();
    expect(repairApi.candidates).not.toHaveBeenCalled();
  });

  it('blocks generation on the scope page when the persisted result still needs rereading', async () => {
    const failed = taskDetail('drafting', undefined, ['item-1']);
    failed.latestAttempt.phase = 'failed'; failed.latestAttempt.error = { code: 'GIT_RESULT_UNAVAILABLE' };
    repairApi.task.mockResolvedValue(failed); repairApi.candidates.mockResolvedValue(candidates());
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('tab', { name: '优化目标' }));
    expect(screen.getByRole('button', { name: '调整建议与要求' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '核对原结果', exact: true })).toBeEnabled();
    expect(repairApi.candidates).not.toHaveBeenCalled();
  });

})

it('pages older candidate history while preserving the full current attempt and round scope', async () => {
  const user = userEvent.setup();
  const first = { ...taskDetail('review', '最新候选', ['last-batch-item']), scopeItemCount: 225,
    automaticProgress: { completedBatches: 3, totalBatches: 3 }, revisionPage: { page: 1, pageSize: 20, total: 25, totalPages: 2 } };
  const old = { ...first.latestAttempt, revision: 0, draft: null };
  const second = { ...first, revisions: [old], revisionPage: { ...first.revisionPage, page: 2 } };
  repairApi.task.mockResolvedValueOnce(first).mockResolvedValueOnce(second);
  repairApi.diff.mockResolvedValue({ files: [], truncated: false });
  render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={vi.fn()} onChanged={vi.fn()} />);
  await user.click(await screen.findByRole('tab', { name: '优化目标' }));
  expect(screen.getByText(/本轮已纳入 225 项/)).toBeVisible();
  await user.click(screen.getByText('历史生成记录', { selector: 'summary' }));
  await user.click(screen.getByRole('button', { name: '较早记录' }));
  await waitFor(() => expect(repairApi.task).toHaveBeenLastCalledWith('FIX-1', 2));
  expect(await screen.findByText('历史记录第 2/2 页 · 共 25 次')).toBeInTheDocument();
});

it('loads all original automatic batches for feedback and appends a revision to the same task',async()=>{
 const detail=taskDetail('review','完整候选',['item-3']);detail.latestAttempt.revision=3;detail.latestAttempt.phase='no_change';detail.latestAttempt.input.automation={roundId:'daily'} as never;
 detail.automaticProgress={completedBatches:3,totalBatches:3,changedItems:2,publishable:true};detail.scopeItemCount=3;
 detail.revisionPage={page:1,pageSize:1,total:3,totalPages:2};
 const old1=taskDetail('review','batch1',['item-1']).latestAttempt;
 const old2={...taskDetail('review','batch2',['item-2']).latestAttempt,revision:2};
 repairApi.task.mockImplementation(async(_id,page)=>page===2?{...detail,revisions:[old2,old1]}:detail);
 repairApi.candidates.mockResolvedValue({...candidates(),items:[]});repairApi.revise.mockResolvedValue({taskId:'FIX-1',revision:4});
 render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={()=>{}} onChanged={()=>{}}/>);
 await userEvent.click(await screen.findByRole('button',{name:'调整建议与要求',exact:true}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'生成新版本'})).toBeEnabled());
 expect(screen.getAllByRole('checkbox')).toHaveLength(3);
 await userEvent.type(screen.getByRole('textbox',{name:'修改反馈'}),'进一步优化输出格式');
 await userEvent.click(screen.getByRole('button',{name:'生成新版本'}));
 await waitFor(()=>expect(repairApi.revise).toHaveBeenCalled());
 expect(repairApi.revise.mock.calls[0][0]).toBe('FIX-1');
 expect(repairApi.revise.mock.calls[0][1]).toMatchObject({expectedAttemptRevision:3,parentCandidateCommit:'d'.repeat(40),feedback:'进一步优化输出格式'});
 expect(new Set(repairApi.revise.mock.calls[0][1].itemIds)).toEqual(new Set(['item-1','item-2','item-3']));
 expect(repairApi.revise.mock.calls[0][1].automation).toBeUndefined();expect(repairApi.cancel).not.toHaveBeenCalled();
});

import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'

const repairApi = vi.hoisted(() => ({
  task: vi.fn(),
  candidates: vi.fn(),
  retryDispatch: vi.fn(),
  retryGeneration: vi.fn(),
  retryResult: vi.fn(),
  cancel: vi.fn(),
  revise: vi.fn(),
  diff: vi.fn(),
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

beforeEach(() => { Object.values(repairApi).forEach(mock => mock.mockReset()); repairApi.diff.mockResolvedValue({ files: [], truncated: false }) })
afterEach(() => vi.useRealTimers())

describe('RepairTaskDialog', () => {
  it('refreshes a read-only failed task without invoking result recovery mutations', async () => {
    const failed = taskDetail('drafting', undefined, ['item-1'])
    failed.latestAttempt.phase = 'failed'; failed.latestAttempt.error = { code: 'GIT_RESULT_UNAVAILABLE' }
    repairApi.task.mockResolvedValue(failed); repairApi.candidates.mockResolvedValue(candidates())
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit={false} onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: '选择建议', exact: true }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    expect(screen.queryByRole('button', { name: '核对原结果', exact: true })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '核对任务状态', exact: true }))
    expect(repairApi.task).toHaveBeenCalledTimes(2)
    expect(repairApi.retryResult).not.toHaveBeenCalled()
    expect(repairApi.revise).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '确认生成下一版' })).toBeDisabled()
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
    await userEvent.click(await screen.findByRole('button', { name: '选择建议', exact: true }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))
    const recovery = await screen.findByRole('button', { name: '核对任务状态', exact: true })
    vi.useFakeTimers()
    await act(async () => { recovery.click() })
    expect(screen.getByText(/最近尝试：v2 · 生成中/)).toBeVisible()
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
    await userEvent.click(await screen.findByRole('button', { name: '选择建议', exact: true }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    await userEvent.type(screen.getByRole('textbox', { name: '修复要求' }), '保留原分支')
    await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '补充边界')
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))
    repairApi.candidates.mockResolvedValue({ ...candidates(), inputDigest: 'f'.repeat(64) })
    await userEvent.click(await screen.findByRole('button', { name: '重新读取建议', exact: true }))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 1' })).not.toBeChecked())
    expect(screen.getByRole('button', { name: '确认生成下一版' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: '修复要求' })).toHaveValue('保留原分支')
    expect(screen.getByRole('textbox', { name: '修改反馈' })).toHaveValue('补充边界')
    await userEvent.click(screen.getByRole('checkbox', { name: '选择修订项 新出现的修复建议' }))
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))
    expect(repairApi.revise.mock.calls[1][1]).toMatchObject({ itemIds: ['item-3'], inputDigest: 'f'.repeat(64) })
  })

  it('regenerates a first failure without requiring feedback or reading current candidates', async () => {
    const failed = taskDetail('drafting', undefined, ['item-1']);
    failed.latestAttempt.phase = 'failed'; failed.latestAttempt.error = { code: 'AIS_FAILED' };
    failed.execution!.status = 'failed';
    repairApi.task.mockResolvedValue(failed); repairApi.retryGeneration.mockResolvedValue({});
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '重新生成 Pack' }));
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
    expect(screen.getByRole('button', { name: '重新生成 Pack' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '返回上一成功候选审核' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '调整范围与要求' })).toBeEnabled();
  });
  it('polls an active generation until the task becomes reviewable', async () => {
    vi.useFakeTimers()
    repairApi.task.mockResolvedValueOnce(taskDetail('drafting')).mockResolvedValueOnce(taskDetail('review', '候选已生成'))

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await act(async () => {})
    expect(screen.getByText(/最近尝试：v1 · 生成中/)).toBeInTheDocument()

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
    await userEvent.click(await screen.findByRole('button', { name: '反馈并生成下一版' }))
    expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 1' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' })).toBeChecked()
    const newItem = await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    expect(newItem).not.toBeChecked()

    await userEvent.click(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' }))
    await userEvent.click(newItem)
    await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '只保留第一项')
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))

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
    await userEvent.click(await screen.findByRole('button', { name: '反馈并生成下一版' }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    let feedback = screen.getByRole('textbox', { name: '修改反馈' })
    await userEvent.type(feedback, '第一次反馈')

    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledTimes(1))
    const firstId = repairApi.revise.mock.calls[0][1].requestId
    await userEvent.click(screen.getByRole('button', { name: '核对任务状态', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: '选择建议', exact: true }))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认生成下一版' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledTimes(2))
    expect(repairApi.revise.mock.calls[1][1].requestId).toBe(firstId)

    await userEvent.click(screen.getByRole('button', { name: '核对任务状态', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: '选择建议', exact: true }))
    feedback = screen.getByRole('textbox', { name: '修改反馈' })
    await userEvent.type(feedback, '，调整范围')
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledTimes(3))
    expect(repairApi.revise.mock.calls[2][1].requestId).not.toBe(firstId)
  })

  it('rejects an oversized feedback revision before posting it', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '候选已生成', ['item-1']))
    repairApi.candidates.mockResolvedValue({ ...candidates(), limits: { maxItems: 100, maxRequestBytes: 300 } })

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: '反馈并生成下一版' }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '修'.repeat(100))
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))

    expect(screen.getByRole('alert')).toHaveTextContent('请求内容过大，请缩短说明或减少选择。')
    expect(repairApi.revise).not.toHaveBeenCalled()
  })
  it('generates after a first failure with both optional text fields empty', async () => {
    const failed = taskDetail('drafting', undefined, ['item-1']);
    failed.latestAttempt.phase = 'failed'; failed.latestAttempt.error = { code: 'AIS_FAILED' };
    repairApi.task.mockResolvedValue(failed); repairApi.candidates.mockResolvedValue(candidates());
    repairApi.revise.mockResolvedValue({});
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '调整范围与要求' }));
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' });
    expect(screen.getByRole('button', { name: '确认生成下一版' })).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }));
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledWith('FIX-1', expect.objectContaining({ instructions: '', feedback: '', parentCandidateCommit: null, itemIds: ['item-1'] })));
  });

  it('navigates between scope and review without losing edits or submitting', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '候选已生成', ['item-1', 'item-2']));
    repairApi.candidates.mockResolvedValue(candidates());
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '选择建议', exact: true }));
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' });
    await userEvent.type(screen.getByRole('textbox', { name: '修复要求' }), '保留原分支');
    await userEvent.click(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' }));
    await userEvent.click(screen.getByRole('button', { name: '审核 Pack', exact: true }));
    expect(screen.getByText('候选已生成')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: '选择建议', exact: true }));
    expect(screen.getByRole('textbox', { name: '修复要求' })).toHaveValue('保留原分支');
    expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' })).not.toBeChecked();
    expect(screen.getByRole('button', { name: '发布与灰度', exact: true })).toBeDisabled();
    expect(screen.getByRole('button', { name: '效果与复发', exact: true })).toBeDisabled();
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
    await userEvent.click(await screen.findByRole('button', { name: '调整范围与要求' }));
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' });
    await userEvent.type(screen.getByRole('textbox', { name: '修复要求' }), '我的调整');
    await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '修改说明');
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }));
    const recover = await screen.findByRole('button', { name: '核对原结果', exact: true });
    expect(screen.queryByText(/private upstream/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认生成下一版' })).toBeDisabled();
    await userEvent.click(recover);
    expect(await screen.findByText('恢复的原候选')).toBeVisible();
    expect(repairApi.retryResult).toHaveBeenCalledWith('FIX-1', 1);
    expect(repairApi.retryGeneration).not.toHaveBeenCalled();
    expect(repairApi.revise).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole('button', { name: '选择建议', exact: true }));
    expect(screen.getByRole('textbox', { name: '修复要求' })).toHaveValue('我的调整');
  });

  it('explains read-only generation while allowing inspection of the frozen scope', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '只读候选', ['item-1']));
    repairApi.candidates.mockResolvedValue(candidates());
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit={false} onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '选择建议', exact: true }));
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' });
    expect(screen.getByRole('button', { name: '确认生成下一版' })).toBeDisabled();
    expect(screen.getByText('当前只有查看权限，不能生成候选。')).toBeVisible();
    expect(screen.getByRole('textbox', { name: '修复要求' })).toBeDisabled();
  });

  it('blocks generation on the scope page when the persisted result still needs rereading', async () => {
    const failed = taskDetail('drafting', undefined, ['item-1']);
    failed.latestAttempt.phase = 'failed'; failed.latestAttempt.error = { code: 'GIT_RESULT_UNAVAILABLE' };
    repairApi.task.mockResolvedValue(failed); repairApi.candidates.mockResolvedValue(candidates());
    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false} canEdit onClose={() => {}} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: '选择建议', exact: true }));
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' });
    expect(screen.getByRole('button', { name: '确认生成下一版' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '核对原结果', exact: true })).toBeEnabled();
  });

})

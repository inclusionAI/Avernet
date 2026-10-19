import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { render, repairApi, repairItem, repairPage, lifecycle, mutate, applyBatch, eligibleBots, applyTasks, runAnalysis, openIssueRepairs, closeIssue } from './evolution-issue-fixture'
import EvolutionTab from '../EvolutionTab'

describe('issue and optimization flow', () => {
  it('keeps the issue list usable while the batched suggestion preview is pending', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    expect(screen.queryByRole('region', { name: '修复收件箱' })).not.toBeInTheDocument()
    expect(screen.queryByText('诊断证据与历史应用')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '问题详情' }).length).toBeGreaterThan(0)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    expect(repairApi.candidates).toHaveBeenCalledTimes(1)
  })
  it('stores and restores repair controls per workflow', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    const first = render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs(false)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '建议状态' }), 'pending')
    await userEvent.click(await screen.findByRole('checkbox', { name: '包含历史未复现' }))
    await waitFor(() => expect(JSON.parse(sessionStorage.getItem('workflow-repair:wf-1')!)).toMatchObject({
      repairOpen: true, includeHistorical: true,
    }))
    first.unmount()
    repairApi.candidates.mockClear()

    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    expect(screen.queryByRole('button', { name: '进入修复处理' })).not.toBeInTheDocument()
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalledWith('wf-1', expect.objectContaining({
      state: 'all', page: 1, pageSize: 1, includeHistorical: true,
    })))
  })
  it('opens legacy evidence directly for a workflow issue deep link', () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" issueSignature="timeout:fetch-data" section="diagnosis" /></MemoryRouter>)
    expect(screen.getByRole('dialog', { name: '问题详情' })).toBeInTheDocument()
  })
  it.each(['pending', 'applying', 'applied_unverified'])('preserves suggestion-only controls for %s', async (status) => {
    lifecycle.hideGroups = true
    lifecycle.status = status
    repairApi.candidates.mockResolvedValue({ ...repairPage(), capabilities: { generation: false, diff: false, publication: false, reason: 'unavailable' } })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    const row = screen.getByText('将超时阈值调整为 90 秒').closest('article')!
    expect(row).not.toBeNull()
    expect(screen.queryByText(/当前工作流暂无已记录异常/)).not.toBeInTheDocument()
    if (status === 'pending') {
      expect(await within(row).findByRole('button', { name: '应用建议' })).toBeInTheDocument()
    } else if (status === 'applied_unverified') {
      expect(within(row).getByRole('button', { name: '确认有效' })).toBeInTheDocument()
      expect(within(row).getByRole('button', { name: '未达预期' })).toBeInTheDocument()
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
      await userEvent.click(within(row).getByRole('button', { name: '确认有效' }))
      expect(mutate).toHaveBeenLastCalledWith(expect.objectContaining({ suggestionId: 's-1', action: 'verified' }), expect.anything())
      confirm.mockRestore()
    } else {
      expect(within(row).getByText('应用中')).toBeInTheDocument()
      expect(within(row).queryByRole('button', { name: '应用建议' })).not.toBeInTheDocument()
    }
  })
  it('preserves legacy apply when Pack generation is unavailable', async () => {
    repairApi.candidates.mockResolvedValueOnce({
      ...repairPage(),
      capabilities: { generation: false, diff: false, publication: false, reason: 'AIS unavailable' },
    })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')!
    await userEvent.click(within(issue).getByRole('button', { name: '问题详情' }))
    await userEvent.click(screen.getByRole('button', { name: '证据与历史' }))
    await userEvent.click(screen.getByText('历史建议与任务（独立于本次修复）'))
    await userEvent.click(await screen.findByRole('button', { name: '应用建议' }))
    expect(screen.getByRole('dialog', { name: '选择 Bot 自动应用建议' })).toBeInTheDocument()
  })
  it('does not restore legacy apply for a Pack item on another server page', async () => {
    lifecycle.hideGroups = true
    repairApi.candidates.mockResolvedValueOnce(repairPage({ items: [], total: 21, repairSignatures: ['timeout:fetch-data'] }))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    const followup = await screen.findByRole('region', { name: '已有建议跟进' })
    const offPageSuggestion = within(followup).getByText('将超时阈值调整为 90 秒').closest('article')!
    expect(offPageSuggestion).not.toBeNull()
    expect(within(offPageSuggestion).queryByRole('button', { name: '应用建议' })).not.toBeInTheDocument()
  })
  it('keeps suggestion-only controls read-only without workflow edit permission', () => {
    lifecycle.hideGroups = true
    lifecycle.canEdit = false
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    expect(screen.getByText('将超时阈值调整为 90 秒')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '应用建议' })).not.toBeInTheDocument()
  })
  it('keeps the list compact and opens problem actions in a detail drawer', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const actionableIssue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(actionableIssue).not.toBeNull()
    expect(actionableIssue).toHaveAttribute('data-layout', 'compact-issue-row')
    expect(within(actionableIssue!).getByText('节点')).toBeInTheDocument()
    expect(within(actionableIssue!).getByText('问题类型')).toBeInTheDocument()
    expect(within(actionableIssue!).getByText('执行超时')).toBeInTheDocument()
    expect(within(actionableIssue!).queryByText('将超时阈值调整为 90 秒')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '已有建议跟进' })).not.toBeInTheDocument()
    expect(within(actionableIssue!).queryByRole('button', { name: '采纳' })).not.toBeInTheDocument()

    await userEvent.click(within(actionableIssue!).getByRole('button', { name: '问题详情' }))
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByRole('heading', { name: '问题原因' })).toBeInTheDocument()
    expect(within(drawer).queryByText('所选分析详情')).not.toBeInTheDocument()
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
    expect(within(drawer).queryByText('本次修复范围')).not.toBeInTheDocument()
    expect(within(drawer).getByText('已有建议')).toBeInTheDocument()
    expect(within(drawer).getByText('相关分析记录')).toBeInTheDocument()
    expect(within(drawer).getByText('所选分析详情')).toBeInTheDocument()
    expect(within(drawer).getByText('判断依据')).toBeInTheDocument()
    expect(within(drawer).queryByText('完整问题')).not.toBeInTheDocument()
    expect(within(drawer).queryByText('完整建议')).not.toBeInTheDocument()
    expect(within(drawer).queryByText('本次分析')).not.toBeInTheDocument()
    await userEvent.click(within(drawer).getByText('历史建议与任务（独立于本次修复）'))
    expect(within(drawer).queryByRole('button', { name: '应用建议' })).not.toBeInTheDocument()
    await closeIssue()

    const observingIssue = screen.getByText('render-report', { selector: 'span' }).closest('article')
    expect(observingIssue).not.toBeNull()
    expect(within(observingIssue!).getByRole('button', { name: '问题详情' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看建议' })).not.toBeInTheDocument()
  })

  it('includes suggestion impact flows in the related run count and detail links', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(issue).not.toBeNull()
    expect(within(issue!).queryByText(/统计暂不可用|统计详情|分析覆盖/)).not.toBeInTheDocument()

    await userEvent.click(within(issue!).getByRole('button', { name: '问题详情' }))
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
    expect(within(drawer).getByRole('link', { name: /run-1/ })).toHaveAttribute(
      'href',
      '/runs/run-1?from=workspace&workspaceView=diagnosis&issueSignature=timeout%3Afetch-data&analysisId=AN-1',
    )
    expect(within(drawer).getByRole('link', { name: /run-4/ })).toBeInTheDocument()
  })

  it('opens the exact analysis instance from a contextual deep link', () => {
    render(
      <MemoryRouter>
        <EvolutionTab
          workflowId="wf-1"
          runId="run-1"
          analysisId="AN-1"
          issueSignature="timeout:fetch-data"
          section="diagnosis"
        />
      </MemoryRouter>,
    )

    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByRole('button', { name: '选择分析 run-1 AN-1' })).toBeInTheDocument()
    expect(within(drawer).getAllByText('请求超时')).toHaveLength(1)
    expect(runAnalysis).toHaveBeenCalledWith('run-1', 'AN-1', true)
  })

  it('switches between related analysis records inside the drawer', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(issue).not.toBeNull()
    await userEvent.click(within(issue!).getByRole('button', { name: '问题详情' }))

    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
    expect(within(drawer).getByText('相关分析记录')).toBeInTheDocument()
    expect(within(drawer).queryByText('运行证据')).not.toBeInTheDocument()

    await userEvent.click(within(drawer).getByRole('button', { name: /run-4.*AN-2/ }))
    expect(runAnalysis).toHaveBeenLastCalledWith('run-4', 'AN-2', true)
    expect(within(drawer).getByText('第二次运行请求超时')).toBeInTheDocument()
  })

  it('shows a clear empty state when the selected analysis has no detail payload', async () => {
    runAnalysis.mockReturnValueOnce({
      data: { analysis: null },
      isLoading: false,
      isError: false,
    })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(issue).not.toBeNull()
    await userEvent.click(within(issue!).getByRole('button', { name: '问题详情' }))

    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
    expect(within(drawer).getByText('未找到所选分析详情，请重试或打开关联运行查看。')).toBeInTheDocument()
  })

  it('does not expose the legacy group repair and direct Bot deployment path', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    expect(screen.queryByRole('checkbox', { name: '选择 fetch-data 的建议' })).not.toBeInTheDocument()
    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')!
    await userEvent.click(within(issue).getByRole('button', { name: '问题详情' }))
    expect(screen.queryByRole('region', { name: '本次修复范围' })).not.toBeInTheDocument()
    expect(screen.queryByText('确认修复并部署')).not.toBeInTheDocument()
    expect(screen.queryByText('选择 Bot 自动应用建议')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '应用 2 条建议' })).not.toBeInTheDocument()
  })

  it('merges task activity and repair items into the original issue list and only generates a Pack draft', async () => {
    repairApi.task.mockResolvedValueOnce({
      workflowId: 'wf-1', taskId: 'FIX-1', capabilities: { generation: true, diff: true, publication: false, reason: null },
      execution: { status: 'succeeded', errorCode: null, jobId: 'job-1', attempt: 1, executionId: 'exec-1', rawStatus: 'success' },
      latestAttempt: {
        workflowId: 'wf-1', taskId: 'FIX-1', revision: 2, phase: 'review', stateVersion: 1, requestId: 'request-1', requestDigest: 'f'.repeat(64),
        input: { schemaVersion: 'workflow-repair/v2', taskId: 'FIX-1', revision: 2,
          baseline: { workflowId: 'wf-1', packId: 'pack', releaseRevision: 1, activeDeployNumber: null, specDigest: 'a'.repeat(64), repoId: 'repo', specPath: 'workflow.yaml', packCommit: 'b'.repeat(40), packDigest: 'c'.repeat(40) },
          items: [{ itemId: 'item-1', groupKey: 'group-1', proposalKey: 'd'.repeat(64), contentRevision: 2, previousItemId: null, proposal: { summary: '将超时阈值调整为 90 秒' }, instruction: '保留原业务分支', sources: [{ kind: 'diagnosis_candidate', signature: 'timeout:fetch-data', inputDigest: 'e'.repeat(64), candidateId: 'candidate-1', analysisId: 'AN-1', diagnosisId: 'd-1', flowId: 'run-1' }] }],
          excludedSources: { count: 0, digest: '0'.repeat(64) }, instructions: '保留原业务分支', parentCandidateCommit: null, feedback: '', previousReportRef: null, taskBranch: 'repair/FIX-1' },
        draft: { candidateCommit: 'f'.repeat(40), summary: '候选修复已生成' }, checks: {}, candidateDigest: '1'.repeat(64), checksDigest: '2'.repeat(64), error: null, createdAtMs: 1, updatedAtMs: 2,
      },
      latestSuccessful: null, revisions: [],
    })
    repairApi.candidates.mockResolvedValueOnce(repairPage({
      tasks: [{ taskId: 'FIX-1', revision: 2, phase: 'review', updatedAtMs: 2, itemCount: 1 }],
    }))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    expect(await screen.findByRole('region', { name: '问题与优化任务入口' })).toHaveTextContent('包含 1 条建议')
    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')!
    expect(within(issue).queryByRole('checkbox')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '查看并审阅' }))
    const taskDialog = await screen.findByRole('region', { name: 'Workflow 修复工作区' })
    expect(within(taskDialog).getByText('候选修复已生成')).toBeInTheDocument()
    await userEvent.click(within(taskDialog).getByRole('tab', { name: '发布后效果' }))
    expect(within(taskDialog).getByText('此候选尚未发布，暂无对应部署的观察证据。')).toBeVisible()
    await userEvent.click(within(taskDialog).getByRole('button', { name: /返回问题与优化/ }))

    expect(screen.queryByRole('region', { name: '本次修复操作' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看并审阅' })).toBeVisible()
  })



  it('starts with no selected suggestions and requires an explicit selection before generation', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs(false)
    const checkbox = await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })
    expect(checkbox).not.toBeChecked()
    expect(screen.getByRole('button', { name: '生成优化草稿（0）' })).toBeDisabled()
    await userEvent.click(checkbox)
    expect(screen.getByRole('button', { name: '生成优化草稿（1）' })).toBeEnabled()
  })

  it.each(['loading', 'error'])('keeps repair controls usable when issue summaries are %s', async state => {
    lifecycle.groupsLoading = state === 'loading'; lifecycle.groupsError = state === 'error'
    lifecycle.hideGroups = true
    repairApi.candidates.mockResolvedValue(repairPage())
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs(false)
    expect(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })).toBeVisible()
    expect(screen.getByRole('button', { name: '生成优化草稿（0）' })).toBeDisabled()
    expect(screen.queryByText(/当前工作流暂无已记录异常/)).not.toBeInTheDocument()
  })

  it('retries a failed detail locally and can inspect standalone historical suggestions', async () => {
    lifecycle.hideGroups = true
    repairApi.candidates.mockResolvedValue(repairPage())
    repairApi.item.mockRejectedValueOnce(new Error('读取超时')).mockResolvedValueOnce(repairItem())
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs()
    const drawer = screen.getByRole('dialog', { name: '全部建议与历史' })
    expect(await within(drawer).findByRole('alert')).toHaveTextContent('读取超时')
    await userEvent.click(within(drawer).getByRole('button', { name: '重试详情' }))
    expect(await within(drawer).findByText('技术详情（来源标识与完整载荷）')).toBeInTheDocument()
    expect(within(drawer).queryByRole('alert')).not.toBeInTheDocument()
    expect(repairApi.item).toHaveBeenCalledTimes(2)
    expect(repairApi.create).not.toHaveBeenCalled()
  })

  it('opens one suggestion in the shared drawer without expanding evidence in the problem list', async () => {
    repairApi.candidates.mockResolvedValue(repairPage({ items: [repairItem(), repairItem('item-2', '增加重试')] }))
    const completePrompt = '完整修改目标'.repeat(80)
    repairApi.item.mockResolvedValue({ ...repairItem(), proposal: { summary: '将超时阈值调整为 90 秒',
      operations: [{ op: 'replace', nodeId: 'fetch-data', path: '/executor/prompt', value: completePrompt }] },
      context: { signature: 'timeout:fetch-data', diagnoses: [{ nodeId: 'fetch-data', reasoning: '诊断正文', flowId: 'run-1', analysisId: 'AN-1' }] } })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs(false)
    const checkbox = await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })
    const candidate = checkbox.closest('article')!
    expect(repairApi.item).not.toHaveBeenCalled()
    expect(within(candidate).queryByText('查看建议与证据')).not.toBeInTheDocument()
    await openIssueRepairs()
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByRole('button', { name: '修复建议' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(drawer).getByRole('checkbox', { name: '选择 增加重试' })).toBeInTheDocument()
    expect(await within(drawer).findByText(completePrompt)).toBeInTheDocument()
    expect(within(candidate).queryByText('诊断正文')).not.toBeVisible()
    await userEvent.click(within(drawer).getByRole('button', { name: '关闭' }))
    await openIssueRepairs()
    expect(repairApi.item).toHaveBeenCalledTimes(1)
  })

  it('keeps a diagnosis cluster when its incompatible legacy repair source was skipped', async () => {
    repairApi.candidates.mockResolvedValueOnce(repairPage({ items: [], total: 0, repairSignatures: [] }))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    expect(screen.queryByRole('region', { name: '本次修复操作' })).not.toBeInTheDocument()
    const issue = screen.getByText('write-report', { selector: 'span' }).closest('article')!
    expect(issue).not.toBeNull()
    await userEvent.click(within(issue).getByRole('button', { name: '问题详情' }))
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
    await userEvent.click(within(drawer).getByText('历史建议与任务（独立于本次修复）'))
    expect(within(drawer).getByRole('button', { name: '应用建议' })).toBeInTheDocument()
  })

  it('loads the selected repair state from the server before paginating it', async () => {
    repairApi.candidates.mockImplementation((_workflowId: string, query: { state?: string }) => Promise.resolve(query.state === 'pending'
      ? repairPage({ items: [repairItem('pending-on-later-page', '后续页待处理建议')], total: 1 })
      : repairPage({ items: [{ ...repairItem('processing-first-page', '首页处理中建议'), state: 'processing' }], total: 21 })))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    await openIssueRepairs(false)
    expect(await screen.findByText('首页处理中建议')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '建议状态' }), 'pending')
    expect(await screen.findByText('后续页待处理建议')).toBeInTheDocument()
  })

  it('lets users explicitly include historical repair items', async () => {
    repairApi.candidates.mockImplementation((_workflowId: string, query: { includeHistorical?: boolean }) => Promise.resolve(query.includeHistorical
      ? repairPage({ items: [repairItem('historical-item', '历史未复现建议')], includeHistorical: true })
      : repairPage({ items: [], total: 0 })))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    const history = await screen.findByRole('checkbox', { name: '包含历史未复现' })
    await userEvent.click(history)
    await openIssueRepairs(false)
    expect(await screen.findByText('历史未复现建议')).toBeInTheDocument()
    expect(repairApi.candidates).toHaveBeenLastCalledWith('wf-1', expect.objectContaining({ includeHistorical: true, page: 1, pageSize: 20 }))
  })

  it('drops the old selection while a changed history scope is loading', async () => {
    repairApi.candidates.mockImplementation((_workflowId, query) => query.includeHistorical
      ? new Promise(() => {}) : Promise.resolve(repairPage()))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    expect(screen.getByRole('button', { name: '生成优化草稿（1）' })).toBeEnabled()
    await closeIssue()
    await userEvent.click(screen.getByRole('checkbox', { name: '包含历史未复现' }))

    expect(screen.queryByRole('region', { name: '本次修复操作' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '处理任务' })).not.toBeInTheDocument()
  })

  it('loads full evidence only when a repair item is expanded', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    repairApi.item.mockResolvedValueOnce({ ...repairItem(), context: { signature: 'timeout:fetch-data', evidencePayload: '完整证据载荷' } })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    await openIssueRepairs()
    expect(await screen.findByText(/\u5b8c\u6574\u8bc1\u636e\u8f7d\u8377/)).toBeInTheDocument()
    expect(repairApi.item).toHaveBeenCalledWith('wf-1', 'item-1')
  })

  it('drops repair state immediately when the workflow changes', async () => {
    repairApi.candidates.mockImplementation((workflowId: string) => workflowId === 'wf-1'
      ? Promise.resolve(repairPage({ items: [repairItem('old-item', '旧工作流修复项')] }))
      : new Promise(() => {}))
    const view = render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs(false)
    expect(await screen.findByText('旧工作流修复项')).toBeInTheDocument()

    view.rerender(<MemoryRouter><EvolutionTab workflowId="wf-2" section="diagnosis" /></MemoryRouter>)
    expect(screen.queryByText('旧工作流修复项')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '本次修复操作' })).not.toBeInTheDocument()
  })

  it.each([
    { state: 'pending', action: 'no_action', label: '暂不处理', nextState: 'no_action' },
    { state: 'no_action', action: 'restore', label: '恢复', nextState: 'pending' },
  ])('closes stale details after $action removes the item from the $state filter', async ({ state, action, label, nextState }) => {
    let changed = false
    const item = { ...repairItem(), state, stateVersion: 3 }
    const updated = { ...item, state: nextState, stateVersion: 4 }
    repairApi.candidates.mockImplementation((_workflowId, query) => Promise.resolve(repairPage({
      items: query.state === 'all' || query.state === (changed ? nextState : state) ? [changed ? updated : item] : [],
    })))
    repairApi.item.mockImplementation(() => Promise.resolve(changed ? updated : item))
    repairApi.disposition.mockImplementation(() => {
      changed = true
      return Promise.resolve({ itemId: item.itemId, state: nextState, stateVersion: 4, disposition: null })
    })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs(false)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '建议状态' }), state)
    await userEvent.click((await screen.findAllByRole('button', { name: '查看修改与依据' }))[0])
    await userEvent.click(await screen.findByRole('button', { name: `${label} 将超时阈值调整为 90 秒` }))
    const dialog = screen.getByRole('dialog', { name: action === 'restore' ? '恢复待处理' : '暂不处理' })
    await userEvent.type(within(dialog).getByRole('textbox', { name: '处置原因' }), '重新评估处理范围')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await openIssueRepairs(false)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '建议状态' }), nextState)
    await userEvent.click((await screen.findAllByRole('button', { name: '查看修改与依据' }))[0])
    const reopened = screen.getByRole('dialog', { name: '问题详情' })
    await waitFor(() => expect(within(reopened).getByText(/"stateVersion": 4/)).toBeInTheDocument())
    expect(within(reopened).getByRole('button', { name: `${action === 'restore' ? '暂不处理' : '恢复'} 将超时阈值调整为 90 秒` })).toBeEnabled()
  })

  it('reuses a disposition request ID only for an exact retry', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    repairApi.disposition.mockRejectedValueOnce(new Error('response lost'))
      .mockRejectedValueOnce(new Error('response lost again'))
      .mockResolvedValueOnce({})
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    await openIssueRepairs()
    await userEvent.click(await screen.findByRole('button', { name: '暂不处理 将超时阈值调整为 90 秒' }))
    const dialog = screen.getByRole('dialog', { name: '暂不处理' })
    const reason = within(dialog).getByRole('textbox', { name: '处置原因' })
    await userEvent.type(reason, '等待更多证据')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }))
    await waitFor(() => expect(repairApi.disposition).toHaveBeenCalledTimes(1))
    const firstId = repairApi.disposition.mock.calls[0][1].requestId
    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }))
    await waitFor(() => expect(repairApi.disposition).toHaveBeenCalledTimes(2))
    expect(repairApi.disposition.mock.calls[1][1].requestId).toBe(firstId)
    await userEvent.type(reason, '，下周复核')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }))
    await waitFor(() => expect(repairApi.disposition).toHaveBeenCalledTimes(3))
    expect(repairApi.disposition.mock.calls[2][1].requestId).not.toBe(firstId)
  })

  it('keeps the problem list usable when repair item loading fails', async () => {
    repairApi.candidates.mockRejectedValueOnce(new Error('Repair request failed'))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    expect(await screen.findByText('修复任务与处理状态加载失败；问题与证据仍可查看。')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '问题详情' }).length).toBeGreaterThan(0)
  })

  it('distinguishes forbidden reads and removes stale statistics after a failed refresh', async () => {
    repairApi.candidates.mockResolvedValueOnce(repairPage({ total: 223 }))
      .mockRejectedValueOnce(Object.assign(new Error('API 403'), { status: 403,
        body: JSON.stringify({ code: 'FORBIDDEN', requestId: '8c8ed247-2578-4439-8857-e6a6a83e1211' }) }))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    expect(screen.queryByRole('region', { name: '本次修复操作' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: '包含历史未复现' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('访问被拒绝（403）')
    expect(screen.getByRole('alert')).toHaveTextContent('8c8ed247-2578-4439-8857-e6a6a83e1211')
    expect(screen.queryByText('223')).not.toBeInTheDocument()

    expect(screen.queryByRole('region', { name: '本次修复操作' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '问题详情' }).length).toBeGreaterThan(0)
  })

  it.each([false, true])('shows application progress with diagnosis groups removed=%s', async (hideGroups) => {
    lifecycle.hideGroups = hideGroups
    const now = Date.now()
    applyTasks.mockReturnValueOnce({
      data: {
        tasks: [{
          taskId: 'EVAP-1',
          stepId: 'EVAP-1-step-apply',
          suggestionId: 's-1',
          status: 'dispatched',
          summary: null,
          botId: 'bot-1',
          botName: '修复 Bot',
          botEnv: 'pre',
          errorMessage: null,
          appliedAt: null,
          createdAt: now - 125_000,
          updatedAt: now - 100_000,
          progress: {
            phase: 'editing_workflow',
            message: '正在修改 Workflow',
            elapsedMs: 125_000,
            updatedAtMs: now - 100_000,
            stalled: true,
            history: [{
              phase: 'planning_change',
              message: 'Agent 正在生成修改方案',
              updatedAtMs: now - 120_000,
            }, {
              phase: 'editing_workflow',
              message: '工具调用已返回：workflow_edit',
              updatedAtMs: now - 100_000,
            }],
          },
        }, {
          taskId: 'EVAP-OLD',
          stepId: 'EVAP-OLD-step-apply',
          suggestionId: 's-1',
          status: 'failed',
          summary: null,
          botId: 'bot-old',
          botName: '旧 Bot',
          botEnv: 'pre',
          errorMessage: '历史失败',
          appliedAt: 90,
          createdAt: 90,
          updatedAt: 91,
        }],
      },
    })

    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    expect(screen.getByText('Bot 正在执行应用和部署')).toBeInTheDocument()
    expect(screen.getByText(/正在修改 Workflow · 已用时 2分/)).toBeInTheDocument()
    expect(screen.getByText(/超过 90 秒未更新/)).toBeInTheDocument()
    expect(screen.getByText(/Bot: 修复 Bot · pre/)).toBeInTheDocument()
    await userEvent.click(screen.getByText('执行记录（2）'))
    expect(screen.getByText('Agent 正在生成修改方案')).toBeInTheDocument()
    expect(screen.getByText('工具调用已返回：workflow_edit')).toBeInTheDocument()
  })



  it('retains unchanged original selection on unrelated source changes without selecting the new page', async () => {
    let changed = false
    repairApi.candidates.mockImplementation((_workflowId, query) => {
      if (query.page === 2) changed = true
      return Promise.resolve(changed ? {
        ...repairPage({ items: [repairItem('new-item', '新来源建议')], page: query.page, total: 21 }), inputDigest: 'new-digest',
      } : repairPage({ total: 21 }))
    })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await userEvent.click(screen.getByRole('button', { name: '下一页建议' }))
    expect(await screen.findByRole('checkbox', { name: '选择 新来源建议' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '生成优化草稿（1）' })).toBeEnabled()
  })



  it('lets users navigate to evidence beyond the ten newest runs', async () => {
    lifecycle.extraRuns = 12
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getAllByRole('button', { name: '问题详情' })[0])
    await userEvent.click(screen.getByRole('button', { name: '证据与历史' }))
    await userEvent.click(screen.getByRole('button', { name: '下一页分析' }))
    expect(screen.getByRole('button', { name: /选择分析 extra-run-11/ })).toBeVisible()
  })
})

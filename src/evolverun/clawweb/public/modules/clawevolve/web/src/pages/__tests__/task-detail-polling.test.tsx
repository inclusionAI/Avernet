// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import Evolve from '../Evolve'
import type { EvolveTaskPresentationExtension } from '../../features/evolve/host-extensions'

const api = vi.hoisted(() => ({ evolve: { getTask: vi.fn(), listTaskLogArchives: vi.fn(), getTaskSkillDiff: vi.fn(), getSkillAsset: vi.fn(), getSkillAssetHistory: vi.fn(), getSkillVersionContent: vi.fn(), getSkillVersionDiff: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ authState: 'authenticated', user: { userId: 'viewer' } }) }))

function task(status = 'running', ready = false) {
  return { task_id: 'TASK-1', task_type: 'diagnose', task_name: `Polling ${status}`, user_id: 'owner', bot_id: 'bot', created_by: 'owner', status, steps: [], interactions: [],
    config: { targetSkill: { assetId: 'SKILL-1', name: 'Fixture Skill', candidate: ready ? { artifact: { ref: 'candidate.zip', sha256: 'ready-hash' } } : {} } } }
}
async function tick(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
function Navigation() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/evolve/runs/TASK-2')}>Open second task</button>
}
function Location() { const location = useLocation(); return <output aria-label="location">{location.pathname}{location.search}</output> }
function open(taskPresentationExtensions: readonly EvolveTaskPresentationExtension[] = [], initialEntry = '/evolve/runs/TASK-1') {
  return render(<MemoryRouter initialEntries={[initialEntry]}><Navigation /><Evolve taskPresentationExtensions={taskPresentationExtensions} /><Location /></MemoryRouter>)
}
beforeEach(() => {
  vi.stubGlobal('React', React); vi.useFakeTimers(); vi.resetAllMocks()
  api.evolve.listTaskLogArchives.mockResolvedValue({ items: [] })
  api.evolve.getTaskSkillDiff.mockRejectedValue(new Error('候选尚未生成'))
  api.evolve.getSkillAsset.mockResolvedValue({ assetId: 'SKILL-1', botId: 'bot', skillId: 'skill', name: 'Fixture Skill', currentVersion: 'v1', updatedAt: 1, versions: [{ versionId: 'V1', version: 'v1', createdAt: 1 }] })
  api.evolve.getSkillAssetHistory.mockResolvedValue({ events: [] })
  api.evolve.getSkillVersionContent.mockResolvedValue({ files: [], selected: null })
  api.evolve.getSkillVersionDiff.mockResolvedValue({ baseline: null, files: [] })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('Task detail live updates', () => {
  it('keeps the StageTest HTML form and errors while withholding waiting HITL output as deliverables', async () => {
    const question = { format: 'html', tag: 'scope', content: '<form><input name="scope"><input type="hidden" name="payload" value="HIDDEN_PAYLOAD_SENTINEL"><button>提交</button></form>' }
    const waiting = { ...task('waiting_human'), task_type: 'stage_test', user_id: 'viewer',
      config: { stageTest: { stage: 'diagnose', mode: 'preprocess' } },
      steps: [{ stepId: 'STEP-1', taskId: 'TASK-1', stepType: 'stage_extension', status: 'waiting_context', command: 'stage test',
        error: { code: 'CONTEXT', message: '保留步骤错误' }, output: { hitl: { question } } }],
      interactions: [{ interactionId: 'HITL-1', stepId: 'STEP-1', status: 'waiting', question }],
    }
    api.evolve.getTask.mockResolvedValueOnce(waiting).mockResolvedValue({ ...waiting, status: 'completed',
      steps: [{ ...waiting.steps[0], status: 'succeeded', output: { summary: '最终交付结果' } }],
      interactions: [{ ...waiting.interactions[0], status: 'answered', answer: { scope: '已确认' } }],
    })
    const view = open(); await tick()
    const frame = view.container.querySelector('iframe')!
    expect(frame).toBeTruthy()
    expect(frame.getAttribute('sandbox')).toBe('allow-forms allow-scripts')
    expect(frame.srcdoc).toContain('HIDDEN_PAYLOAD_SENTINEL')
    expect(screen.getByText('等待回答')).toBeTruthy()
    expect(screen.getAllByText('等待补充上下文').length).toBeGreaterThan(0)
    expect(screen.getAllByText('CONTEXT: 保留步骤错误').length).toBeGreaterThan(0)
    expect(screen.queryByText('自定义 Stage 交付结果')).toBeNull()
    const rawOutputs = Array.from(view.container.querySelectorAll('pre')).filter((el) => el.textContent?.includes('HIDDEN_PAYLOAD_SENTINEL'))
    expect(rawOutputs.length).toBeGreaterThan(0)
    expect(rawOutputs.every((el) => el.closest('details')?.open === false)).toBe(true)
    await tick(3000)
    expect(screen.getAllByText('自定义 Stage 交付结果').length).toBeGreaterThan(0)
    expect(screen.getAllByText('最终交付结果').length).toBeGreaterThan(0)
    expect(screen.getByTitle('Stage 已回答的交互表单')).toBeTruthy()
  })

  it('filters Stage test preparation and artifacts without introducing a second visual section', async () => {
    const step = (stepId: string, stepType: string) => ({ stepId, taskId: 'TASK-1', stepType, status: 'succeeded', command: 'original command', summary: 'original ' + stepType })
    const history = { ...task('completed', true), task_type: 'stage_test', config: { ...task('completed', true).config, stageTest: { stage: 'diagnose', mode: 'replace' } },
      steps: [step('PREP', 'skill_prepare'), { ...step('MAIN', 'stage_extension'), stageExtension: { stage: 'diagnose', mode: 'replace', implementationId: 'IMPL-HOST', displayName: '自定义诊断实现' } }, step('FINAL', 'skill_finalize')],
      interactions: [{ interactionId: 'HITL-1', stepId: 'MAIN', status: 'answered', question: { format: 'text', content: '历史问题' }, answer: { content: '历史回答', tag: 'scope' } }],
    }
    api.evolve.getTask.mockResolvedValue(history)
    const view = open(); await tick()
    expect(screen.getByRole('heading', { name: 'Stage 集成测试流程' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '进化工作流' })).toBeNull()
    expect(screen.getByRole('button', { name: /自定义诊断实现.*自定义/ })).toBeTruthy()
    expect(screen.queryByText('测试环境准备与产物记录（2）')).toBeNull()
    expect(view.container.querySelector('#step-PREP')).toBeNull()
    expect(view.container.querySelector('#step-FINAL')).toBeNull()
    expect(screen.getByText('历史回答').closest('#step-MAIN')).toBeTruthy()
    expect(view.container.querySelectorAll('#step-MAIN')).toHaveLength(1)
    expect(screen.queryByText('Skill 候选版本')).toBeNull()
    expect(api.evolve.getTaskSkillDiff).not.toHaveBeenCalled()
  })

  it('lets the embedding host render and replace one Step result through an opaque presentation extension', async () => {
    const custom = { ...task('completed'), config: { ...task('completed').config,
      presentation: { extensionId: 'host.result', data: { anything: true } } },
      steps: [{ stepId: 'HOST', taskId: 'TASK-1', stepType: 'stage_extension', status: 'succeeded',
        command: 'host stage', output: { summary: 'default deliverable sentinel' } }],
    }
    api.evolve.getTask.mockResolvedValue(custom)
    const extension: EvolveTaskPresentationExtension = {
      id: 'host.result',
      renderStepResult: ({ step }) => step.stepId === 'HOST' ? <div>Host-rendered result</div> : null,
      suppressDefaultStepDeliverables: ({ step }) => step.stepId === 'HOST',
    }
    const view = open([extension]); await tick()
    expect(screen.getByText('Host-rendered result')).toBeTruthy()
    expect(view.container.querySelector('#step-HOST')?.textContent).not.toContain('自定义 Stage 交付结果')
  })

  it('folds prepared Skill results and presents their fields in readable Chinese instead of raw JSON', async () => {
    const prepared = { ...task('completed'), steps: [{
      stepId: 'PREPARE', taskId: 'TASK-1', stepType: 'skill_prepare', status: 'succeeded', command: 'prepare candidate',
      output: { prepared: true, workspace: '/workspace/candidate', targetSkillPath: '/workspace/candidate/skills/example' },
    }] }
    api.evolve.getTask.mockResolvedValue(prepared)
    const view = open(); await tick()
    const results = screen.getAllByText('Skill 候选处理结果')
    expect(results.length).toBeGreaterThan(0)
    expect(results.every((title) => title.closest('details')?.open === false)).toBe(true)
    const details = results.at(-1)!.closest('details')!
    expect(details.querySelector('pre')).toBeNull()

    fireEvent.click(details.querySelector('summary')!)
    expect(details.open).toBe(true)
    expect(details.textContent).toContain('准备状态')
    expect(details.textContent).toContain('已完成')
    expect(details.textContent).toContain('候选工作区')
    expect(details.textContent).toContain('/workspace/candidate')
    expect(details.textContent).toContain('目标 Skill 路径')
    expect(view.container.querySelector('#step-PREPARE')).toBeTruthy()
  })

  it('polls running to completed and supplies the new candidate without page navigation', async () => {
    api.evolve.getTask.mockResolvedValueOnce(task()).mockResolvedValueOnce(task('completed', true))
    api.evolve.getTaskSkillDiff.mockResolvedValueOnce({ files: [{ path: 'SKILL.md', change: 'modified', before: 'Task baseline', after: 'Final candidate' }] })
    open(); await tick()
    expect(screen.getByRole('heading', { name: 'Polling running' })).toBeTruthy()
    await tick(3000)
    expect(screen.getByRole('heading', { name: 'Polling completed' })).toBeTruthy()
    expect(screen.getByText('Final candidate')).toBeTruthy()
    expect(screen.queryByText('候选尚未生成')).toBeNull()
  })

  it('returns a task opened from Skill detail to the exact Skill workbench state', async () => {
    api.evolve.getTask.mockResolvedValue(task('completed'))
    const returnTo = '/evolve/skills/SKILL-1?selected=task%3ATASK-1&view=task'
    open([], `/evolve/runs/TASK-1?${new URLSearchParams({ returnTo })}`)
    await tick()
    fireEvent.click(screen.getByRole('button', { name: '返回 Skill 详情' }))
    expect(screen.getByLabelText('location').textContent).toBe(returnTo)
  })

  it('discovers a new HITL question and keeps polling while waiting for input', async () => {
    const waiting = { ...task('waiting_human'), steps: [{ stepId: 'STEP-1', taskId: 'TASK-1', stepType: 'stage_extension', status: 'waiting_context', command: 'stage test' }], interactions: [{ interactionId: 'HITL-1', stepId: 'STEP-1', status: 'waiting', question: { format: 'text', content: 'Confirm the diagnosis scope' } }] }
    api.evolve.getTask.mockResolvedValueOnce(task()).mockResolvedValue(waiting)
    open(); await tick(); await tick(3000)
    expect(screen.getByText('Confirm the diagnosis scope')).toBeTruthy()
    expect(screen.getByText('等待回答')).toBeTruthy()
    await tick(3000)
    expect(api.evolve.getTask).toHaveBeenCalledTimes(3)
  })

  it.each(['completed', 'failed', 'canceled'])('stops automatic requests at terminal status %s', async (status) => {
    api.evolve.getTask.mockResolvedValueOnce(task()).mockResolvedValue(task(status))
    open(); await tick(); await tick(3000); await tick(12000)
    expect(screen.getByRole('heading', { name: `Polling ${status}` })).toBeTruthy()
    expect(api.evolve.getTask).toHaveBeenCalledTimes(2)
  })

  it('keeps the visible task through a transient failure and recovers on the next poll', async () => {
    api.evolve.getTask.mockResolvedValueOnce(task()).mockRejectedValueOnce(new Error('Temporary offline')).mockResolvedValueOnce(task('completed'))
    open(); await tick(); await tick(3000)
    expect(screen.getByRole('heading', { name: 'Polling running' })).toBeTruthy()
    expect(screen.queryByText('正在加载任务详情…')).toBeNull()
    await tick(3000)
    expect(screen.getByRole('heading', { name: 'Polling completed' })).toBeTruthy()
  })

  it('never overlaps requests when a poll is slow', async () => {
    let finish!: (value: ReturnType<typeof task>) => void
    api.evolve.getTask.mockResolvedValueOnce(task()).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    open(); await tick(); await tick(3000); await tick(15000)
    expect(api.evolve.getTask).toHaveBeenCalledTimes(2)
    await act(async () => { finish(task('completed')) })
    await tick(12000)
    expect(api.evolve.getTask).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('heading', { name: 'Polling completed' })).toBeTruthy()
  })

  it.each(['resolve', 'reject'] as const)('ignores an old task request that later %ss after navigation', async (outcome) => {
    let finish!: (value: ReturnType<typeof task>) => void
    let fail!: (reason: Error) => void
    api.evolve.getTask.mockReturnValueOnce(new Promise((resolve, reject) => { finish = resolve; fail = reject }))
      .mockResolvedValueOnce({ ...task('completed'), task_id: 'TASK-2', task_name: 'Second task' })
    open(); await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Open second task' })); await tick()
    await act(async () => { if (outcome === 'resolve') finish(task()); else fail(new Error('Old task failed')) })
    await tick(12000)
    expect(screen.getByRole('heading', { name: 'Second task' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Polling running' })).toBeNull()
    expect(screen.queryByText('Old task failed')).toBeNull()
    expect(api.evolve.getTask).toHaveBeenCalledTimes(2)
  })

  it('does not restart polling after unmount with a request still in flight', async () => {
    let finish!: (value: ReturnType<typeof task>) => void
    api.evolve.getTask.mockResolvedValueOnce(task()).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const view = open(); await tick(); await tick(3000); view.unmount()
    await act(async () => { finish(task()) }); await tick(12000)
    expect(api.evolve.getTask).toHaveBeenCalledTimes(2)
  })
})

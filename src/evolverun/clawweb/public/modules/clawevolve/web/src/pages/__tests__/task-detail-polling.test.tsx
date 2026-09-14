// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import Evolve from '../Evolve'

const api = vi.hoisted(() => ({ evolve: { getTask: vi.fn(), listTaskLogArchives: vi.fn(), getTaskSkillDiff: vi.fn() } }))
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
function open() { return render(<MemoryRouter initialEntries={['/evolve/runs/TASK-1']}><Navigation /><Evolve /></MemoryRouter>) }
beforeEach(() => {
  vi.stubGlobal('React', React); vi.useFakeTimers(); vi.resetAllMocks()
  api.evolve.listTaskLogArchives.mockResolvedValue({ items: [] })
  api.evolve.getTaskSkillDiff.mockRejectedValue(new Error('候选尚未生成'))
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('Task detail live updates', () => {
  it('folds legacy Stage test preparation and artifacts while preserving its original step and answered interaction', async () => {
    const step = (stepId: string, stepType: string) => ({ stepId, taskId: 'TASK-1', stepType, status: 'succeeded', command: 'original command', summary: 'original ' + stepType })
    const history = { ...task('completed', true), task_type: 'stage_test', config: { ...task('completed', true).config, stageTest: { stage: 'diagnose', mode: 'replace' } },
      steps: [step('PREP', 'skill_prepare'), step('MAIN', 'stage_extension'), step('FINAL', 'skill_finalize')],
      interactions: [{ interactionId: 'HITL-1', stepId: 'MAIN', status: 'answered', question: { format: 'text', content: '历史问题' }, answer: { content: '历史回答', tag: 'scope' } }],
    }
    api.evolve.getTask.mockResolvedValue(history)
    const view = open(); await tick()
    expect(screen.getByRole('heading', { name: 'Stage 集成测试流程' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: '进化工作流' })).toBeNull()
    const archive = screen.getByText('测试环境准备与产物记录（2）').closest('details')!
    expect(archive.open).toBe(false)
    expect(archive.querySelector('#step-PREP')).toBeTruthy()
    expect(archive.querySelector('#step-FINAL')).toBeTruthy()
    expect(archive.querySelector('#step-MAIN')).toBeNull()
    expect(screen.getByText('历史回答').closest('#step-MAIN')).toBeTruthy()
    expect(view.container.querySelectorAll('#step-MAIN')).toHaveLength(1)
    expect(screen.queryByText('Skill 候选版本')).toBeNull()
    expect(api.evolve.getTaskSkillDiff).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('测试环境准备与产物记录（2）'))
    expect(archive.open).toBe(true)
    expect(archive.textContent).toContain('original skill_finalize')
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

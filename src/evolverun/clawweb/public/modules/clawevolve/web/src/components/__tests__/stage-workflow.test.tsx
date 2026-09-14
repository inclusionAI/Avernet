// @vitest-environment jsdom
import React, { useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { EvolveTask, EvolveStep } from '../../api/client'
import Evolve from '../../pages/Evolve'
import StageWorkflow from '../StageWorkflow'
import { StepInteractions } from '../SkillTaskRuntimePanel'

const api = vi.hoisted(() => ({ evolve: { getStageSkill: vi.fn(), answerStageInteraction: vi.fn(), getTask: vi.fn(), listTaskLogArchives: vi.fn(), listVersions: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ authState: 'authenticated', user: { userId: 'owner' } }) }))
beforeEach(() => { vi.stubGlobal('React', React); vi.resetAllMocks() })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function step(stepId: string, status: string, stage?: string, mode = 'preprocess', roundNo: number | null = null): EvolveStep {
  return { stepId, taskId: 'TASK', stepType: stage ? 'stage_extension' : 'diagnose', stepNo: 1, roundNo, status,
    command: 'frozen command', output: { summary: 'receipt is not a status' },
    ...(stage ? { stageExtension: { stage, mode, implementationId: `${stage}-${mode}`, displayName: stage === 'diagnose' ? '诊断范围确认' : '轮内质量检查' } } : {}),
  } as EvolveStep
}
function task(steps: EvolveStep[]): EvolveTask {
  return { task_id: 'TASK', task_type: 'full', config: { stageSelection: { diagnose: true, plan: false, optimize: false } }, steps,
    interactions: [{ interactionId: 'HITL', stepId: 'PRE', status: 'waiting', question: { tag: 'scope', format: 'text', content: '请选择诊断范围' } }],
  } as EvolveTask
}
function Workflow({ value }: { value: EvolveTask }) {
  const [selectedStepId, onSelect] = useState<string | null>(null)
  return <StageWorkflow task={value} selectedStepId={selectedStepId} onSelect={onSelect}
    renderDetails={(selected) => <StepInteractions task={value} stepId={selected.stepId} canOperate onUpdated={async () => {}} />} />
}

it('shows the registered preprocess as an independent waiting node and opens its existing form', () => {
  render(<Workflow value={task([step('PRE', 'waiting_context', 'diagnose'), step('BUILTIN', 'queued')])} />)
  const node = screen.getByRole('button', { name: /诊断范围确认/ })
  expect(within(node).getByText('前置')).toBeTruthy()
  expect(within(node).getByText('等待补充信息')).toBeTruthy()
  expect(screen.queryByText('请选择诊断范围')).toBeNull()
  fireEvent.click(node)
  expect(screen.getByText('请选择诊断范围')).toBeTruthy()
  expect(screen.getByRole('button', { name: '提交并继续' })).toBeTruthy()
  expect(within(node).queryByText('已完成')).toBeNull()
})

it('keeps preprocess and postprocess inside their real optimize rounds, including a round with only preprocess started', () => {
  const value = task([
    { ...step('OPT-1', 'succeeded'), stepType: 'optimize', roundNo: 1, stepNo: 2 },
    { ...step('POST-1', 'failed', 'optimize', 'postprocess', 1), stepNo: 3 },
    { ...step('PRE-2', 'running', 'optimize', 'preprocess', 2), stepNo: 4 },
    { ...step('PRE-1', 'succeeded', 'optimize', 'preprocess', 1), stepNo: 1 },
  ])
  value.config = { stageSelection: { diagnose: false, plan: false, optimize: true } }
  render(<Workflow value={value} />)
  const first = within(screen.getByRole('group', { name: '第 1 轮优化' }))
  expect(first.getAllByRole('button').map((node) => node.textContent)).toEqual([
    expect.stringContaining('PRE-1'), expect.stringContaining('OPT-1'), expect.stringContaining('POST-1'),
  ])
  expect(within(first.getByRole('button', { name: /POST-1/ })).getByText('失败')).toBeTruthy()
  const second = within(screen.getByRole('group', { name: '第 2 轮优化' }))
  expect(second.getByRole('button', { name: /PRE-2/ })).toBeTruthy()
  expect(second.queryByText('已完成')).toBeNull()
  expect(second.getByText('尚未创建')).toBeTruthy()
})

it('shows configured but uncreated extensions without borrowing an unbound receipt or claiming completion', async () => {
  api.evolve.getStageSkill.mockResolvedValue({ implementationId: 'POST', stage: 'diagnose', mode: 'postprocess', displayName: '通用诊断复核' })
  const value = task([{ ...step('UNBOUND', 'failed'), stepType: 'stage_extension', output: { summary: 'postprocess succeeded' } }])
  value.config.stageExtensions = { diagnose: { postprocess: { enabled: true, implementationId: 'POST' } } }
  render(<Workflow value={value} />)
  const pending = await screen.findByRole('button', { name: /通用诊断复核/ })
  expect((pending as HTMLButtonElement).disabled).toBe(true)
  expect(within(pending).getByText('状态未知（未关联）')).toBeTruthy()
  expect(within(pending).queryByText('已完成')).toBeNull()
  const unknown = within(screen.getByRole('region', { name: '未关联的执行步骤' }))
  expect(unknown.getByRole('button', { name: /UNBOUND/ })).toBeTruthy()
  expect(unknown.getByText('绑定信息缺失')).toBeTruthy()
})

it('prefers the exact frozen registered name and shows a genuinely uncreated postprocess as pending', () => {
  const value = task([step('PRE', 'running', 'diagnose')])
  value.config.stageExtensions = { diagnose: {
    preprocess: { enabled: true, implementationId: 'diagnose-preprocess', displayName: '已冻结的注册名称' },
    postprocess: { enabled: true, implementationId: 'POST', displayName: '未来复核' },
  } }
  render(<Workflow value={value} />)
  expect(screen.getByRole('button', { name: /已冻结的注册名称/ })).toBeTruthy()
  const pending = screen.getByRole('button', { name: /未来复核/ })
  expect(within(pending).getByText('尚未创建')).toBeTruthy()
  expect((pending as HTMLButtonElement).disabled).toBe(true)
  expect(api.evolve.getStageSkill).not.toHaveBeenCalled()
})

it('keeps replacement independent, respects frozen flow selection, and follows actual status changes', () => {
  const replacement = step('REPLACE', 'waiting_context', 'plan', 'replace')
  const value = task([replacement])
  value.config = { flow: { stages: { diagnose: false, plan: true, optimize: false } }, stageSelection: { diagnose: true, plan: true, optimize: true } }
  const view = render(<Workflow value={value} />)
  expect(screen.queryByRole('region', { name: '诊断工作流' })).toBeNull()
  expect(screen.queryByRole('region', { name: '优化工作流' })).toBeNull()
  expect(screen.getAllByRole('button')).toHaveLength(1)
  expect(screen.getByText('替换')).toBeTruthy()
  view.rerender(<Workflow value={{ ...value, steps: [{ ...replacement, status: 'canceled' }] }} />)
  expect(screen.getByText('已取消')).toBeTruthy()
  expect(screen.queryByText('已完成')).toBeNull()
})

it('does not move an extension with no round to an invented optimize round or accept another task’s step', () => {
  const value = task([step('NO-ROUND', 'failed', 'optimize'), { ...step('FOREIGN', 'succeeded', 'diagnose'), taskId: 'OTHER' }])
  render(<Workflow value={value} />)
  expect(screen.queryByRole('group', { name: '第 1 轮优化' })).toBeNull()
  const unbound = within(screen.getByRole('region', { name: '未关联的执行步骤' }))
  expect(unbound.getByRole('button', { name: /NO-ROUND/ })).toBeTruthy()
  expect(screen.queryByText('FOREIGN')).toBeNull()
  expect(screen.getByText('尚无可关联的优化轮次')).toBeTruthy()
})

it.each(['denied', 'mismatched'])('keeps the frozen implementation ID visible when the name lookup is %s', async (lookup) => {
  if (lookup === 'denied') api.evolve.getStageSkill.mockRejectedValue(new Error('403'))
  else api.evolve.getStageSkill.mockResolvedValue({ implementationId: 'OTHER', stage: 'diagnose', mode: 'preprocess', displayName: '不属于这个节点' })
  const value = task([])
  value.config.stageExtensions = { diagnose: { preprocess: { enabled: true, implementationId: 'REGISTERED-ID' } } }
  await act(async () => { render(<Workflow value={value} />) })
  const node = screen.getByRole('button', { name: /名称不可用 · REGISTERED-ID/ })
  expect(within(node).getByText('尚未创建')).toBeTruthy()
  expect(screen.queryByText('不属于这个节点')).toBeNull()
  expect(screen.queryByText('自定义')).toBeNull()
})

it('uses an identity-matched historical receipt name only as a label, never as the execution status', () => {
  const original = step('OLD', 'failed', 'diagnose')
  const value = task([{ ...original,
    stageExtension: { stage: 'diagnose', mode: 'preprocess', implementationId: 'diagnose-preprocess', displayName: null },
    output: { implementation: { implementationId: 'diagnose-preprocess', displayName: '历史注册名称' }, status: 'succeeded' },
  } as EvolveStep])
  render(<Workflow value={value} />)
  const node = screen.getByRole('button', { name: /历史注册名称/ })
  expect(within(node).getByText('失败')).toBeTruthy()
  expect(within(node).queryByText('已完成')).toBeNull()
})

it('opens the existing form and result in the task detail workflow without duplicating the form', async () => {
  const value = { ...task([step('PRE', 'waiting_context', 'diagnose'),
    { ...step('POST', 'succeeded', 'diagnose', 'postprocess'), output: { summary: '实际后置产物', changed: false } }]),
    task_name: 'Generic workflow', user_id: 'owner', status: 'completed' }
  api.evolve.getTask.mockResolvedValue(value)
  api.evolve.listTaskLogArchives.mockResolvedValue({ items: [] })
  api.evolve.listVersions.mockResolvedValue({ versions: [] })
  render(<MemoryRouter initialEntries={['/evolve/runs/TASK']}><Evolve /></MemoryRouter>)
  const heading = await screen.findByRole('heading', { name: '进化工作流' })
  const graph = within(heading.closest('section')!)
  fireEvent.click(graph.getByRole('button', { name: /前置.*PRE/ }))
  expect(graph.getByText('请选择诊断范围')).toBeTruthy()
  expect(screen.getAllByText('请选择诊断范围')).toHaveLength(1)
  fireEvent.click(graph.getByRole('button', { name: /后置.*POST/ }))
  expect(graph.getByText('实际后置产物')).toBeTruthy()
  expect(screen.getAllByText('请选择诊断范围')).toHaveLength(1)
})

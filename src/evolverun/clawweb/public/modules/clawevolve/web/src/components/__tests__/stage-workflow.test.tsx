// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { EvolveStep, EvolveTask } from '../../api/client'
import Evolve from '../../pages/Evolve'

const api = vi.hoisted(() => ({ evolve: {
  getTask: vi.fn(), listTaskLogArchives: vi.fn(), listVersions: vi.fn(), answerStageInteraction: vi.fn(),
} }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ authState: 'authenticated', user: { userId: 'owner' } }) }))

beforeEach(() => {
  vi.stubGlobal('React', React)
  vi.resetAllMocks()
  api.evolve.listTaskLogArchives.mockResolvedValue({ items: [] })
  api.evolve.listVersions.mockResolvedValue({ versions: [] })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function step(stepId: string, stepType: string, status: string, stepNo: number, roundNo: number | null = null): EvolveStep {
  return { stepId, taskId: 'TASK', stepType, status, stepNo, roundNo, command: `${stepType} command` } as EvolveStep
}

function extension(stepId: string, stage: 'diagnose' | 'plan' | 'optimize', mode: 'preprocess' | 'replace' | 'postprocess',
  status: string, stepNo: number, roundNo: number | null = null): EvolveStep {
  return { ...step(stepId, 'stage_extension', status, stepNo, roundNo),
    stageExtension: { stage, mode, implementationId: `${stage}-${mode}`, displayName: stage === 'diagnose' ? '诊断范围确认' : '轮内质量检查' },
  } as EvolveStep
}

function task(steps: EvolveStep[], config: Record<string, unknown> = {}): EvolveTask {
  return { task_id: 'TASK', task_type: 'full', task_name: '原样式工作流', user_id: 'owner', bot_id: 'BOT', created_by: 'owner',
    status: 'running', config, steps, interactions: [], error_message: null } as EvolveTask
}

async function renderTask(value: EvolveTask) {
  api.evolve.getTask.mockResolvedValue(value)
  render(<MemoryRouter initialEntries={['/evolve/runs/TASK']}><Evolve /></MemoryRouter>)
  await screen.findByRole('heading', { name: value.task_name! })
}

it('inserts a named custom Stage into the original horizontal workflow without replacing the task detail shell', async () => {
  const value = task([
    extension('PRE', 'diagnose', 'preprocess', 'waiting_context', 1),
    step('DIAGNOSE', 'diagnose', 'created', 2),
    step('PLAN', 'plan', 'created', 3),
    step('OPTIMIZE', 'optimize', 'created', 4, 1),
  ], { stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: 'diagnose-preprocess', displayName: '诊断范围确认' } } } })
  value.interactions = [{ interactionId: 'HITL', stepId: 'PRE', status: 'waiting', question: { tag: 'scope', format: 'text', content: '请选择诊断范围' } }] as EvolveTask['interactions']
  await renderTask(value)
  const workflow = screen.getByRole('heading', { name: '进化工作流' }).closest('section')!
  const custom = within(workflow).getByRole('button', { name: /诊断范围确认.*自定义/ })
  const builtin = within(workflow).getByRole('button', { name: /Bot 诊断/ })
  expect(custom.className).toContain('w-52 rounded-xl border p-4 text-left transition')
  expect(builtin.className).toContain('w-52 rounded-xl border p-4 text-left transition')
  expect(workflow.textContent!.indexOf('诊断范围确认')).toBeLessThan(workflow.textContent!.indexOf('Bot 诊断'))
  expect(within(workflow).getByRole('button', { name: /目标规划/ })).toBeTruthy()
  expect(within(workflow).getByRole('button', { name: /优化 Loop/ })).toBeTruthy()
  expect(screen.getByRole('heading', { name: '执行记录' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: '任务配置' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: '诊断 Stage' })).toBeNull()
  expect(screen.queryByRole('region', { name: '诊断工作流' })).toBeNull()
  expect(screen.getAllByText('请选择诊断范围')).toHaveLength(1)
  fireEvent.click(custom)
  expect(screen.getAllByText('请选择诊断范围')).toHaveLength(1)
})

it('uses the same card for a replacement and omits only the replaced default business node', async () => {
  const value = task([extension('REPLACE', 'diagnose', 'replace', 'succeeded', 1)])
  value.task_type = 'diagnose'
  await renderTask(value)
  const workflow = screen.getByRole('heading', { name: '进化工作流' }).closest('section')!
  expect(within(workflow).getByRole('button', { name: /诊断范围确认.*自定义/ })).toBeTruthy()
  expect(within(workflow).queryByRole('button', { name: /Bot 诊断/ })).toBeNull()
  expect(within(workflow).getByRole('button', { name: /目标规划/ })).toBeTruthy()
})

it('keeps one original Optimize Loop and its compact round strip while aggregating custom round executions', async () => {
  const value = task([
    extension('PRE-1', 'optimize', 'preprocess', 'succeeded', 1, 1),
    step('OPT-1', 'optimize', 'succeeded', 2, 1),
    extension('PRE-2', 'optimize', 'preprocess', 'running', 3, 2),
    step('OPT-2', 'optimize', 'created', 4, 2),
  ])
  await renderTask(value)
  const workflow = screen.getByRole('heading', { name: '进化工作流' }).closest('section')!
  const custom = within(workflow).getByRole('button', { name: /轮内质量检查.*自定义/ })
  expect(within(custom).getByText('2 次执行')).toBeTruthy()
  expect(within(workflow).getAllByRole('button', { name: /优化 Loop/ })).toHaveLength(1)
  expect(within(workflow).getByText('优化轮次')).toBeTruthy()
  expect(within(workflow).getByRole('button', { name: /第 1 轮优化/ })).toBeTruthy()
  expect(within(workflow).getByRole('button', { name: /第 2 轮优化/ })).toBeTruthy()
  expect(within(workflow).queryByRole('group', { name: /第 .* 轮优化/ })).toBeNull()
})

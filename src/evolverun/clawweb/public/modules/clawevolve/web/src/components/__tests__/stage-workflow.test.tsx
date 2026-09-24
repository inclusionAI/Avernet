// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { EvolveStep, EvolveTask } from '../../api/client'
import Evolve from '../../pages/Evolve'

const api = vi.hoisted(() => ({ evolve: { capabilities: async () => ({ skillManagement: true, stageCustomization: true }),
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

it.each(['diagnose', 'hardening', 'plan', 'optimize'])('matches a native %s replacement to its actual running Step', async (stage) => {
  const value = task([{ ...step('CORE', stage, 'running', 1),
    coreImplementation: { stage, mode: 'replace', implementationId: 'IMPL', displayName: '本次业务实现' },
  } as EvolveStep], { stageTest: { stage, mode: 'replace' },
    stageExtensions: { [stage]: { replace: { enabled: true, implementationId: 'IMPL', displayName: '本次业务实现' } } } })
  value.task_type = 'stage_test'
  await renderTask(value)
  const workflow = screen.getByRole('heading', { name: 'Stage 集成测试流程' }).closest('section')!
  const card = within(workflow).getByRole('button', { name: /本次业务实现.*自定义/ })
  expect(card.hasAttribute('disabled')).toBe(false)
  expect(within(card).getByText('运行中')).toBeTruthy()
  expect(workflow.textContent).not.toContain('等待上一步')
  const record = document.getElementById('step-CORE')!
  expect(record.textContent).toContain('本次业务实现')
  expect(record.textContent).not.toContain('平台默认')
})

it('keeps each native hardening feedback round and its own results selectable', async () => {
  const steps = [1, 2, 3].map(round => ({ ...step(`CORE-${round}`, 'hardening', 'succeeded', round),
    coreImplementation: { stage: 'hardening', mode: 'replace', implementationId: 'IMPL', displayName: '业务加固' },
    stageLoop: { rootStepId: 'CORE-1', round, previousStepId: round === 1 ? null : `CORE-${round - 1}` },
    summary: `## 第${round}轮加固总结\n\n本轮完成`,
    output: { summary: `## 第${round}轮加固总结\n\n本轮完成`, changed: true, changed_files: [`round-${round}.md`] },
  } as EvolveStep))
  const value = task(steps)
  value.interactions = steps.flatMap((item, index) => [
    { interactionId: `HITL-${index}`, stepId: item.stepId, status: 'answered', question: { format: 'text', tag: 'confirm', content: `第${index + 1}轮执行前确认` }, answer: '确认' },
    { interactionId: `LOOP-${index}`, stepId: item.stepId, status: 'waiting', question: { kind: 'loop_feedback', prompt: `第${index + 1}轮结果反馈`, accepts: { text: true, files: [] } } },
  ]) as EvolveTask['interactions']
  value.task_type = 'hardening' as EvolveTask['task_type']
  await renderTask(value)
  for (const round of [1, 2, 3]) {
    const record = document.getElementById(`step-CORE-${round}`)!
    const result = within(record).getByRole('heading', { name: `第${round}轮加固总结` })
    expect(within(record).getAllByRole('heading', { name: `第${round}轮加固总结` })).toHaveLength(1)
    expect(Array.from(record.querySelectorAll('p')).some(p => p.textContent?.includes(`## 第${round}轮加固总结`))).toBe(false)
    const confirmation = within(record).getByText(`第${round}轮执行前确认`)
    const feedback = within(record).getByText(`第${round}轮结果反馈`)
    expect(confirmation.compareDocumentPosition(result) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(result.compareDocumentPosition(feedback) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(record.textContent).not.toContain(`round-${round === 3 ? 1 : round + 1}.md`)
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`第 ${round} 轮 · 业务加固`) }))
    const inspector = screen.getByText('节点运行详情').closest('.border-t')!
    expect(inspector.textContent).toContain(`CORE-${round}`)
    expect(within(inspector).queryByRole('heading', { name: `第${round}轮加固总结` })).toBeNull()
    expect(within(inspector).getByRole('link', { name: '查看本轮交互与结果' }).getAttribute('href')).toBe(`#step-CORE-${round}`)
    expect(inspector.textContent).not.toContain(`round-${round === 3 ? 1 : round + 1}.md`)
  }
})

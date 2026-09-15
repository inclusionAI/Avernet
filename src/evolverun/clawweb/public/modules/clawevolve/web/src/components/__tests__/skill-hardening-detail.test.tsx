// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { EvolveStep, EvolveTask } from '../../api/client'
import SkillHardeningDetail from '../SkillHardeningDetail'
import Evolve from '../../pages/Evolve'

const api = vi.hoisted(() => ({ evolve: { getTask: vi.fn(), listTaskLogArchives: vi.fn(), answerStageInteraction: vi.fn(), getStageSkill: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ authState: 'authenticated', user: { userId: 'owner' } }) }))
beforeEach(() => {
  vi.stubGlobal('React', React); vi.resetAllMocks()
  api.evolve.listTaskLogArchives.mockResolvedValue({ items: [] })
  api.evolve.answerStageInteraction.mockResolvedValue({ ok: true })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function fixture(): EvolveTask {
  const step = (stepId: string, implementationId: string, stage: string, mode: string, summary: string) => ({
    stepId, taskId: 'TASK', stepType: 'stage_extension', stepNo: 1, roundNo: null, status: 'succeeded', command: 'technical command',
    stageExtension: { implementationId, stage, mode, displayName: '注册名称' },
    output: { summary, changed_files: ['SKILL.md', 'scripts/check.py'], private_detail: '不应作为主要输出显示' },
  } as EvolveStep)
  return { task_id: 'TASK', task_type: 'diagnose', task_name: 'Hardening task', user_id: 'owner', bot_id: 'BOT', status: 'failed',
    error_message: '后续主流程失败',
    config: { presentation: { kind: 'skill_hardening', spaceId: 'TEAM-SPACE', hardeningImplementationId: 'HARDEN' } },
    steps: [step('PRE', 'HARDEN', 'diagnose', 'preprocess', '## 加固结论\n\n修复了 **输入校验**。'),
      { ...step('OTHER', 'OTHER', 'diagnose', 'preprocess', '其他实现结果'), status: 'waiting_context' },
      step('POST', 'HARDEN', 'diagnose', 'postprocess', '同名实现的后置不匹配'),
      step('PLAN', 'HARDEN', 'plan', 'preprocess', '规划前置不匹配'),
      { ...step('MAIN', 'none', 'diagnose', 'replace', '主流程明细'), stepType: 'diagnose', status: 'failed', error: { code: 'BROKEN', message: '诊断主流程失败', retryable: true } }],
    interactions: [{ interactionId: 'QUESTION', stepId: 'OTHER', status: 'waiting', question: { tag: 'scope', format: 'text', content: '其他步骤仍需回答' } }],
  } as EvolveTask
}

it('renders only the exact diagnose preprocess Markdown and file list', () => {
  const task = fixture()
  const view = render(<SkillHardeningDetail task={task} implementationId="HARDEN" renderStatus={(status) => status} />)
  expect(screen.getByRole('heading', { name: '加固结论' })).toBeTruthy()
  expect(view.container.querySelector('strong')?.textContent).toBe('输入校验')
  expect(screen.getByText('scripts/check.py')).toBeTruthy()
  expect(screen.queryByText('其他实现结果')).toBeNull()
  expect(screen.queryByText('同名实现的后置不匹配')).toBeNull()
  expect(screen.queryByText('规划前置不匹配')).toBeNull()
  expect(screen.queryByText('不应作为主要输出显示')).toBeNull()
  expect(screen.queryByRole('heading', { name: '进化工作流' })).toBeNull()
  expect(screen.queryByText('后续主流程失败')).toBeNull()
  expect(screen.queryByText('其他步骤仍需回答')).toBeNull()
})

it.each(['waiting_context', 'running', 'failed', 'canceled'] as const)('excludes matching %s output until succeeded, even when the Task later fails', (status) => {
  const task = fixture()
  task.steps![0].status = status
  task.interactions![0].stepId = 'PRE'
  const renderDetail = () => <SkillHardeningDetail task={task} implementationId="HARDEN" renderStatus={(value) => value} />
  const view = render(renderDetail())
  const results = within(screen.getByRole('region', { name: '诊断前置结果' }))
  expect(results.queryByRole('heading', { name: '加固结论' })).toBeNull()
  expect(results.queryByText('scripts/check.py')).toBeNull()
  expect(results.getByText('尚无可关联的诊断前置输出')).toBeTruthy()
  task.steps![0].status = 'succeeded'
  view.rerender(renderDetail())
  expect(results.getByRole('heading', { name: '加固结论' })).toBeTruthy()
  expect(results.getByText('scripts/check.py')).toBeTruthy()
})

it('keeps the original task detail shell for the frozen presentation policy and adds only the hardening result', async () => {
  api.evolve.getTask.mockResolvedValue(fixture())
  render(<MemoryRouter initialEntries={['/evolve/runs/TASK']}><Evolve /></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: 'Hardening task' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: '进化工作流' })).toBeTruthy()
  expect(screen.getAllByRole('button', { name: /注册名称/ }).length).toBeGreaterThan(0)
  expect(screen.getByRole('heading', { name: '加固结论' })).toBeTruthy()
  expect(screen.getByText('后续主流程失败')).toBeTruthy()
  expect(screen.getByText('BROKEN: 诊断主流程失败')).toBeTruthy()
  expect(screen.getAllByText('其他步骤仍需回答')).toHaveLength(1)
  expect(screen.getByRole('heading', { name: '执行记录' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: '任务配置' })).toBeTruthy()
  const hardeningStep = document.getElementById('step-PRE')!
  const hardeningResult = screen.getByRole('region', { name: '诊断前置结果' })
  const diagnoseStep = document.getElementById('step-MAIN')!
  expect(hardeningStep.contains(hardeningResult)).toBe(true)
  expect(Boolean(hardeningResult.compareDocumentPosition(diagnoseStep) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true)
  expect(screen.queryByText('自定义 STAGE 交付结果')).toBeNull()
  expect(screen.queryByRole('region', { name: '诊断工作流' })).toBeNull()
  expect(screen.queryByRole('heading', { name: '诊断 Stage' })).toBeNull()
})

it.each([undefined, { kind: 'other', spaceId: '97', hardeningImplementationId: 'HARDEN' },
  { kind: 'skill_hardening', spaceId: '97' }])('keeps the existing detail and generic form without a complete matching policy: %j', async (presentation) => {
  const task = fixture()
  task.config = { presentation, targetSpaceId: '97' }
  api.evolve.getTask.mockResolvedValue(task)
  render(<MemoryRouter initialEntries={['/evolve/runs/TASK']}><Evolve /></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: '执行记录' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: '进化工作流' })).toBeTruthy()
  expect(screen.getByText('其他步骤仍需回答')).toBeTruthy()
  expect(screen.queryByRole('region', { name: '诊断前置结果' })).toBeNull()
})

it('does not guess a missing binding from output metadata', () => {
  const task = fixture()
  task.steps = [{ ...task.steps![0], stageExtension: null } as EvolveStep]
  task.steps[0].output = { implementation: { implementationId: 'HARDEN' }, summary: '不可猜测关联' }
  render(<SkillHardeningDetail task={task} implementationId="HARDEN" renderStatus={(status) => status} />)
  expect(screen.getByText('尚无可关联的诊断前置输出')).toBeTruthy()
  expect(screen.queryByText('不可猜测关联')).toBeNull()
})

it('renders untrusted Markdown without active HTML or image loads and does not stringify invalid file lists', () => {
  const task = fixture()
  task.steps = [task.steps![0]]
  task.steps[0].output = { summary: '# 结果\n<script>alert(1)</script>\n\n![远程图片](https://example.invalid/tracker.png)\n\n[链接](javascript:alert(1))', changed_files: { unexpected: '不可展示为文件' } }
  const view = render(<SkillHardeningDetail task={task} implementationId="HARDEN" renderStatus={(status) => status} />)
  expect(screen.getByRole('heading', { name: '结果' })).toBeTruthy()
  expect(view.container.querySelector('script,img,[href^="javascript:"]')).toBeNull()
  expect(screen.getByText('尚无可展示的 changed_files 列表')).toBeTruthy()
  expect(screen.queryByText('不可展示为文件')).toBeNull()
})

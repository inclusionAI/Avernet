// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SkillEvolutionFields from '../SkillEvolutionFields'

const api = vi.hoisted(() => ({ evolve: { stageCatalog: vi.fn(), listStageSkills: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
beforeEach(() => {
  vi.stubGlobal('React', React)
  api.evolve.stageCatalog.mockResolvedValue({
    flows: [{ key: 'skill_evolution', stages: [{ key: 'plan', enabled: true, canDisable: false, disabledReason: '必须形成优化方案' }] }],
    stages: [{ stage: 'plan', name: '规划', description: '制定优化方案', extensionModes: ['preprocess', 'postprocess', 'replace'] }],
  })
  api.evolve.listStageSkills.mockResolvedValue({ items: [] })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks() })

it('keeps customization usable without implementations and offers an entry from the empty list', async () => {
  render(<MemoryRouter><SkillEvolutionFields botId="bot" includeTargetSkill={false} assetId=""
    onAssetIdChange={() => {}} extensions={{}} onExtensionsChange={() => {}}
    stageSelection={{ diagnose: true, plan: true, optimize: true }} onStageSelectionChange={() => {}}
    fullTask flowKey="skill_evolution" inputMode="diagnose_goal" hasGoal={false} section="extensions" /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: /自定义 Stage 处理/ }))
  const toggle = await screen.findByRole('checkbox', { name: '自定义' })
  expect((toggle as HTMLInputElement).disabled).toBe(false)
  expect(screen.queryByRole('checkbox', { name: '启用' })).toBeNull()
  expect(screen.getByText('必选')).toBeTruthy()
  fireEvent.click(toggle)
  expect(await screen.findByText('暂无可用的自定义实现')).toBeTruthy()
  expect(screen.getByRole('link', { name: '去接入自定义实现 ↗' }).getAttribute('href')).toBe('/evolve/stage-skills/new')
  expect((screen.getByRole('button', { name: '＋ 选择实现' }) as HTMLButtonElement).disabled).toBe(false)
})

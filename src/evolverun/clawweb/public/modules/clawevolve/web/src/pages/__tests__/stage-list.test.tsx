// @vitest-environment jsdom
import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import StageSkillManagement from '../StageSkillManagement'

const api = vi.hoisted(() => ({ evolve: { listStageSkills: vi.fn(), listStageDevelopments: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('sorts drafts and uploaded families together newest first, and fixes the action column', async () => {
  vi.stubGlobal('React', React)
  const common = { ownerId: 'owner', stage: 'diagnose', stageName: '诊断', mode: 'preprocess' }
  api.evolve.listStageSkills.mockResolvedValue({ items: [
    { ...common, stageSkillId: 'old', implementationId: 'old-v2', displayName: '已有实现', version: 'v2', status: 'registered', integrationTestStatus: 'test_passed', updatedAt: 100 },
  ] })
  api.evolve.listStageDevelopments.mockResolvedValue({ items: [
    { ...common, stageSkillId: 'new', displayName: '新建开发记录', updatedAt: 200 },
  ] })
  render(<MemoryRouter><StageSkillManagement /></MemoryRouter>)
  await screen.findByText('新建开发记录')
  const rows = screen.getAllByRole('row')
  expect(within(rows[1]).getByText('新建开发记录')).toBeTruthy()
  expect(within(rows[2]).getByText('v2')).toBeTruthy()
  expect(within(rows[1]).getByText('诊断').className).toContain('border-amber-200')
  expect(screen.getByRole('columnheader', { name: '操作' }).className).toContain('sticky')
  expect(rows[1].lastElementChild?.className).toContain('right-0')
})

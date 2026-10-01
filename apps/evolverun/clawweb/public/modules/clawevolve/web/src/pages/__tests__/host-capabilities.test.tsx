// @vitest-environment jsdom
import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Evolve from '../Evolve'
const api = vi.hoisted(() => ({ evolve: { capabilities: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ authState: 'ready', user: { userId: 'owner' } }) }))
vi.mock('../../features/evolve/admin-scope', () => ({
  EvolveAdminScopeProvider: ({ children }: { children: React.ReactNode }) => children,
  useEvolveAdminScope: () => ({ available: false, ownerUserIds: [] }),
}))
vi.mock('../SkillCenter', () => ({ default: () => <div>Skill page mounted</div> }))
vi.mock('../StageSkillManagement', () => ({ default: () => <div>Stage page mounted</div> }))
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks() })

it.each(['/evolve/skills', '/evolve/stage-skills'])('hides missing capabilities and blocks direct page %s', async path => {
  vi.stubGlobal('React', React)
  api.evolve.capabilities.mockResolvedValue({ skillManagement: false, stageCustomization: false })
  render(<MemoryRouter initialEntries={[path]}><Evolve version="openversion" /></MemoryRouter>)
  await waitFor(() => expect(api.evolve.capabilities).toHaveBeenCalledOnce())
  expect(screen.queryByText('技能中心')).toBeNull()
  expect(screen.queryByText('自定义 Stage')).toBeNull()
  expect(screen.queryByText(/page mounted/)).toBeNull()
  expect(screen.getByText('进化任务')).toBeTruthy()
  expect(screen.getByText('当前宿主未启用此能力。')).toBeTruthy()
})
it('uses actual Host capabilities, not product version, to enable the page', async () => {
  vi.stubGlobal('React', React)
  api.evolve.capabilities.mockResolvedValue({ skillManagement: true, stageCustomization: true })
  render(<MemoryRouter initialEntries={['/evolve/skills']}><Evolve version="openversion" /></MemoryRouter>)
  expect(await screen.findByText('Skill page mounted')).toBeTruthy()
  expect(screen.getByText('技能中心')).toBeTruthy()
  expect(screen.getByText('自定义 Stage')).toBeTruthy()
})

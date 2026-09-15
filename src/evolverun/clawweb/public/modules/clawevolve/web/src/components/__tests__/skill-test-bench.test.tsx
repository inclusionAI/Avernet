// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SkillEventLog from '../SkillEventLog'
import { PackManagement } from '../../pages/evolve/PackManagement'

const api = vi.hoisted(() => ({ evolve: { listSkillEvents: vi.fn(), listVersions: vi.fn() }, tclog: { bots: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ user: { userId: 'owner' } }) }))
const event = { eventId: 'EVENT', assetId: 'ASSET', name: 'Evidence Skill', ownerId: 'owner', botId: 'BOT',
  type: 'optimization', status: 'waiting_acceptance', outcome: null, actorId: null, actorType: 'system', taskId: 'TASK',
  versionFrom: { versionId: 'V1', version: 'v1' }, versionTo: null, waitingInteractionId: null, summary: '等待确认候选版本',
  startedAt: 1789060001, completedAt: null, updatedAt: 1789060001 }
beforeEach(() => { vi.stubGlobal('React', React); vi.resetAllMocks(); api.tclog.bots.mockResolvedValue({ bots: [] }) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it.each([
  { baseline: .5, candidate: .75, delta: .25, text: '+0.2500', tone: 'text-emerald-600' },
  { baseline: .5, candidate: .25, delta: -.25, text: '-0.2500', tone: 'text-red-600' },
  { baseline: 0, candidate: 0, delta: 0, text: '+0.0000', tone: 'text-gray-400' },
])('shows the same actual comparison in versions and events: $text', async ({ baseline, candidate, delta, text, tone }) => {
  const comparison = { name: 'test_score', baseline, candidate, delta }
  api.evolve.listVersions.mockResolvedValue({ items: [{ versionId: 'ROUND', kind: 'round', acceptanceStatus: 'unassessed',
    botId: 'BOT', taskId: 'TASK', taskName: 'Bench task', stepId: 'OPTIMIZE', round: 1, scoreComparison: comparison }] })
  api.evolve.listSkillEvents.mockResolvedValue({ items: [{ ...event, testBench: { taskId: 'TASK', stepId: 'OPTIMIZE', round: 1, scoreComparison: comparison } }] })
  const versionView = render(<MemoryRouter><PackManagement /></MemoryRouter>)
  const versionDelta = await screen.findByText(text)
  const versionCell = versionDelta.closest('td')!.innerHTML
  expect(versionDelta.className).toContain(tone)
  versionView.unmount()
  render(<MemoryRouter><SkillEventLog /></MemoryRouter>)
  await screen.findByText('Evidence Skill')
  expect(screen.getByRole('columnheader', { name: 'Test Bench' })).toBeTruthy()
  expect(screen.getByText(text).closest('td')!.innerHTML).toBe(versionCell)
})

it('distinguishes unrecorded history, an unscored producer, and partial scores without fake zero', async () => {
  api.evolve.listSkillEvents.mockResolvedValue({ items: [event,
    { ...event, eventId: 'NO-SCORE', testBench: { taskId: 'TASK', stepId: 'OPTIMIZE', round: 1, scoreComparison: null } },
    { ...event, eventId: 'PARTIAL', testBench: { taskId: 'TASK', stepId: 'OPTIMIZE', round: 1,
      scoreComparison: { name: 'test_score', baseline: null, candidate: .75, delta: null } } },
  ] })
  render(<MemoryRouter><SkillEventLog /></MemoryRouter>)
  await screen.findByText('未记录评测关联')
  expect(screen.getByText('未评测')).toBeTruthy()
  const partial = screen.getByText('0.75').closest('td')!
  expect(within(partial).getAllByText('—')).toHaveLength(2)
  expect(screen.queryByText('+0.0000')).toBeNull()
  expect(screen.queryByText('0')).toBeNull()
})

// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import SkillCenter from '../SkillCenter'
import SkillDetail from '../SkillDetail'
import SkillEventLog from '../../components/SkillEventLog'

const api = vi.hoisted(() => ({ evolve: { listSpaces: vi.fn(), listSkillAssets: vi.fn(), listSkillEvents: vi.fn(), getSkillAsset: vi.fn(), getSkillVersionContent: vi.fn(), getSkillVersionDiff: vi.fn() }, tclog: { bots: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ user: { userId: 'viewer' } }) }))
const asset = { assetId: 'ASSET-1', name: 'Evidence Skill', description: 'Diagnose actual session evidence.', ownerId: 'owner', botId: 'BOT-1', skillId: '47', currentVersion: 'v2', updatedAt: 1789060000, versions: [{ versionId: 'VERSION-2', version: 'v2' }, { versionId: 'VERSION-1', version: 'v1' }] }
function Location() { const location = useLocation(); return <output>{location.pathname}</output> }
beforeEach(() => {
  vi.stubGlobal('React', React); vi.resetAllMocks()
  api.evolve.listSpaces.mockResolvedValue({ items: [] })
  api.evolve.listSkillAssets.mockResolvedValue({ items: [asset] })
  api.tclog.bots.mockResolvedValue({ bots: [{ botId: 'BOT-1', botName: 'Evidence Bot' }] })
  api.evolve.listSkillEvents.mockResolvedValue({ items: [] })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Skill center asset list and recorded events', () => {
  it('keeps the registration form without the internal snapshot explanation', async () => {
    render(<MemoryRouter><SkillCenter /></MemoryRouter>)
    await screen.findByText('Evidence Skill')
    fireEvent.click(screen.getByRole('button', { name: '登记 Skill' }))
    expect(screen.getByRole('heading', { name: '登记 Bot 中已有 Skill' })).toBeTruthy()
    expect(screen.getAllByRole('combobox')).toHaveLength(3)
    expect(screen.getByRole('button', { name: '登记' })).toBeTruthy()
    expect(screen.queryByText('平台从 OCB 读取完整 Skill，并保存登记时的 v1 冻结版本。')).toBeNull()
    expect(screen.getByRole('combobox', { name: '所属 Bot' }).closest('label')?.className).toContain('block')
    expect(screen.getByRole('combobox', { name: 'Bot 中自己上传的 Skill' }).closest('label')?.className).toContain('block')
  })

  it('shows one row per Skill with real metadata and no list version selector', async () => {
    render(<MemoryRouter><SkillCenter /><Location /></MemoryRouter>)
    await screen.findByText('Evidence Skill')
    expect(screen.getByText('Diagnose actual session evidence.')).toBeTruthy()
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(2)
    expect(within(rows[1]).getByText('owner')).toBeTruthy()
    expect(within(rows[1]).getByText('Evidence Bot')).toBeTruthy()
    expect(within(rows[1]).getByText('已登记')).toBeTruthy()
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(within(rows[1]).getByText('v2')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '技能事件日志' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '查看' }))
    expect(screen.getByText('/evolve/skills/ASSET-1')).toBeTruthy()
  })

  it('leaves missing description and owner unknown instead of inventing them', async () => {
    api.evolve.listSkillAssets.mockResolvedValue({ items: [{ ...asset, description: null, ownerId: undefined }] })
    render(<MemoryRouter><SkillCenter /></MemoryRouter>)
    await screen.findByText('Evidence Skill')
    expect(screen.queryByText('Diagnose actual session evidence.')).toBeNull()
    expect(screen.queryByText('viewer')).toBeNull()
    expect(screen.getByText('—')).toBeTruthy()
  })

  it('filters actual metadata and paginates while keeping versions read-only', async () => {
    api.evolve.listSkillAssets.mockResolvedValue({ items: Array.from({ length: 21 }, (_, index) => ({ ...asset, assetId: `A-${index}`, name: `Skill ${index}` })) })
    render(<MemoryRouter><SkillCenter /></MemoryRouter>)
    await screen.findByText('Skill 0')
    expect(screen.getAllByRole('row')).toHaveLength(21)
    expect(screen.queryByText('Skill 20')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText('Skill 20')).toBeTruthy()
    fireEvent.change(screen.getByRole('textbox', { name: '搜索技能' }), { target: { value: 'Skill 3' } })
    expect(screen.getAllByRole('row')).toHaveLength(2)
    expect(screen.getByText('Skill 3')).toBeTruthy()
    expect(screen.getByText('v2')).toBeTruthy()
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('shows persisted audit results and actors with exact asset or task links', async () => {
    api.evolve.listSkillEvents.mockResolvedValue({ items: [
      { eventId: 'AUDIT-DIAGNOSE', assetId: 'ASSET-1', name: 'Evidence Skill', ownerId: 'owner', botId: 'BOT-1', version: 'v2', type: 'diagnosis_started', actorId: 'diagnoser', actorType: 'user', result: 'pending', taskId: 'TASK-DIAGNOSE', createdAt: 1789060002 },
      { eventId: 'AUDIT-END', assetId: 'ASSET-1', name: 'Evidence Skill', description: 'OCB event skill description', ownerId: 'owner', botId: 'BOT-1', version: null, type: 'evolution_finished', actorId: null, actorType: 'system', result: 'not_improved', taskId: 'TASK-1', createdAt: 1789060001 },
      { eventId: 'AUDIT-REGISTER', assetId: 'ASSET-1', name: 'Evidence Skill', ownerId: 'owner', botId: 'BOT-1', version: 'v1', type: 'registered', actorId: 'registrar', actorType: 'user', result: 'succeeded', taskId: null, createdAt: 1789060000 },
    ] })
    render(<MemoryRouter><SkillEventLog /></MemoryRouter>)
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(4))
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(4)
    expect(screen.getAllByRole('columnheader')[0].textContent).toBe('技能名称')
    expect(screen.getByText('OCB event skill description')).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: '操作' }).className).toContain('sticky')
    expect(within(rows[1]).getByRole('link', { name: '查看' }).getAttribute('href')).toBe('/evolve/runs/TASK-DIAGNOSE')
    const diagnosisBadge = within(rows[1]).getByText('发起技能诊断')
    const pendingBadge = within(rows[1]).getByText('已发起')
    const versionBadge = within(rows[1]).getByText('v2')
    for (const token of ['rounded-full', 'bg-amber-50', 'text-amber-700']) expect(diagnosisBadge.className).toContain(token)
    for (const token of ['rounded-full', 'bg-blue-50', 'text-blue-700']) expect(pendingBadge.className).toContain(token)
    for (const token of ['rounded-md', 'font-mono']) expect(versionBadge.className).toContain(token)
    expect(within(rows[1]).getByText('owner').className).toContain('font-mono')
    expect(within(rows[1]).getByText('BOT-1').className).toContain('rounded-md')
    expect(within(rows[2]).getByRole('link', { name: '查看' }).getAttribute('href')).toBe('/evolve/runs/TASK-1')
    expect(within(rows[3]).getByRole('link', { name: '查看' }).getAttribute('href')).toBe('/evolve/skills/ASSET-1')
    expect(within(rows[3]).getByText('登记 Skill')).toBeTruthy()
    expect(within(rows[2]).getByText('未提升')).toBeTruthy()
    expect(within(rows[2]).getByText('系统')).toBeTruthy()
    expect(within(rows[3]).getByText('registrar')).toBeTruthy()
    expect(screen.queryByText('拒绝候选版本')).toBeNull()
    expect(screen.queryByText(/提分|优化成功/)).toBeNull()
  })

  it('distinguishes empty recorded history from an event API failure', async () => {
    const view = render(<MemoryRouter><SkillEventLog /></MemoryRouter>)
    await screen.findByText('暂无技能事件记录。')
    view.unmount()
    api.evolve.listSkillEvents.mockRejectedValue(new Error('Event store unavailable'))
    render(<MemoryRouter><SkillEventLog /></MemoryRouter>)
    await screen.findByText('Event store unavailable')
    expect(screen.queryByText('暂无技能事件记录。')).toBeNull()
  })

  it('retains exact version selection in the Skill detail, not the list', async () => {
    api.evolve.getSkillAsset.mockResolvedValue(asset)
    api.evolve.getSkillVersionContent.mockImplementation(async (_assetId, versionId) => ({ files: [{ path: 'SKILL.md', text: true }], selected: { path: 'SKILL.md', content: `Frozen ${versionId}` } }))
    api.evolve.getSkillVersionDiff.mockResolvedValue({ baseline: null, files: [] })
    render(<MemoryRouter initialEntries={['/evolve/skills/ASSET-1']}><SkillDetail /></MemoryRouter>)
    await screen.findByText('Frozen VERSION-2')
    fireEvent.change(screen.getByRole('combobox', { name: '版本' }), { target: { value: 'VERSION-1' } })
    await screen.findByText('Frozen VERSION-1')
    await waitFor(() => expect(api.evolve.getSkillVersionContent).toHaveBeenCalledWith('ASSET-1', 'VERSION-1'))
  })

  it('offers Git Diff by default and full comparison only in Skill detail', async () => {
    api.evolve.getSkillAsset.mockResolvedValue(asset)
    api.evolve.getSkillVersionContent.mockResolvedValue({ files: [{ path: 'SKILL.md', text: true }], selected: { path: 'SKILL.md', content: 'Current content' } })
    api.evolve.getSkillVersionDiff.mockResolvedValue({ baseline: { version: 'v1' }, files: [
      { path: 'SKILL.md', change: 'modified', before: 'shared\nOld guidance', after: 'shared\nNew guidance' },
    ] })
    render(<MemoryRouter initialEntries={['/evolve/skills/ASSET-1']}><SkillDetail /></MemoryRouter>)
    await screen.findByText('Current content')
    fireEvent.click(screen.getByRole('button', { name: '与本次进化前对比' }))

    expect(screen.getByRole('button', { name: 'Git Diff' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: '完整对比' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByText('Old guidance').parentElement?.textContent).toContain('-Old guidance')
    expect(screen.getByText('New guidance').parentElement?.textContent).toContain('+New guidance')
    expect(screen.queryByText('进化前')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '完整对比' }))
    expect(screen.getByRole('button', { name: '完整对比' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('进化前')).toBeTruthy()
    expect(screen.getByText('当前版本')).toBeTruthy()
  })
})

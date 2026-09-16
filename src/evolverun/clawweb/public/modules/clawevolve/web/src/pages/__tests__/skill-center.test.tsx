// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import SkillCenter from '../SkillCenter'
import SkillDetail from '../SkillDetail'
import SkillEventLog from '../../components/SkillEventLog'

const api = vi.hoisted(() => ({ evolve: { listSpaces: vi.fn(), listSkillAssets: vi.fn(), listSkillEvents: vi.fn(), getSkillAsset: vi.fn(), getSkillAssetHistory: vi.fn(), getSkillVersionContent: vi.fn(), getSkillVersionDiff: vi.fn() }, tclog: { bots: vi.fn() } }))
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
  api.evolve.getSkillAssetHistory.mockResolvedValue({ events: [] })
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

  it('shows one business event per task with HITL action and exact Skill detail selection', async () => {
    api.evolve.listSkillEvents.mockResolvedValue({ items: [
      { eventId: 'EVENT-DIAGNOSE', assetId: 'ASSET-1', name: 'Evidence Skill', ownerId: 'owner', botId: 'BOT-1', type: 'diagnosis', status: 'waiting_user_input', outcome: null, taskId: 'TASK-DIAGNOSE', actorId: 'diagnoser', actorType: 'user', versionFrom: { versionId: 'VERSION-2', version: 'v2' }, versionTo: null, waitingInteractionId: 'HITL-1', summary: '等待确认诊断范围', startedAt: 1789060002, completedAt: null, updatedAt: 1789060003, testBench: null },
      { eventId: 'EVENT-OPTIMIZE', assetId: 'ASSET-1', name: 'Evidence Skill', description: 'OCB event skill description', ownerId: 'owner', botId: 'BOT-1', type: 'optimization', status: 'completed', outcome: '指标提升', taskId: 'TASK-1', actorId: null, actorType: 'system', versionFrom: { versionId: 'VERSION-1', version: 'v1' }, versionTo: { versionId: 'VERSION-2', version: 'v2' }, waitingInteractionId: null, summary: '完成两轮优化', startedAt: 1789060001, completedAt: 1789060010, updatedAt: 1789060010, testBench: null },
      { eventId: 'EVENT-REGISTER', assetId: 'ASSET-1', name: 'Evidence Skill', ownerId: 'owner', botId: 'BOT-1', type: 'registered', status: 'completed', outcome: '登记成功', taskId: null, actorId: 'registrar', actorType: 'user', versionFrom: null, versionTo: { versionId: 'VERSION-1', version: 'v1' }, waitingInteractionId: null, summary: '登记初始版本', startedAt: 1789060000, completedAt: 1789060000, updatedAt: 1789060000, testBench: null },
    ] })
    render(<MemoryRouter initialEntries={['/evolve/skills/events']}><SkillEventLog /></MemoryRouter>)
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(4))
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(4)
    expect(screen.getAllByRole('columnheader')[0].textContent).toBe('技能名称')
    expect(screen.getByText('OCB event skill description')).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: '操作' }).className).toContain('sticky')
    expect(within(rows[1]).getByRole('link', { name: '查看' }).getAttribute('href')).toBe('/evolve/runs/TASK-DIAGNOSE?returnTo=%2Fevolve%2Fskills%2Fevents')
    expect(within(rows[1]).getByRole('link', { name: '去处理' }).getAttribute('href')).toBe('/evolve/runs/TASK-DIAGNOSE?returnTo=%2Fevolve%2Fskills%2Fevents')
    const diagnosisBadge = within(rows[1]).getByText('诊断')
    const pendingBadge = within(rows[1]).getByText('等待用户输入')
    const versionBadge = within(rows[1]).getByText('v2')
    for (const token of ['rounded-full', 'bg-amber-50', 'text-amber-700']) expect(diagnosisBadge.className).toContain(token)
    for (const token of ['rounded-full', 'bg-amber-100', 'text-amber-900']) expect(pendingBadge.className).toContain(token)
    for (const token of ['rounded-md', 'font-mono']) expect(versionBadge.className).toContain(token)
    expect(within(rows[1]).getByText('owner').className).toContain('font-mono')
    expect(within(rows[2]).getByRole('link', { name: '查看' }).getAttribute('href')).toBe('/evolve/runs/TASK-1?returnTo=%2Fevolve%2Fskills%2Fevents')
    expect(within(rows[3]).getByRole('link', { name: '查看' }).getAttribute('href')).toBe('/evolve/skills/ASSET-1?selected=version%3AVERSION-1&view=content&backTo=%2Fevolve%2Fskills%2Fevents')
    expect(within(rows[3]).getByText('登记')).toBeTruthy()
    expect(within(rows[2]).getByText('v1 → v2')).toBeTruthy()
    expect(within(rows[2]).getByText('系统发起')).toBeTruthy()
    expect(within(rows[3]).getByText('发起人 registrar')).toBeTruthy()
    expect(screen.queryByText('优化开始')).toBeNull()
    expect(screen.queryByText('优化结束')).toBeNull()
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

  it('uses the timeline as exact version selection in the Skill detail', async () => {
    api.evolve.getSkillAsset.mockResolvedValue(asset)
    api.evolve.getSkillVersionContent.mockImplementation(async (_assetId, versionId) => ({ files: [{ path: 'SKILL.md', text: true }], selected: { path: 'SKILL.md', content: `Frozen ${versionId}` } }))
    api.evolve.getSkillVersionDiff.mockResolvedValue({ baseline: null, files: [] })
    render(<MemoryRouter initialEntries={['/evolve/skills/ASSET-1']}><SkillDetail /></MemoryRouter>)
    await screen.findByText('Frozen VERSION-2')
    fireEvent.click(screen.getByRole('button', { name: /^v1/ }))
    await screen.findByText('Frozen VERSION-1')
    await waitFor(() => expect(api.evolve.getSkillVersionContent).toHaveBeenCalledWith('ASSET-1', 'VERSION-1'))
  })

  it('orders versions and their producing events as one newest-first timeline', async () => {
    const versionedAsset = {
      ...asset,
      currentVersion: 'v3',
      versions: [
        { versionId: 'VERSION-3', version: 'v3', createdAt: 1789527832 },
        { versionId: 'VERSION-2', version: 'v2', createdAt: 1789405465 },
        { versionId: 'VERSION-1', version: 'v1', createdAt: 1789378091 },
      ],
    }
    api.evolve.getSkillAsset.mockResolvedValue(versionedAsset)
    api.evolve.getSkillAssetHistory.mockResolvedValue({ events: [{
      eventId: 'HARDEN-3', assetId: 'ASSET-1', name: 'Evidence Skill', ownerId: 'owner', botId: 'BOT-1',
      type: 'hardening', status: 'completed', outcome: 'applied', taskId: 'TASK-HARDEN-3', actorId: 'owner', actorType: 'user',
      versionFrom: { versionId: 'VERSION-2', version: 'v2' }, versionTo: { versionId: 'VERSION-3', version: 'v3' },
      waitingInteractionId: null, summary: '完成 v2 到 v3 加固', startedAt: 1789526428, completedAt: 1789527832, updatedAt: 1789527832, testBench: null,
    }] })
    api.evolve.getSkillVersionContent.mockResolvedValue({ files: [{ path: 'SKILL.md', text: true }], selected: { path: 'SKILL.md', content: 'Current content' } })
    api.evolve.getSkillVersionDiff.mockResolvedValue({ baseline: { version: 'v2' }, files: [] })

    render(<MemoryRouter initialEntries={['/evolve/skills/ASSET-1']}><SkillDetail /></MemoryRouter>)

    const timeline = await screen.findByLabelText('Skill 迭代时间线')
    const v3 = within(timeline).getByRole('button', { name: /v3.*当前版本/ })
    const hardening = within(timeline).getByRole('button', { name: /加固.*完成 v2 到 v3 加固.*v2 → v3/ })
    const v2 = within(timeline).getByRole('button', { name: /^v2/ })
    expect(v3.compareDocumentPosition(hardening) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(hardening.compareDocumentPosition(v2) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('offers Git Diff by default and full comparison only in Skill detail', async () => {
    api.evolve.getSkillAsset.mockResolvedValue(asset)
    api.evolve.getSkillVersionContent.mockResolvedValue({ files: [{ path: 'SKILL.md', text: true }], selected: { path: 'SKILL.md', content: 'Current content' } })
    api.evolve.getSkillVersionDiff.mockResolvedValue({ baseline: { version: 'v1' }, files: [
      { path: 'SKILL.md', change: 'modified', before: 'shared\nOld guidance', after: 'shared\nNew guidance' },
    ] })
    render(<MemoryRouter initialEntries={['/evolve/skills/ASSET-1']}><SkillDetail /></MemoryRouter>)
    await screen.findByText('Current content')
    fireEvent.click(screen.getByRole('button', { name: '版本 Diff' }))

    expect(screen.getByRole('button', { name: 'Git Diff' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: '完整对比' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByText('Old guidance').parentElement?.textContent).toContain('-Old guidance')
    expect(screen.getByText('New guidance').parentElement?.textContent).toContain('+New guidance')
    expect(screen.queryByText('进化前')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '完整对比' }))
    expect(screen.getByRole('button', { name: '完整对比' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('进化前')).toBeTruthy()
    expect(screen.getAllByText('当前版本')).toHaveLength(2)
  })

  it('mixes multiple task records under one version and restores a selected HITL task from query', async () => {
    api.evolve.getSkillAsset.mockResolvedValue(asset)
    api.evolve.getSkillAssetHistory.mockResolvedValue({ events: [
      { eventId: 'DIAG-1', assetId: 'ASSET-1', name: 'Evidence Skill', ownerId: 'owner', botId: 'BOT-1', type: 'diagnosis', status: 'completed', outcome: '发现一个问题', taskId: 'TASK-OLD', actorId: 'owner', actorType: 'user', versionFrom: { versionId: 'VERSION-2', version: 'v2' }, versionTo: null, waitingInteractionId: null, summary: '历史诊断', startedAt: 1789050000, completedAt: 1789050100, updatedAt: 1789050100, testBench: null },
      { eventId: 'DIAG-2', assetId: 'ASSET-1', name: 'Evidence Skill', ownerId: 'owner', botId: 'BOT-1', type: 'diagnosis', status: 'waiting_user_input', outcome: null, taskId: 'TASK-WAIT', actorId: 'owner', actorType: 'user', versionFrom: { versionId: 'VERSION-2', version: 'v2' }, versionTo: null, waitingInteractionId: 'HITL', summary: '加固信息待确认', startedAt: 1789060000, completedAt: null, updatedAt: 1789060010, testBench: null },
    ] })
    api.evolve.getSkillVersionContent.mockResolvedValue({ files: [{ path: 'SKILL.md', text: true }], selected: { path: 'SKILL.md', content: 'Current content' } })
    api.evolve.getSkillVersionDiff.mockResolvedValue({ baseline: { version: 'v1' }, files: [] })
    render(<MemoryRouter initialEntries={['/evolve/skills/ASSET-1?selected=task%3ATASK-WAIT&view=task&backTo=%2Fevolve%2Fskills%2Fevents']}><SkillDetail /></MemoryRouter>)
    await screen.findByRole('heading', { name: '加固信息待确认' })
    expect(screen.getByRole('button', { name: '← 返回技能事件日志' })).toBeTruthy()
    expect(screen.getByText('2 个版本 · 2 次诊断 · 0 次加固 · 0 次优化')).toBeTruthy()
    expect(screen.getByText('历史诊断')).toBeTruthy()
    expect(screen.getAllByText('等待用户输入').length).toBeGreaterThan(1)
    const fullRecord = screen.getByRole('link', { name: '查看完整执行记录 ↗' })
    expect(fullRecord.getAttribute('href')).toContain('/evolve/runs/TASK-WAIT?returnTo=')
    expect(decodeURIComponent(fullRecord.getAttribute('href') ?? '')).toContain('/evolve/skills/ASSET-1?selected=task%3ATASK-WAIT&view=task')
  })
})

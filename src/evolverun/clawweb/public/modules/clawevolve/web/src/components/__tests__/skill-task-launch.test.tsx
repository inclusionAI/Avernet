// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import SkillTaskLaunchDialog, { type SkillTaskAction } from '../SkillTaskLaunchDialog'
import SkillCenter from '../../pages/SkillCenter'
import SkillDetail from '../../pages/SkillDetail'

const api = vi.hoisted(() => ({ evolve: {
  getSkillTaskDefaults: vi.fn(), createTask: vi.fn(), listSkillAssets: vi.fn(), getSkillAsset: vi.fn(),
  getSkillVersionContent: vi.fn(), getSkillVersionDiff: vi.fn(),
}, tclog: { bots: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ user: { userId: 'viewer-not-owner' } }) }))

const asset = { assetId: 'asset / 1', botId: 'bot-1', name: '我的技能', skillId: 'skill-1', currentVersion: 'v1', updatedAt: 1789060000 }
const defaults = { assetId: asset.assetId, botId: asset.botId, userId: 'original-owner',
  diagnose: { taskType: 'diagnose', goal: '检查该技能的表现' },
  optimize: { taskType: 'full', goal: '优化该技能的表现', unavailableReason: null,
    stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: 'hardened-97-v3' } } } } }

function Location() { const location = useLocation(); return <output data-testid="location">{location.pathname + location.search}</output> }
function open(action: SkillTaskAction = 'diagnose', onClose = vi.fn()) {
  return render(<MemoryRouter><SkillTaskLaunchDialog asset={asset} action={action} onClose={onClose} /><Location /></MemoryRouter>)
}

beforeEach(() => {
  vi.stubGlobal('React', React)
  vi.resetAllMocks()
  api.evolve.getSkillTaskDefaults.mockResolvedValue(defaults)
  api.evolve.createTask.mockResolvedValue({ task_id: 'task-1', status: 'pending' })
  api.evolve.listSkillAssets.mockResolvedValue({ items: [asset] })
  api.evolve.getSkillAsset.mockResolvedValue(asset)
  api.tclog.bots.mockResolvedValue({ bots: [] })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Skill task launch confirmation', () => {
  it('opens and confirms on hosts where crypto.randomUUID is unavailable', async () => {
    vi.stubGlobal('crypto', {})
    open()
    await screen.findByText(defaults.diagnose.goal)
    fireEvent.click(screen.getByRole('button', { name: '确认诊断' }))
    await waitFor(() => expect(api.evolve.createTask).toHaveBeenCalledTimes(1))
    expect(api.evolve.createTask.mock.calls[0][1]).toMatch(/^skill-task-/)
  })

  it.each(['diagnose', 'optimize'] as const)('requires confirmation and submits %s with the real owner and fixed target', async (action) => {
    open(action)
    const dialog = screen.getByRole('dialog')
    expect(api.evolve.createTask).not.toHaveBeenCalled()
    expect((within(dialog).getByRole('button', { name: /确认诊断|确认优化/ }) as HTMLButtonElement).disabled).toBe(true)
    await within(dialog).findByText(defaults[action].goal)
    expect(within(dialog).getByText('original-owner')).toBeTruthy()
    expect(within(dialog).queryByText('viewer-not-owner')).toBeNull()
    expect(within(dialog).getByText(/GLM-5.2/)).toBeTruthy()
    expect(within(dialog).queryByRole('combobox')).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: action === 'diagnose' ? '确认诊断' : '确认优化' }))
    await waitFor(() => expect(api.evolve.createTask).toHaveBeenCalledTimes(1))
    const [input, key] = api.evolve.createTask.mock.calls[0]
    expect(input).toMatchObject({ taskType: action === 'diagnose' ? 'diagnose' : 'full',
      targetSkillAssetId: asset.assetId, botId: 'bot-1', userId: 'original-owner', goal: defaults[action].goal,
      diagnoseIntent: defaults[action].goal, model: 'GLM-5.2', judgeBackend: 'subagent', maxSessions: 10,
      runtimeMaintenance: false, stageSelection: { diagnose: true, plan: true, optimize: action === 'optimize' } })
    expect(input).not.toHaveProperty('disableStages')
    expect(input).not.toHaveProperty('skipPlan')
    expect(key).toEqual(expect.any(String))
    if (action === 'optimize') {
      expect(input).toMatchObject({ inputMode: 'diagnose_goal', maxRounds: 3, stageExtensions: defaults.optimize.stageExtensions })
      expect(input.startDate).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    } else expect(input).not.toHaveProperty('stageExtensions')
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/evolve/runs/task-1'))
  })

  it.each([null, '当前用户没有可用的 97 加固 Stage'])('blocks missing optimization bindings with a visible reason (%s)', async (unavailableReason) => {
    api.evolve.getSkillTaskDefaults.mockResolvedValue({ ...defaults, optimize: { ...defaults.optimize, stageExtensions: null, unavailableReason } })
    open('optimize')
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(unavailableReason ?? '当前没有有权使用且已通过集成测试的 97 加固 Stage')
    const confirm = screen.getByRole('button', { name: '确认优化' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    expect(confirm.title).toContain('97 加固 Stage')
    fireEvent.click(confirm)
    expect(api.evolve.createTask).not.toHaveBeenCalled()
  })

  it('honors a server denial even if a binding is present', async () => {
    api.evolve.getSkillTaskDefaults.mockResolvedValue({ ...defaults, optimize: { ...defaults.optimize, unavailableReason: '权限已失效' } })
    open('optimize')
    await screen.findByText('不可启动优化：权限已失效')
    expect((screen.getByRole('button', { name: '确认优化' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows load failures and permits explicit retry without launching', async () => {
    api.evolve.getSkillTaskDefaults.mockRejectedValueOnce(new Error('默认配置不可用'))
    open()
    await screen.findByText('默认配置不可用')
    expect((screen.getByRole('button', { name: '确认诊断' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await screen.findByText(defaults.diagnose.goal)
    expect(api.evolve.getSkillTaskDefaults).toHaveBeenCalledTimes(2)
    expect(api.evolve.createTask).not.toHaveBeenCalled()
  })

  it('fails closed when defaults belong to another asset', async () => {
    api.evolve.getSkillTaskDefaults.mockResolvedValue({ ...defaults, assetId: 'another-asset' })
    open()
    await screen.findByText('任务默认配置与当前 Skill 不匹配，请刷新重试')
    expect((screen.getByRole('button', { name: '确认诊断' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('prevents duplicate confirmation while pending and reports server launch errors in the dialog', async () => {
    let reject!: (error: Error) => void
    api.evolve.createTask.mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail }))
    const onClose = vi.fn()
    open('optimize', onClose)
    await screen.findByText(defaults.optimize.goal)
    const button = screen.getByRole('button', { name: '确认优化' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(api.evolve.createTask).toHaveBeenCalledTimes(1)
    expect((screen.getByRole('button', { name: '取消' }) as HTMLButtonElement).disabled).toBe(true)
    reject(new Error('Stage 权限已失效'))
    await screen.findByText('Stage 权限已失效')
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByTestId('location').textContent).toBe('/')
    fireEvent.click(screen.getByRole('button', { name: '确认优化' }))
    await waitFor(() => expect(api.evolve.createTask).toHaveBeenCalledTimes(2))
    expect(api.evolve.createTask.mock.calls[1][1]).toBe(api.evolve.createTask.mock.calls[0][1])
  })

  it.each(['diagnose', 'optimize'] as const)('opens the full advanced form for %s with only the agreed target query', async (action) => {
    open(action)
    await screen.findByText(defaults[action].goal)
    fireEvent.click(screen.getByRole('button', { name: '自定义 / 高级' }))
    const url = new URL(screen.getByTestId('location').textContent!, 'https://example.test')
    expect(url.pathname).toBe('/evolve/new')
    expect(Object.fromEntries(url.searchParams)).toEqual({ type: action === 'diagnose' ? 'diagnose' : 'full',
      target: 'skill', assetId: asset.assetId, skillAction: action })
    expect(api.evolve.createTask).not.toHaveBeenCalled()
  })

  it('cancels without creating a task', async () => {
    const onClose = vi.fn()
    open('diagnose', onClose)
    await screen.findByText(defaults.diagnose.goal)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onClose).toHaveBeenCalledOnce()
    expect(api.evolve.createTask).not.toHaveBeenCalled()
  })

  it('exposes diagnose, optimize and view on the list; both task actions open confirmation', async () => {
    render(<MemoryRouter><SkillCenter /></MemoryRouter>)
    await screen.findByText(asset.name)
    expect(screen.getByRole('button', { name: '查看' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '诊断', exact: true }))
    await screen.findByRole('heading', { name: '确认诊断 Skill' })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    fireEvent.click(screen.getByRole('button', { name: '优化', exact: true }))
    await screen.findByRole('heading', { name: '确认优化 Skill' })
    expect(api.evolve.createTask).not.toHaveBeenCalled()
  })

  it('replaces the old Skill evolution entry in detail with the same confirmation actions', async () => {
    render(<MemoryRouter initialEntries={['/evolve/skills/asset%20%2F%201']}><SkillDetail /></MemoryRouter>)
    await screen.findByText(asset.name)
    expect(screen.queryByRole('button', { name: '发起 Skill 自进化' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '优化', exact: true }))
    await screen.findByText(defaults.optimize.goal)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(api.evolve.createTask).not.toHaveBeenCalled()
  })
})

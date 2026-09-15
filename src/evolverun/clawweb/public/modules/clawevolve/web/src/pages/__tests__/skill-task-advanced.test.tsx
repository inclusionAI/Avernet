// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import Evolve from '../Evolve'

const api = vi.hoisted(() => ({ evolve: {
  getSkillAsset: vi.fn(), getSkillTaskDefaults: vi.fn(), taskDefinitions: vi.fn(), listSkillAssets: vi.fn(),
  stageCatalog: vi.fn(), listStageSkills: vi.fn(), createTask: vi.fn(), createDiagnosis: vi.fn(),
}, tclog: { bots: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ authState: 'ready', user: { userId: 'viewer' } }) }))

const asset = { assetId: 'asset-1', botId: 'owner-bot', name: 'Fixed Skill', currentVersion: 'v1' }
const bot = { botId: 'owner-bot', botName: 'Owner Bot', ownerId: 'original-owner', env: 'dev', activeEngine: 'openclaw', deviceProvider: 'baas' }
const defaults = { assetId: asset.assetId, botId: asset.botId, userId: 'original-owner',
  diagnose: { taskType: 'diagnose', goal: '原技能诊断目标' }, optimize: { taskType: 'full', goal: '原技能优化目标', unavailableReason: null,
    stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: 'verified-97' } } } } }

function Navigate() { const navigate = useNavigate(); return <button onClick={() => navigate('/evolve/new?type=full&target=skill&assetId=asset-2&skillAction=optimize')}>另一个登记目标</button> }
function open(type = 'diagnose', extra = '') {
  return render(<MemoryRouter initialEntries={[
    `/evolve/new?type=${type}&target=skill&assetId=asset-1&skillAction=${type === 'diagnose' ? 'diagnose' : 'optimize'}${extra}`,
  ]}><Navigate /><Evolve /></MemoryRouter>)
}
function submit(type = 'diagnose') { return screen.getByRole('button', { name: type === 'diagnose' ? '创建诊断任务' : '创建 Skill 自进化任务' }) as HTMLButtonElement }
async function ready(type = 'diagnose') { await waitFor(() => expect(submit(type).disabled).toBe(false)) }

beforeEach(() => {
  vi.stubGlobal('React', React)
  vi.resetAllMocks()
  api.evolve.getSkillAsset.mockResolvedValue(asset)
  api.evolve.getSkillTaskDefaults.mockResolvedValue(defaults)
  api.evolve.taskDefinitions.mockResolvedValue({ tasks: [], variants: { insight_improvement: [] } })
  api.evolve.listSkillAssets.mockResolvedValue({ items: [asset, { ...asset, assetId: 'other-skill', name: 'Other Skill' }] })
  api.evolve.stageCatalog.mockResolvedValue({ stages: [], flows: [] })
  api.evolve.listStageSkills.mockResolvedValue({ items: [] })
  api.tclog.bots.mockResolvedValue({ bots: [bot, { ...bot, botId: 'other-bot', botName: 'Other Bot' }] })
  api.evolve.createTask.mockResolvedValue({ task_id: 'created', status: 'pending' })
  api.evolve.createDiagnosis.mockResolvedValue({ task_id: 'created', status: 'pending' })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('fixed Skill advanced task form', () => {
  it('submits the explicit date range represented by the current diagnosis lookback', async () => {
    const expectedDate = (offsetDays: number) => {
      const date = new Date()
      date.setDate(date.getDate() + offsetDays)
      return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    }
    open()
    await ready()
    fireEvent.change(screen.getByRole('combobox', { name: '会话时间范围' }), { target: { value: '14' } })
    fireEvent.click(submit())

    await waitFor(() => expect(api.evolve.createDiagnosis).toHaveBeenCalledOnce())
    const payload = api.evolve.createDiagnosis.mock.calls[0][0]
    expect(payload.diagnoseIntent).toContain('最近14天')
    expect(payload.startDate).toBe(expectedDate(-14))
    expect(payload.endDate).toBe(expectedDate(0))
  })

  it('labels fixed Skill diagnosis and previews its registered name while preserving Plan', async () => {
    api.evolve.getSkillAsset.mockResolvedValue({ ...asset, name: 'daily-report-zh' })
    open()
    await ready()
    expect(screen.getByRole('heading', { name: '发起 Skill 诊断' })).toBeTruthy()
    expect(screen.getByText('Skill 诊断', { exact: true })).toBeTruthy()
    const preview = within(screen.getByText('流程预览').closest('aside')!)
    expect(preview.getByText('Skill 诊断 · daily-report-zh')).toBeTruthy()
    expect(preview.queryByText('Bot 诊断')).toBeNull()
    expect(preview.getByText('目标规划')).toBeTruthy()
    fireEvent.click(submit())
    await waitFor(() => expect(api.evolve.createDiagnosis).toHaveBeenCalledOnce())
    expect(api.evolve.createDiagnosis.mock.calls[0][0]).toMatchObject({ targetSkillAssetId: 'asset-1',
      stageSelection: { diagnose: true, plan: true, optimize: false } })
  })

  it.each(['diagnose', 'full'])('submits %s with defaults Owner, ignores forged URL identity and keeps model editable', async (type) => {
    open(type, '&botId=forged-bot&userId=forged-owner&botEnv=prod')
    await ready(type)
    expect(api.evolve.getSkillAsset).toHaveBeenCalledWith('asset-1')
    expect(api.evolve.getSkillTaskDefaults).toHaveBeenCalledWith('asset-1')
    expect(api.tclog.bots).toHaveBeenCalledWith({ ownerId: 'original-owner', status: 'all' })
    expect(api.tclog.bots.mock.calls.every(([input]) => input.ownerId === 'original-owner')).toBe(true)
    const owner = screen.getByRole('textbox', { name: '用户空间 user_id' }) as HTMLInputElement
    expect(owner.value).toBe('original-owner')
    expect(owner.readOnly).toBe(true)
    const skill = screen.getByRole('combobox', { name: '待进化 Skill' }) as HTMLSelectElement
    expect(skill.disabled).toBe(true)
    await waitFor(() => expect(skill.value).toBe('asset-1'))
    const picker = screen.getByRole('button', { name: /Owner Bot/ }) as HTMLButtonElement
    expect(picker.disabled).toBe(true)
    fireEvent.click(picker)
    expect(screen.queryByRole('radiogroup', { name: '选择 Bot' })).toBeNull()
    // Exercise the callback guard even when a synthetic event bypasses disabled DOM controls.
    fireEvent.change(skill, { target: { value: 'other-skill' } })
    fireEvent.change(owner, { target: { value: 'forged-owner' } })
    fireEvent.change(screen.getByRole('combobox', { name: '诊断模型' }), { target: { value: '__custom__' } })
    fireEvent.change(screen.getByRole('textbox', { name: '诊断自定义模型名称' }), { target: { value: 'custom-model' } })
    fireEvent.click(submit(type))
    const create = type === 'diagnose' ? api.evolve.createDiagnosis : api.evolve.createTask
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toMatchObject({ userId: 'original-owner', botId: 'owner-bot', botEnv: 'dev',
      targetSkillAssetId: 'asset-1', model: 'custom-model' })
    if (type === 'full') expect(create.mock.calls[0][0]).toMatchObject({ taskType: 'full', goal: defaults.optimize.goal,
      stageSelection: { diagnose: true, plan: true, optimize: true }, stageExtensions: defaults.optimize.stageExtensions })
  })

  it('blocks submission and does not query viewer bots before defaults resolve', async () => {
    let resolve!: (value: typeof defaults) => void
    api.evolve.getSkillTaskDefaults.mockReturnValue(new Promise(done => { resolve = done }))
    open()
    await waitFor(() => expect(api.evolve.getSkillTaskDefaults).toHaveBeenCalledOnce())
    expect(submit().disabled).toBe(true)
    expect(api.tclog.bots).not.toHaveBeenCalled()
    fireEvent.click(submit())
    expect(api.evolve.createDiagnosis).not.toHaveBeenCalled()
    await act(async () => { resolve(defaults) })
    await ready()
  })

  it.each(['asset', 'bot', 'owner', 'failure'])('fails closed for invalid defaults: %s', async (kind) => {
    if (kind === 'failure') api.evolve.getSkillTaskDefaults.mockRejectedValue(new Error('空间访问被拒绝'))
    else api.evolve.getSkillTaskDefaults.mockResolvedValue({ ...defaults,
      ...(kind === 'asset' ? { assetId: 'another-asset' } : kind === 'bot' ? { botId: 'another-bot' } : { userId: '' }) })
    open()
    await screen.findByRole('alert')
    expect(submit().disabled).toBe(true)
    expect(api.tclog.bots).not.toHaveBeenCalled()
    expect(api.evolve.createDiagnosis).not.toHaveBeenCalled()
  })

  it('rejects a getSkillAsset response for a different asset before fetching defaults', async () => {
    api.evolve.getSkillAsset.mockResolvedValue({ ...asset, assetId: 'other' })
    open()
    await screen.findByText('固定 Skill 与请求目标不匹配')
    expect(submit().disabled).toBe(true)
    expect(api.evolve.getSkillTaskDefaults).not.toHaveBeenCalled()
  })

  it.each(['missing', 'wrong-owner', 'failure'])('never falls back to another Bot when the fixed Bot is %s', async (kind) => {
    if (kind === 'failure') api.tclog.bots.mockRejectedValue(new Error('Bot 查询失败'))
    else api.tclog.bots.mockResolvedValue({ bots: [kind === 'missing' ? { ...bot, botId: 'other-bot' } : { ...bot, ownerId: 'viewer' }] })
    open()
    await screen.findByText(kind === 'failure' ? 'Bot 查询失败' : '无法加载固定 Skill 所属 Bot，不能切换到其他 Bot')
    expect(submit().disabled).toBe(true)
    expect(api.evolve.createDiagnosis).not.toHaveBeenCalled()
  })

  it('ignores a late response for the previous fixed Skill after query navigation', async () => {
    let resolve!: (value: typeof defaults) => void
    api.evolve.getSkillTaskDefaults.mockReturnValueOnce(new Promise(done => { resolve = done }))
      .mockResolvedValue({ ...defaults, assetId: 'asset-2', botId: 'second-bot', userId: 'second-owner' })
    api.evolve.getSkillAsset.mockResolvedValueOnce(asset).mockResolvedValue({ ...asset, assetId: 'asset-2', botId: 'second-bot', name: 'Second Skill' })
    api.evolve.listSkillAssets.mockResolvedValue({ items: [{ ...asset, assetId: 'asset-2', botId: 'second-bot', name: 'Second Skill' }] })
    api.tclog.bots.mockResolvedValue({ bots: [{ ...bot, botId: 'second-bot', ownerId: 'second-owner' }] })
    open('full')
    await waitFor(() => expect(api.evolve.getSkillTaskDefaults).toHaveBeenCalledWith('asset-1'))
    fireEvent.click(screen.getByRole('button', { name: '另一个登记目标' }))
    await ready('full')
    await act(async () => { resolve(defaults) })
    expect((screen.getByRole('textbox', { name: '用户空间 user_id' }) as HTMLInputElement).value).toBe('second-owner')
    fireEvent.click(submit('full'))
    await waitFor(() => expect(api.evolve.createTask).toHaveBeenCalledTimes(1))
    expect(api.evolve.createTask.mock.calls[0][0]).toMatchObject({ userId: 'second-owner', botId: 'second-bot', targetSkillAssetId: 'asset-2' })
  })

  it('keeps ordinary non-Skill task creation on the logged-in user', async () => {
    render(<MemoryRouter initialEntries={['/evolve/new?type=diagnose']}><Evolve /></MemoryRouter>)
    expect(screen.getByRole('heading', { name: '发起 Bot 诊断' })).toBeTruthy()
    expect(screen.getByText('Bot诊断', { exact: true })).toBeTruthy()
    expect(within(screen.getByText('流程预览').closest('aside')!).getByText('Bot 诊断')).toBeTruthy()
    await waitFor(() => expect(api.tclog.bots).toHaveBeenCalledWith({ ownerId: 'viewer', status: 'all' }))
    expect((screen.getByRole('textbox', { name: '用户空间 user_id' }) as HTMLInputElement).value).toBe('viewer')
    expect(api.evolve.getSkillTaskDefaults).not.toHaveBeenCalled()
  })
})

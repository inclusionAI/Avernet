// @vitest-environment jsdom
import React, { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import SkillCenter from '../SkillCenter'
import StageSkillDevelopment from '../StageSkillDevelopment'
import StageSkillManagement from '../StageSkillManagement'
import StageSkillDetail from '../StageSkillDetail'
import SpaceSelector, { spaceLabel } from '../../components/SpaceSelector'

const api = vi.hoisted(() => ({ evolve: {
  listSpaces: vi.fn(), listSkillAssets: vi.fn(), listAvailableLocalSkills: vi.fn(), registerSkillAsset: vi.fn(),
  stageCatalog: vi.fn(), listStageSkills: vi.fn(), listStageDevelopments: vi.fn(), getStageSkill: vi.fn(),
  getStageSkillContent: vi.fn(), createStageDevelopment: vi.fn(), getStageDevelopment: vi.fn(),
  uploadStageSkill: vi.fn(), downloadStageDevelopmentPackage: vi.fn(),
}, bots: { list: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ user: { userId: 'owner' } }) }))

const ownership = { spaceId: 'team-1', spaceType: 'TEAM', spaceName: '研发空间' }
const stageSkill = { implementationId: 'IMPL-1', stageSkillId: 'STAGE-1', displayName: '自定义诊断',
  stage: 'diagnose', stageName: '诊断', mode: 'preprocess', version: 'v1', status: 'registered',
  integrationTestStatus: 'untested', staticValidation: {}, updatedAt: 1789060000, ...ownership }
const draft = { stageSkillId: 'STAGE-1', displayName: '开发记录', flow: 'bot_evolution', stage: 'diagnose', stageName: '诊断', mode: 'preprocess', updatedAt: 1789060000, ...ownership }
const asset = { assetId: 'A1', name: '已登记技能', botId: 'bot-1', currentVersion: 'v1', updatedAt: 1789060000, ...ownership }

function Location() { const location = useLocation(); return <output data-testid="location">{location.pathname + location.search}</output> }
function openStage(query = '') { return render(<MemoryRouter initialEntries={['/evolve/stage-skills/new' + query]}><StageSkillDevelopment /><Location /></MemoryRouter>) }
function Selector() { const [value, setValue] = useState(''); return <SpaceSelector value={value} onChange={setValue} /> }

beforeEach(() => {
  vi.stubGlobal('React', React)
  vi.resetAllMocks()
  api.evolve.listSpaces.mockResolvedValue({ items: [
    { id: 'personal-1', name: '个人空间', type: 'PERSONAL', role: 'ADMIN' },
    { id: 'team-1', name: '研发空间', type: 'TEAM', role: 'MEMBER' },
  ] })
  api.evolve.listSkillAssets.mockResolvedValue({ items: [asset] })
  api.bots.list.mockResolvedValue({ bots: [{ botId: 'bot-1', botName: '我的 Bot' }] })
  api.evolve.listAvailableLocalSkills.mockResolvedValue({ items: [{ skillId: 'skill-1', displayName: '本地技能' }] })
  api.evolve.registerSkillAsset.mockResolvedValue(asset)
  api.evolve.stageCatalog.mockResolvedValue({ flows: [{ key: 'bot_evolution', name: 'Bot 自进化' }], stages: [
    { stage: 'diagnose', name: '诊断', extensionModes: ['preprocess'], inputSchema: {}, resultSchema: {} },
  ] })
  api.evolve.listStageSkills.mockResolvedValue({ items: [] })
  api.evolve.listStageDevelopments.mockResolvedValue({ items: [] })
  api.evolve.createStageDevelopment.mockResolvedValue(draft)
  api.evolve.getStageDevelopment.mockResolvedValue(draft)
  api.evolve.getStageSkill.mockResolvedValue(stageSkill)
  api.evolve.getStageSkillContent.mockResolvedValue({ files: [], selected: null })
  api.evolve.uploadStageSkill.mockResolvedValue(stageSkill)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('space selection interactions', () => {
  it('defaults to private and only offers real team memberships, including MEMBER', async () => {
    render(<Selector />)
    expect((screen.getByRole('combobox', { name: '所属空间' }) as HTMLSelectElement).value).toBe('')
    expect(screen.getByRole('status').textContent).toBe('正在加载空间…')
    await screen.findByRole('option', { name: '团队空间 · 研发空间' })
    expect(screen.getAllByRole('option')).toHaveLength(2)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'team-1' } })
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('team-1')
    expect(spaceLabel({ spaceId: null, spaceType: null, spaceName: null })).toBe('私有')
    expect(spaceLabel({ spaceType: 'PERSONAL' })).toBe('私有')
    expect(spaceLabel({ spaceId: 'unknown' })).not.toBe('私有')
  })

  it('shows failure, disables the selector and retries instead of claiming an empty successful load', async () => {
    api.evolve.listSpaces.mockRejectedValueOnce(new Error('Host unavailable'))
    render(<Selector />)
    expect((await screen.findByRole('alert')).textContent).toContain('Host unavailable')
    expect((screen.getByRole('combobox') as HTMLSelectElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await screen.findByRole('option', { name: '团队空间 · 研发空间' })
    expect(screen.queryByRole('alert')).toBeNull()
    expect((screen.getByRole('combobox') as HTMLSelectElement).disabled).toBe(false)
  })

  it.each(['', 'team-1'])('registers a Skill with selected space %s and keeps list ownership visible', async (spaceId) => {
    render(<MemoryRouter><SkillCenter /><Location /></MemoryRouter>)
    await screen.findByText('已登记技能')
    expect(screen.getByText('团队空间 · 研发空间')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '登记 Skill' }))
    await screen.findByRole('option', { name: '团队空间 · 研发空间' })
    fireEvent.change(screen.getByRole('combobox', { name: '所属空间' }), { target: { value: spaceId } })
    fireEvent.click(screen.getByRole('button', { name: /请选择 Bot/ }))
    fireEvent.click(screen.getByRole('radio', { name: /我的 Bot/ }))
    await screen.findByRole('option', { name: '本地技能' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Bot 中自己上传的 Skill' }), { target: { value: 'skill-1' } })
    fireEvent.click(screen.getByRole('button', { name: '登记', exact: true }))
    await waitFor(() => expect(api.evolve.registerSkillAsset).toHaveBeenCalledWith({ botId: 'bot-1', skillId: 'skill-1', ...(spaceId ? { spaceId } : {}) }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/evolve/skills/A1'))
  })

  it('creates a named team Stage draft, then preserves the development-package/upload flow', async () => {
    const view = openStage()
    await screen.findByRole('option', { name: '团队空间 · 研发空间' })
    fireEvent.change(screen.getByRole('combobox', { name: '所属空间' }), { target: { value: 'team-1' } })
    fireEvent.change(screen.getByRole('textbox', { name: '实现名称（可选）' }), { target: { value: '  研发诊断  ' } })
    fireEvent.click(screen.getByRole('button', { name: '确认并开始开发' }))
    await screen.findByText('开发记录已保存，可随时从“自定义 Stage”列表继续。')
    expect(api.evolve.createStageDevelopment).toHaveBeenCalledWith({ stage: 'diagnose', mode: 'preprocess', flow: 'bot_evolution', spaceId: 'team-1', displayName: '研发诊断' })
    expect(screen.queryByRole('combobox', { name: '所属空间' })).toBeNull()
    expect(screen.getByText('所属空间：团队空间 · 研发空间')).toBeTruthy()
    expect(screen.getByRole('button', { name: /下载开发包/ })).toBeTruthy()
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['zip'], 'custom.zip')] } })
    fireEvent.click(screen.getByRole('button', { name: '上传并校验' }))
    await waitFor(() => expect(api.evolve.uploadStageSkill).toHaveBeenCalledWith({ stage: 'diagnose', mode: 'preprocess', stageSkillId: 'STAGE-1', package: expect.any(File) }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('implementationId=IMPL-1'))
  })

  it('omits spaceId and displayName for default private Stage development', async () => {
    openStage()
    await waitFor(() => expect((screen.getByRole('button', { name: '确认并开始开发' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(screen.getByRole('button', { name: '确认并开始开发' }))
    await waitFor(() => expect(api.evolve.createStageDevelopment).toHaveBeenCalledWith({ stage: 'diagnose', mode: 'preprocess', flow: 'bot_evolution' }))
  })

  it('locks upgrade ownership and never supplies a replacement space on upload', async () => {
    api.evolve.listStageSkills.mockResolvedValue({ items: [stageSkill] })
    const view = openStage('?upgrade=STAGE-1')
    await screen.findByText('升级继承原空间，不可修改')
    expect(screen.queryByRole('combobox', { name: '所属空间' })).toBeNull()
    expect(api.evolve.listSpaces).not.toHaveBeenCalled()
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['zip'], 'upgrade.zip')] } })
    fireEvent.click(screen.getByRole('button', { name: '上传并校验' }))
    await waitFor(() => expect(api.evolve.uploadStageSkill).toHaveBeenCalledTimes(1))
    expect(api.evolve.uploadStageSkill.mock.calls[0][0]).not.toHaveProperty('spaceId')
    expect(api.evolve.createStageDevelopment).not.toHaveBeenCalled()
  })

  it('blocks upgrade when the source cannot be loaded', async () => {
    const view = openStage('?upgrade=missing')
    await screen.findByText('原自定义实现不存在，不能创建升级版本')
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['zip'], 'upgrade.zip')] } })
    fireEvent.click(screen.getByRole('button', { name: '上传并校验' }))
    expect(api.evolve.uploadStageSkill).not.toHaveBeenCalled()
  })

  it('shows ownership for both uploaded versions and saved drafts, including legacy private records', async () => {
    api.evolve.listStageSkills.mockResolvedValue({ items: [stageSkill] })
    api.evolve.listStageDevelopments.mockResolvedValue({ items: [{ ...draft, stageSkillId: 'LEGACY', spaceId: null, spaceType: null, spaceName: null }] })
    render(<MemoryRouter><StageSkillManagement /></MemoryRouter>)
    await screen.findByText('团队空间 · 研发空间')
    expect(screen.getByText('私有')).toBeTruthy()
  })

  it('shows the exact Stage version ownership on its detail', async () => {
    render(<MemoryRouter initialEntries={['/evolve/stage-skills/IMPL-1']}><StageSkillDetail /></MemoryRouter>)
    await screen.findByText('所属空间：团队空间 · 研发空间')
  })

  it('does not present failed lists as empty success', async () => {
    api.evolve.listStageSkills.mockRejectedValue(new Error('Stage unavailable'))
    const view = render(<MemoryRouter><StageSkillManagement /></MemoryRouter>)
    await screen.findByText('Stage unavailable')
    expect(screen.queryByText('还没有自定义实现，点击右上角开始接入。')).toBeNull()
    view.unmount()
    api.evolve.listSkillAssets.mockRejectedValue(new Error('Skill unavailable'))
    render(<MemoryRouter><SkillCenter /></MemoryRouter>)
    await screen.findByText('Skill unavailable')
    expect(screen.queryByText('还没有登记 Skill。')).toBeNull()
  })
})

// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigationType } from 'react-router-dom'
import StageSkillDevelopment from '../StageSkillDevelopment'
import StageSkillDetail from '../StageSkillDetail'

const api = vi.hoisted(() => ({
  evolve: {
    stageCatalog: vi.fn(), listStageSkills: vi.fn(), listSkillAssets: vi.fn(),
    getStageSkill: vi.fn(), getStageSkillContent: vi.fn(),
    uploadStageSkill: vi.fn(), registerStageSkill: vi.fn(), runStageSkillTest: vi.fn(),
    createStageDevelopment: vi.fn(), getStageDevelopment: vi.fn(),
  },
  tclog: { bots: vi.fn() },
}))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ user: { userId: 'owner' } }) }))

function implementation(status = 'validated', version = 'v1') {
  return {
    implementationId: `IMPL-${version}`, stageSkillId: 'STAGE-1', displayName: `Evidence ${version}`,
    stage: 'diagnose', stageName: '诊断', mode: 'replace', version, status,
    integrationTestStatus: 'untested', integrationTestTaskId: null,
    staticValidation: { status: 'passed', checks: [{ id: 'entrypoint', label: 'Skill 入口文件', status: 'passed' }] },
  }
}

function Page() {
  const location = useLocation()
  const navigationType = useNavigationType()
  return <>
    <output data-testid="location">{location.pathname + location.search}</output>
    <output data-testid="navigation-type">{navigationType}</output>
    {location.pathname.endsWith('/new') ? <StageSkillDevelopment /> : <StageSkillDetail />}
  </>
}

function open(path = '/evolve/stage-skills/new?implementationId=IMPL-v1') {
  return render(<MemoryRouter initialEntries={[path]}><Page /></MemoryRouter>)
}

beforeEach(() => {
  vi.stubGlobal('React', React)
  vi.resetAllMocks()
  api.evolve.stageCatalog.mockResolvedValue({
    flows: [{ key: 'bot_evolution', name: 'Bot 自进化' }, { key: 'skill_evolution', name: 'Skill 自进化' }],
    stages: [{ stage: 'diagnose', name: '诊断', description: 'Evidence', extensionModes: ['preprocess', 'replace'],
      inputSchema: { required: ['diagnose_goal'], properties: { diagnose_goal: { type: 'string', description: '诊断目标' } } },
      resultSchema: { properties: {} } }],
  })
  api.evolve.listStageSkills.mockResolvedValue({ items: [implementation('registered', 'v2'), implementation()] })
  api.evolve.listSkillAssets.mockResolvedValue({ items: [] })
  api.tclog.bots.mockResolvedValue({ bots: [{ botId: 'bot-test', botName: 'Test Bot', env: 'dev' }] })
  api.evolve.getStageSkill.mockResolvedValue(implementation())
  api.evolve.getStageSkillContent.mockResolvedValue({ files: [], selected: null })
  api.evolve.registerStageSkill.mockResolvedValue(implementation('registered'))
  api.evolve.uploadStageSkill.mockResolvedValue(implementation())
  const development = { stageSkillId: 'STAGE-1', ownerId: 'owner', displayName: '诊断自定义', flow: 'bot_evolution', stage: 'diagnose', stageName: '诊断', mode: 'preprocess' }
  api.evolve.createStageDevelopment.mockResolvedValue(development)
  api.evolve.getStageDevelopment.mockResolvedValue(development)
  api.evolve.runStageSkillTest.mockResolvedValue({ taskId: 'TEST-1' })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Stage Skill exact-version resume', () => {
  it('refreshes the same running test to its terminal state without submitting another test', async () => {
    api.evolve.getStageSkill.mockResolvedValueOnce({ ...implementation('testing'), integrationTestStatus: 'testing', integrationTestTaskId: 'TEST-1' })
      .mockResolvedValue({ ...implementation('test_passed'), integrationTestStatus: 'test_passed', integrationTestTaskId: 'TEST-1' })
    open()
    await screen.findByText('Evidence v1')
    expect((screen.getByRole('button', { name: '注册版本' }) as HTMLButtonElement).disabled).toBe(true)
    await waitFor(() => expect((screen.getByRole('button', { name: '注册版本' }) as HTMLButtonElement).disabled).toBe(false), { timeout: 4500 })
    expect(api.evolve.getStageSkill).toHaveBeenCalledTimes(2)
    expect(api.evolve.runStageSkillTest).not.toHaveBeenCalled()
  })

  it('switches detail content and historical test status together without launching a test', async () => {
    api.evolve.getStageSkill.mockImplementation(async (id: string) => ({
      ...implementation('registered', id === 'IMPL-v1' ? 'v1' : 'v2'),
      integrationTestStatus: id === 'IMPL-v1' ? 'test_failed' : 'test_passed',
      integrationTestTaskId: id === 'IMPL-v1' ? 'TEST-OLD' : 'TEST-NEW',
    }))
    api.evolve.getStageSkillContent.mockImplementation(async (id: string) => ({
      files: [{ path: 'SKILL.md', text: true }], selected: { path: 'SKILL.md', content: `${id} content` },
    }))
    open('/evolve/stage-skills/IMPL-v2')
    await screen.findByText('v2：最近测试通过')
    fireEvent.change(screen.getByRole('combobox', { name: '实现版本' }), { target: { value: 'IMPL-v1' } })
    await screen.findByText('v1：最近测试失败')
    expect(screen.getByText('IMPL-v1 content')).toBeTruthy()
    expect(screen.queryByText('IMPL-v2 content')).toBeNull()
    expect(api.evolve.runStageSkillTest).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('combobox', { name: '实现版本' }), { target: { value: 'IMPL-v2' } })
    await screen.findByText('v2：最近测试通过')
    expect(screen.queryByText('v1：最近测试失败')).toBeNull()
  })

  it('opens an exact historical version from detail, not the latest upgrade', async () => {
    open('/evolve/stage-skills/IMPL-v1')
    fireEvent.click(await screen.findByRole('button', { name: '继续接入' }))
    await screen.findByText('Evidence v1')
    expect(screen.getByTestId('location').textContent).toBe('/evolve/stage-skills/new?implementationId=IMPL-v1')
    expect(api.evolve.getStageSkill).toHaveBeenLastCalledWith('IMPL-v1')
    expect(screen.queryByText('Evidence v2')).toBeNull()
  })

  it('restores the same version on remount, locks binding and registers without uploading', async () => {
    const first = open()
    await screen.findByText('Evidence v1')
    first.unmount()
    open()
    await screen.findByText('Evidence v1')
    expect(api.evolve.getStageSkill).toHaveBeenCalledTimes(2)
    expect((screen.getByRole('combobox', { name: '开放的 Stage' }) as HTMLSelectElement).disabled).toBe(true)
    expect((screen.getByRole('combobox', { name: '接入位置' }) as HTMLSelectElement).value).toBe('replace')
    expect((screen.getByRole('combobox', { name: '接入位置' }) as HTMLSelectElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: '上传并校验' })).toBeNull()
    expect(screen.queryByRole('combobox', { name: '测试 Bot' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '注册版本' }))
    await screen.findByRole('button', { name: '已注册' })
    expect(api.evolve.registerStageSkill).toHaveBeenCalledExactlyOnceWith('IMPL-v1')
    expect(api.evolve.uploadStageSkill).not.toHaveBeenCalled()
  })

  it('persists an upload as an exact-version URL and removes the upload control', async () => {
    api.evolve.listStageSkills.mockResolvedValue({ items: [] })
    const view = open('/evolve/stage-skills/new')
    await screen.findByRole('option', { name: '诊断' })
    expect(view.container.querySelector('input[type="file"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '确认并开始开发' }))
    await screen.findByText('开发记录已保存，可随时从“自定义 Stage”列表继续。')
    expect(api.evolve.createStageDevelopment).toHaveBeenCalledWith({ stage: 'diagnose', mode: 'preprocess', flow: 'bot_evolution' })
    expect(api.evolve.uploadStageSkill).not.toHaveBeenCalled()
    const input = view.container.querySelector('input[type="file"]')!
    fireEvent.change(input, { target: { files: [new File(['zip'], 'evidence.zip', { type: 'application/zip' })] } })
    fireEvent.click(screen.getByRole('button', { name: '上传并校验' }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/evolve/stage-skills/new?implementationId=IMPL-v1'))
    await screen.findByText('Evidence v1')
    expect(api.evolve.uploadStageSkill).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('navigation-type').textContent).toBe('REPLACE')
    expect(screen.queryByRole('button', { name: '上传并校验' })).toBeNull()
  })

  it('keeps explicit upgrade separate and resumes only the new upload response', async () => {
    api.evolve.uploadStageSkill.mockResolvedValue(implementation('validated', 'v3'))
    api.evolve.getStageSkill.mockResolvedValue(implementation('validated', 'v3'))
    const view = open('/evolve/stage-skills/new?upgrade=STAGE-1')
    await waitFor(() => expect((screen.getByRole('combobox', { name: '接入位置' }) as HTMLSelectElement).value).toBe('replace'))
    expect(api.evolve.getStageSkill).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: '注册版本' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(view.container.querySelector('input[type="file"]')!, {
      target: { files: [new File(['zip'], 'upgrade.zip', { type: 'application/zip' })] },
    })
    fireEvent.click(screen.getByRole('button', { name: '上传并校验' }))
    await screen.findByText('Evidence v3')
    expect(api.evolve.uploadStageSkill).toHaveBeenCalledWith(expect.objectContaining({ stageSkillId: 'STAGE-1' }))
    expect(screen.getByTestId('location').textContent).toBe('/evolve/stage-skills/new?implementationId=IMPL-v3')
    await waitFor(() => expect(api.evolve.getStageSkill).toHaveBeenCalledExactlyOnceWith('IMPL-v3'))
  })

  it('allows a registered version to be tested without registering again', async () => {
    api.evolve.getStageSkill.mockResolvedValue(implementation('registered'))
    open()
    const registered = await screen.findByRole('button', { name: '已注册' })
    expect((registered as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /运行真实集成测试/ }))
    await screen.findByRole('option', { name: 'Test Bot' })
    fireEvent.change(screen.getByRole('combobox', { name: '测试 Bot' }), { target: { value: 'bot-test' } })
    fireEvent.change(screen.getByRole('textbox', { name: /诊断目标/ }), { target: { value: 'Check evidence' } })
    fireEvent.click(screen.getByRole('button', { name: '启动真实集成测试' }))
    await screen.findByRole('button', { name: '查看测试任务 ↗' })
    expect(api.evolve.runStageSkillTest).toHaveBeenCalledExactlyOnceWith('IMPL-v1', {
      botId: 'bot-test', botEnv: 'dev', caseInput: { diagnose_goal: 'Check evidence' },
    })
    expect((screen.getByRole('button', { name: '已注册' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '集成测试进行中' }) as HTMLButtonElement).disabled).toBe(true)
    expect(api.evolve.registerStageSkill).not.toHaveBeenCalled()
    expect(api.evolve.uploadStageSkill).not.toHaveBeenCalled()
  })

  it.each(['test_failed', 'testing', 'deleted'])('does not offer registration for %s', async (status) => {
    api.evolve.getStageSkill.mockResolvedValue(implementation(status))
    open()
    await waitFor(() => expect(api.evolve.getStageSkill).toHaveBeenCalledWith('IMPL-v1'))
    if (status === 'deleted') await screen.findByText('当前版本已删除，不能继续接入')
    else await screen.findByText('Evidence v1')
    expect((screen.getByRole('button', { name: '注册版本' }) as HTMLButtonElement).disabled).toBe(true)
    expect(api.evolve.uploadStageSkill).not.toHaveBeenCalled()
  })

  it('does not fall back to a different version when exact resume cannot be loaded', async () => {
    api.evolve.getStageSkill.mockRejectedValue(new Error('Stage Skill 不存在'))
    open()
    await screen.findByText('Stage Skill 不存在')
    expect(screen.queryByText('Evidence v2')).toBeNull()
    expect((screen.getByRole('button', { name: '注册版本' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: '上传并校验' })).toBeNull()
  })

  it('rejects ambiguous resume and upgrade parameters without loading or uploading a version', async () => {
    open('/evolve/stage-skills/new?implementationId=IMPL-v1&upgrade=STAGE-1')
    await screen.findByText('不能同时继续已有版本和创建升级版本')
    expect(api.evolve.getStageSkill).not.toHaveBeenCalled()
    expect(api.evolve.uploadStageSkill).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: '注册版本' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('keeps recovery and registration available when Bot loading fails', async () => {
    api.tclog.bots.mockRejectedValue(new Error('Bot 500'))
    open()
    await screen.findByText('Evidence v1')
    expect(api.tclog.bots).not.toHaveBeenCalled()
    expect(api.evolve.listSkillAssets).not.toHaveBeenCalled()
    expect(screen.queryByRole('combobox', { name: '进化流程' })).toBeNull()
    expect(screen.queryByRole('button', { name: '查看开发说明' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /运行真实集成测试/ }))
    await screen.findByText(/不影响当前版本注册/)
    expect((screen.getByRole('button', { name: '启动真实集成测试' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '注册版本' }))
    await screen.findByRole('button', { name: '已注册' })
    expect(api.evolve.registerStageSkill).toHaveBeenCalledExactlyOnceWith('IMPL-v1')
    expect(api.evolve.uploadStageSkill).not.toHaveBeenCalled()
  })

  it('does not load Skill assets or offer a test template, even if the asset service fails', async () => {
    api.evolve.listSkillAssets.mockRejectedValue(new Error('Assets unavailable'))
    open()
    await screen.findByText('Evidence v1')
    fireEvent.click(screen.getByRole('button', { name: /运行真实集成测试/ }))
    await screen.findByRole('option', { name: 'Test Bot' })
    expect(screen.queryByRole('combobox', { name: /测试目标 Skill/ })).toBeNull()
    expect(screen.queryByRole('combobox', { name: '测试模板' })).toBeNull()
    expect(api.evolve.listSkillAssets).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: '启动真实集成测试' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('submits only the selected Bot and Stage input for the exact implementation version', async () => {
    open()
    await screen.findByText('Evidence v1')
    fireEvent.click(screen.getByRole('button', { name: /运行真实集成测试/ }))
    await screen.findByRole('option', { name: 'Test Bot' })
    fireEvent.change(screen.getByRole('combobox', { name: '测试 Bot' }), { target: { value: 'bot-test' } })
    fireEvent.change(screen.getByRole('textbox', { name: /诊断目标/ }), { target: { value: 'Check evidence' } })
    fireEvent.click(screen.getByRole('button', { name: '启动真实集成测试' }))
    await waitFor(() => expect(api.evolve.runStageSkillTest).toHaveBeenCalledExactlyOnceWith('IMPL-v1', {
      botId: 'bot-test', botEnv: 'dev', caseInput: { diagnose_goal: 'Check evidence' },
    }))
  })
})

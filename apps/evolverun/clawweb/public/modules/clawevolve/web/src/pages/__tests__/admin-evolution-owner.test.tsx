// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Evolve from '../Evolve'

const api = vi.hoisted(() => ({ evolve: {
  capabilities: vi.fn(), adminOwners: vi.fn(), taskDefinitions: vi.fn(), createTask: vi.fn(),
  stageCatalog: vi.fn(), listStageSkills: vi.fn(), listSkillAssets: vi.fn(),
}, bots: { list: vi.fn() } }))
const auth = vi.hoisted(() => ({ user: { userId: 'admin-owner', isAdmin: true, isClawEvolveAdmin: true } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ authState: 'ready', user: auth.user }) }))
vi.mock('../../hooks/useEvolveModelConfig', () => ({
  useEvolveModelConfig: () => ({ defaultModel: 'GLM-5.2', models: ['GLM-5.2'] }),
}))
const materials = vi.hoisted(() => ({ options: vi.fn(), upload: vi.fn() }))
vi.mock('../../api/reference-materials', () => ({ referenceMaterialsApi: materials }))

const ownBot = { botId: 'default', botName: 'My Bot', ownerId: 'admin-owner', env: 'prod',
  activeEngine: 'openclaw', deviceProvider: 'baas' }
const otherBot = { ...ownBot, ownerId: 'target-owner', botName: 'Other Owner Bot' }
function open() { return render(<MemoryRouter initialEntries={['/evolve/new?type=full']}><Evolve /></MemoryRouter>) }
function owner() { return screen.getByLabelText('用户空间 user_id') as HTMLInputElement }
function submit() { return screen.getByRole('button', { name: '创建 Bot自进化任务' }) as HTMLButtonElement }
function adminMode() { fireEvent.click(screen.getByRole('checkbox', { name: '管理员视图' })) }
async function chooseBot(name: string) {
  fireEvent.click(await screen.findByRole('button', { name: /请选择 Bot/ }))
  fireEvent.click(await within(screen.getByRole('radiogroup', { name: '选择 Bot' })).findByRole('radio', { name: new RegExp(name) }))
}
function fillGoal() {
  fireEvent.change(screen.getByRole('textbox', { name: /任务名称/ }), { target: { value: '管理员带材料自进化' } })
  fireEvent.change(screen.getByRole('textbox', { name: /一句话目标、成功标准和优先级/ }), { target: { value: '参考上传规范改进解析，保留已有业务规则。' } })
}

beforeEach(() => {
  vi.stubGlobal('React', React)
  vi.resetAllMocks()
  auth.user = { userId: 'admin-owner', isAdmin: true, isClawEvolveAdmin: true }
  api.evolve.capabilities.mockResolvedValue({ skillManagement: false, stageCustomization: false })
  api.evolve.adminOwners.mockResolvedValue({ ownerUserIds: ['target-owner', 'slow-owner'] })
  api.evolve.taskDefinitions.mockResolvedValue({ tasks: [], variants: { insight_improvement: [] } })
  api.evolve.stageCatalog.mockResolvedValue({ stages: [], flows: [] })
  api.evolve.listStageSkills.mockResolvedValue({ items: [] })
  api.evolve.listSkillAssets.mockResolvedValue({ items: [] })
  api.bots.list.mockImplementation(async ({ ownerId }) => ({ bots: ownerId === ownBot.ownerId ? [ownBot] : [otherBot] }))
  api.evolve.createTask.mockResolvedValue({ task_id: 'created', status: 'pending' })
  materials.options.mockResolvedValue({ available: true, maxFiles: 500, maxFileBytes: 20 * 1024 * 1024,
    maxTotalBytes: 100 * 1024 * 1024, maxArchiveBytes: 110 * 1024 * 1024,
    maxPathBytes: 1024, maxPathComponentBytes: 255, maxDepth: 20 })
  materials.upload.mockResolvedValue({ materialId: 'owner-material', expiresAt: Date.now() + 60_000, files: [], totalBytes: 3 })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('allows an enabled administrator to select an owner and submit its Bot with uploaded materials', async () => {
  open()
  expect(owner().readOnly).toBe(true)
  adminMode()
  expect(owner().readOnly).toBe(false)
  fireEvent.change(owner(), { target: { value: 'target-owner' } })
  await waitFor(() => expect(api.bots.list).toHaveBeenCalledWith({ ownerId: 'target-owner', status: 'all' }))
  await chooseBot('Other Owner Bot')
  fillGoal()
  fireEvent.click(await screen.findByRole('button', { name: '添加参考材料' }))
  await screen.findByRole('button', { name: '添加文件' })
  const file = new File(['abc'], 'rules.md')
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('abc').buffer })
  fireEvent.change(screen.getByLabelText('选择参考文件'), { target: { files: [file] } })
  expect(submit().disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '上传材料' }))
  await screen.findByRole('button', { name: '上传完成' })
  fireEvent.click(submit())
  await waitFor(() => expect(api.evolve.createTask).toHaveBeenCalledOnce())
  expect(api.evolve.createTask.mock.calls[0][0]).toMatchObject({ taskType: 'full', userId: 'target-owner', botId: 'default',
    botEnv: 'prod', referenceMaterialsUploadId: 'owner-material', inputMode: 'diagnose_goal' })
})

it('clears the previous Bot and ignores an old owner lookup that finishes late', async () => {
  let finishOld!: (value: { bots: typeof ownBot[] }) => void
  api.bots.list.mockImplementation(({ ownerId }) => ownerId === 'slow-owner'
    ? new Promise(resolve => { finishOld = resolve })
    : Promise.resolve({ bots: ownerId === ownBot.ownerId ? [ownBot] : [otherBot] }))
  open()
  adminMode()
  fireEvent.change(owner(), { target: { value: 'target-owner' } })
  await chooseBot('Other Owner Bot')
  expect(submit().disabled).toBe(false)
  fireEvent.change(owner(), { target: { value: 'slow-owner' } })
  expect(submit().disabled).toBe(true)
  expect(screen.queryByRole('button', { name: /Other Owner Bot/ })).toBeNull()
  await waitFor(() => expect(finishOld).toBeTypeOf('function'))
  fireEvent.change(owner(), { target: { value: 'target-owner' } })
  await chooseBot('Other Owner Bot')
  await act(async () => { finishOld({ bots: [{ ...ownBot, ownerId: 'slow-owner', botName: 'Late Bot' }] }) })
  expect(owner().value).toBe('target-owner')
  expect(screen.queryByText('Late Bot')).toBeNull()
  fillGoal()
  fireEvent.click(submit())
  await waitFor(() => expect(api.evolve.createTask).toHaveBeenCalledOnce())
  expect(api.evolve.createTask.mock.calls[0][0]).toMatchObject({ userId: 'target-owner', botId: 'default' })
})

it('returns to the signed-in owner and clears the foreign Bot when administrator mode is disabled', async () => {
  open()
  adminMode()
  fireEvent.change(owner(), { target: { value: 'target-owner' } })
  await chooseBot('Other Owner Bot')
  adminMode()
  expect(owner().readOnly).toBe(true)
  expect(owner().value).toBe('admin-owner')
  expect(submit().disabled).toBe(true)
  await chooseBot('My Bot')
  fillGoal()
  fireEvent.click(submit())
  await waitFor(() => expect(api.evolve.createTask).toHaveBeenCalledOnce())
  expect(api.evolve.createTask.mock.calls[0][0]).toMatchObject({ userId: 'admin-owner', botId: 'default' })
})

it('keeps ordinary users bound to their authenticated owner and does not query a forged owner', async () => {
  auth.user = { userId: 'admin-owner', isAdmin: false, isClawEvolveAdmin: false }
  open()
  expect(owner().readOnly).toBe(true)
  expect(screen.queryByRole('checkbox', { name: '管理员视图' })).toBeNull()
  fireEvent.change(owner(), { target: { value: 'target-owner' } })
  await chooseBot('My Bot')
  fillGoal()
  fireEvent.click(submit())
  await waitFor(() => expect(api.evolve.createTask).toHaveBeenCalledOnce())
  expect(api.bots.list.mock.calls.every(([input]) => input.ownerId === 'admin-owner')).toBe(true)
  expect(api.evolve.createTask.mock.calls[0][0]).toMatchObject({ userId: 'admin-owner', botId: 'default' })
})

it('blocks submission after a foreign owner lookup fails or the owner is cleared', async () => {
  api.bots.list.mockImplementation(async ({ ownerId }) => {
    if (ownerId === 'target-owner') throw new Error('Cannot query owner Bots')
    return { bots: [ownBot] }
  })
  open()
  await chooseBot('My Bot')
  adminMode()
  fireEvent.change(owner(), { target: { value: 'target-owner' } })
  await screen.findByText('Cannot query owner Bots')
  expect(submit().disabled).toBe(true)
  fireEvent.change(owner(), { target: { value: '' } })
  expect(submit().disabled).toBe(true)
  expect(api.evolve.createTask).not.toHaveBeenCalled()
})

// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import SkillRegistrationDialog from '../SkillRegistrationDialog'

const api = vi.hoisted(() => ({ evolve: { listSpaces: vi.fn(), listAvailableLocalSkills: vi.fn(), registerSkillAsset: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ user: { userId: 'viewer' } }) }))
const bots = [
  { botId: 'pre-bot', botName: '预发普通 Bot', ownerId: 'owner', env: 'pre', botType: 'personal', deviceProvider: 'baas', activeEngine: 'openclaw' },
  { botId: 'prod-bot', botName: '生产服务 Bot', ownerId: 'owner', env: 'prod', botType: 'service', deviceProvider: 'baas', activeEngine: 'openclaw' },
]
function open(onRegistered = vi.fn()) { render(<SkillRegistrationDialog bots={bots} onClose={vi.fn()} onRegistered={onRegistered} />) }
function choose(name: string) {
  fireEvent.click(screen.getByRole('button', { name: /请选择 Bot|展开查看|owner.*bot/ }))
  fireEvent.click(screen.getByRole('radio', { name: new RegExp(name) }))
}
beforeEach(() => {
  vi.stubGlobal('React', React); vi.resetAllMocks()
  api.evolve.listSpaces.mockResolvedValue({ items: [] })
  api.evolve.listAvailableLocalSkills.mockResolvedValue({ items: [{ skillId: 's1', displayName: '我的技能' }] })
  api.evolve.registerSkillAsset.mockResolvedValue({ assetId: 'ASSET-1' })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('uses the native task Bot picker with environment and service badges and registers the selected Bot', async () => {
  const registered = vi.fn(); open(registered)
  fireEvent.click(screen.getByRole('button', { name: /请选择 Bot/ }))
  expect(screen.getByRole('radio', { name: /预发普通 Bot.*pre.*普通 Bot/ })).toBeTruthy()
  fireEvent.click(screen.getByRole('radio', { name: /生产服务 Bot.*prod.*服务型 Bot/ }))
  await screen.findByRole('option', { name: '我的技能' })
  expect(screen.getByText('服务型 Bot')).toBeTruthy()
  fireEvent.change(screen.getByRole('combobox', { name: 'Bot 中自己上传的 Skill' }), { target: { value: 's1' } })
  fireEvent.click(screen.getByRole('button', { name: '登记', exact: true }))
  await waitFor(() => expect(api.evolve.registerSkillAsset).toHaveBeenCalledWith({ botId: 'prod-bot', skillId: 's1' }))
  await waitFor(() => expect(registered).toHaveBeenCalledWith({ assetId: 'ASSET-1' }))
})

it('shows unreachable feedback without raw HTTP/JSON and allows an explicit retry', async () => {
  const message = 'Bot 不可达，请确认 Bot 在线后重试。'
  api.evolve.listAvailableLocalSkills.mockRejectedValueOnce(Object.assign(new Error('API 503: raw'), {
    status: 503, body: JSON.stringify({ code: 'HOST_BOT_UNREACHABLE', error: message }),
  }))
  open(); choose('预发普通 Bot')
  expect((await screen.findByRole('alert')).textContent).toContain(message)
  expect(screen.queryByText(/API 503/)).toBeNull()
  expect((screen.getByRole('button', { name: '登记', exact: true }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '重试读取 Skill' }))
  await screen.findByRole('option', { name: '我的技能' })
  expect(screen.queryByRole('alert')).toBeNull()
})

it('clears the old selection and ignores late responses after switching Bots', async () => {
  let resolveOld!: (value: unknown) => void
  api.evolve.listAvailableLocalSkills.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
    .mockResolvedValueOnce({ items: [{ skillId: 'new', displayName: '生产技能' }] })
  open(); choose('预发普通 Bot'); choose('生产服务 Bot')
  await screen.findByRole('option', { name: '生产技能' })
  await act(async () => resolveOld({ items: [{ skillId: 'old', displayName: '迟到的预发技能' }] }))
  expect(screen.queryByRole('option', { name: '迟到的预发技能' })).toBeNull()
  expect((screen.getByRole('combobox', { name: 'Bot 中自己上传的 Skill' }) as HTMLSelectElement).value).toBe('')
})

it('keeps permission failures distinct from an unreachable Bot and leaves unknown 500 details hidden', async () => {
  api.evolve.listAvailableLocalSkills.mockRejectedValueOnce(Object.assign(new Error('API 403'), {
    status: 403, body: JSON.stringify({ code: 'HOST_LOCAL_SKILL_REQUEST_FAILED', error: '当前账号无权读取该 Skill' }),
  }))
  open(); choose('预发普通 Bot')
  expect((await screen.findByRole('alert')).textContent).toContain('当前账号无权读取该 Skill')
  expect(screen.queryByText(/Bot 不可达/)).toBeNull()
  api.evolve.listAvailableLocalSkills.mockRejectedValueOnce(Object.assign(new Error('API 500: Internal Server Error'), { status: 500, body: '{"error":"Internal Server Error"}' }))
  fireEvent.click(screen.getByRole('button', { name: '重试读取 Skill' }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Skill 列表读取失败，请稍后重试。'))
  expect(screen.queryByText(/Internal Server Error/)).toBeNull()
})

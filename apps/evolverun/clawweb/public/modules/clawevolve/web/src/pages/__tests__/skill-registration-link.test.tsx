// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Link, MemoryRouter, useLocation } from 'react-router-dom'
import SkillCenter from '../SkillCenter'

const api = vi.hoisted(() => ({
  evolve: { listSkillAssets: vi.fn(), listSpaces: vi.fn(), listAvailableLocalSkills: vi.fn(), registerSkillAsset: vi.fn() },
  bots: { list: vi.fn() },
}))
vi.mock('../../api/client', () => ({ api }))
vi.mock('../../hooks/useClientUser', () => ({ useClientUser: () => ({ user: { userId: 'viewer' } }) }))

const bots = [
  { botId: 'bot-1', botName: 'Pre Bot', ownerId: 'viewer', env: 'pre' },
  { botId: 'bot-1', botName: 'Prod Bot', ownerId: 'viewer', env: 'prod' },
  { botId: 'bot-2', botName: 'Other Bot', ownerId: 'viewer', env: 'prod' },
]
const skills = [
  { skillId: 'skill-1', displayName: 'First Skill' },
  { skillId: 'skill-2', displayName: 'Second Skill' },
]
const query = '?register=1&botId=bot-1&botEnv=prod'
function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname + location.search}</output>
}
function open(search = query) {
  return render(<MemoryRouter initialEntries={['/evolve/skills' + search]}>
    <SkillCenter /><Location />
    <Link to={'/evolve/skills' + query + '&skillId=skill-2'}>登记指定技能</Link>
  </MemoryRouter>)
}
function skillSelector() {
  return screen.getByRole('combobox', { name: 'Bot 中自己上传的 Skill' }) as HTMLSelectElement
}

beforeEach(() => {
  vi.stubGlobal('React', React)
  vi.resetAllMocks()
  api.evolve.listSkillAssets.mockResolvedValue({ items: [] })
  api.evolve.listSpaces.mockResolvedValue({ items: [] })
  api.bots.list.mockResolvedValue({ bots })
  api.evolve.listAvailableLocalSkills.mockResolvedValue({ items: skills })
  api.evolve.registerSkillAsset.mockResolvedValue({ assetId: 'ASSET-1' })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('opens from the link and selects the exact Bot after the directory loads', async () => {
  let finish!: (result: { bots: typeof bots }) => void
  api.bots.list.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  open()
  expect(screen.getByRole('dialog', { name: '登记 Bot 中已有 Skill' })).toBeTruthy()
  expect(api.evolve.listAvailableLocalSkills).not.toHaveBeenCalled()
  await act(async () => { finish({ bots }) })
  await screen.findByRole('option', { name: 'Second Skill' })
  expect(screen.getByRole('button', { name: /Prod Bot/ })).toBeTruthy()
  expect(api.bots.list).toHaveBeenCalledWith({ ownerId: 'viewer', status: 'all' })
  expect(api.evolve.listAvailableLocalSkills).toHaveBeenCalledExactlyOnceWith('bot-1', 'prod')
  expect(skillSelector().value).toBe('')
  expect(api.evolve.registerSkillAsset).not.toHaveBeenCalled()
})

it('preselects the optional Skill and registers only after explicit submission', async () => {
  open(query + '&skillId=skill-2')
  await waitFor(() => expect(skillSelector().value).toBe('skill-2'))
  expect(api.evolve.registerSkillAsset).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '登记', exact: true }))
  await waitFor(() => expect(api.evolve.registerSkillAsset).toHaveBeenCalledExactlyOnceWith({
    botId: 'bot-1', botEnv: 'prod', skillId: 'skill-2',
  }))
  await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/evolve/skills/ASSET-1'))
})

it('allows opening the empty form without a target Bot', async () => {
  open('?register=1')
  await screen.findByRole('button', { name: /请选择 Bot/ })
  expect(skillSelector().value).toBe('')
  expect(api.evolve.listAvailableLocalSkills).not.toHaveBeenCalled()
})

it.each([
  ['?register=1&botId=bot-1', '请提供目标 botEnv'],
  ['?register=1&botId=inaccessible&botEnv=prod', '未找到可访问的目标 Bot'],
  ['?register=1&botId=default&botEnv=prod', 'default Bot 必须提供 ownerId'],
])('keeps an invalid Bot target selectable by hand (%s)', async (search, error) => {
  open(search)
  await screen.findByText(new RegExp(error))
  expect(api.evolve.listAvailableLocalSkills).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: /请选择 Bot/ }))
  fireEvent.click(screen.getByRole('radio', { name: /Prod Bot/ }))
  await screen.findByRole('option', { name: 'First Skill' })
  expect(api.evolve.listAvailableLocalSkills).toHaveBeenCalledWith('bot-1', 'prod')
  expect(screen.queryByRole('alert')).toBeNull()
})

it('uses the existing owner constraint to disambiguate default Bots', async () => {
  api.bots.list.mockResolvedValue({ bots: [
    { ...bots[1], botId: 'default' },
    { ...bots[1], botId: 'default', ownerId: 'collaborator', botName: 'Collaborator Bot' },
  ] })
  open('?register=1&botId=default&botEnv=prod&ownerId=viewer&skillId=skill-1')
  await waitFor(() => expect(skillSelector().value).toBe('skill-1'))
  expect(screen.getByRole('button', { name: /Prod Bot/ })).toBeTruthy()
  expect(api.evolve.listAvailableLocalSkills).toHaveBeenCalledWith('default', 'prod')
})

it('leaves an unavailable Skill unselected and allows choosing a valid one', async () => {
  open(query + '&skillId=missing')
  await screen.findByText('链接指定的 Skill 不在该 Bot 的可登记列表中，请重新选择。')
  expect(skillSelector().value).toBe('')
  expect(skillSelector().disabled).toBe(false)
  expect((screen.getByRole('button', { name: '登记', exact: true }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(skillSelector(), { target: { value: 'skill-1' } })
  expect(screen.queryByRole('alert')).toBeNull()
  expect((screen.getByRole('button', { name: '登记', exact: true }) as HTMLButtonElement).disabled).toBe(false)
})

it('keeps manual Skill changes and clears the preset when switching Bots', async () => {
  open(query + '&skillId=skill-2')
  await waitFor(() => expect(skillSelector().value).toBe('skill-2'))
  fireEvent.change(skillSelector(), { target: { value: 'skill-1' } })
  fireEvent.change(screen.getByRole('textbox', { name: '搜索技能' }), { target: { value: 'filter' } })
  expect(skillSelector().value).toBe('skill-1')
  fireEvent.click(screen.getByRole('button', { name: /Prod Bot/ }))
  fireEvent.click(screen.getByRole('radio', { name: /Other Bot/ }))
  await waitFor(() => expect(api.evolve.listAvailableLocalSkills).toHaveBeenLastCalledWith('bot-2', 'prod'))
  await screen.findByRole('option', { name: 'Second Skill' })
  expect(skillSelector().value).toBe('')
})

it('removes registration parameters on close and keeps unrelated query parameters', async () => {
  open(query + '&skillId=skill-2&ownerId=viewer&entityId=viewer&filter=keep')
  await waitFor(() => expect(skillSelector().value).toBe('skill-2'))
  fireEvent.click(screen.getByRole('button', { name: '关闭', exact: true }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByTestId('location').textContent).toBe('/evolve/skills?filter=keep')
  fireEvent.click(screen.getByRole('button', { name: '登记 Skill' }))
  await screen.findByRole('button', { name: /请选择 Bot/ })
  expect(skillSelector().value).toBe('')
})

it('opens on in-app link navigation and leaves the plain list closed', async () => {
  open('')
  await screen.findByText('还没有登记 Skill。')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(api.bots.list).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('link', { name: '登记指定技能' }))
  await waitFor(() => expect(skillSelector().value).toBe('skill-2'))
})

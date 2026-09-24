import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../client'

const fetchJson = vi.hoisted(() => vi.fn())
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { evolve: {} }, fetchJson }))
afterEach(() => vi.resetAllMocks())

describe('module space API contracts', () => {
  it('loads real spaces and propagates failures', async () => {
    fetchJson.mockResolvedValueOnce({ items: [{ id: 't1', name: 'Team', type: 'TEAM', role: 'MEMBER' }] })
    expect((await api.evolve.listSpaces()).items[0].id).toBe('t1')
    expect(fetchJson).toHaveBeenCalledWith('/api/evolve/spaces')
    fetchJson.mockRejectedValueOnce(new Error('Space access denied'))
    await expect(api.evolve.listSpaces()).rejects.toThrow('Space access denied')
  })

  it('serializes optional search without changing unfiltered calls', async () => {
    await api.evolve.listStageSkills()
    expect(fetchJson).toHaveBeenLastCalledWith('/api/evolve/stage-skills')
    await api.evolve.listStageSkills({ search: '  团队 & stage  ' })
    expect(fetchJson).toHaveBeenLastCalledWith('/api/evolve/stage-skills?search=%E5%9B%A2%E9%98%9F+%26+stage')
  })

  it('sends selected space and display name, omitting default personal space', async () => {
    await api.evolve.registerSkillAsset({ botId: 'b1', skillId: 's1', spaceId: 't1' })
    expect(fetchJson).toHaveBeenLastCalledWith('/api/evolve/skill-assets', {
      method: 'POST', body: JSON.stringify({ botId: 'b1', skillId: 's1', spaceId: 't1' }),
    })
    const draft = { stage: 'diagnose', mode: 'preprocess' as const, flow: 'bot_evolution' as const, displayName: 'Custom', spaceId: 't1' }
    await api.evolve.createStageDevelopment(draft)
    expect(fetchJson).toHaveBeenLastCalledWith('/api/evolve/stage-developments', { method: 'POST', body: JSON.stringify(draft) })
    await api.evolve.registerSkillAsset({ botId: 'b1', skillId: 's1' })
    expect(fetchJson).toHaveBeenLastCalledWith('/api/evolve/skill-assets', { method: 'POST', body: '{"botId":"b1","skillId":"s1"}' })
  })
})

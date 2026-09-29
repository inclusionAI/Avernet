// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../client'

afterEach(() => vi.unstubAllGlobals())

describe('Skill list administrator query parameters', () => {
  it.each([
    ['skill-assets', api.evolve.listSkillAssets],
    ['skill-events', api.evolve.listSkillEvents],
  ] as const)('preserves default %s requests and encodes administrator filters', async (path, list) => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ items: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    await list()
    expect(fetch).toHaveBeenLastCalledWith(`/api/evolve/${path}`, expect.any(Object))
    await list({ scope: 'all', ownerUserId: '  owner & 1  ' })
    expect(fetch).toHaveBeenLastCalledWith(`/api/evolve/${path}?scope=all&ownerUserId=owner+%26+1`, expect.any(Object))
    await list({ scope: 'all', ownerUserId: ' ' })
    expect(fetch).toHaveBeenLastCalledWith(`/api/evolve/${path}?scope=all`, expect.any(Object))
  })
})

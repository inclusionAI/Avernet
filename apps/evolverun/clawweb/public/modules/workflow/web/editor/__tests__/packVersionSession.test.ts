import { describe, expect, it } from 'vitest'
import { canonicalPackPath, compatiblePackScope, validateSnapshot } from '../packVersionSession'

const base = {
  workflowId: 'flow',
  packId: 'pack',
  baseRef: 'origin/wf/flow',
  baseCommit: 'a'.repeat(40),
  draftHead: 'a'.repeat(40),
  versionFiles: [],
}

describe('Pack snapshot identity', () => {
  it('normalizes trailing separators before comparing Pack scopes', () => {
    expect(canonicalPackPath('packs/pack/')).toBe('packs/pack')
    expect(validateSnapshot({ ...base, packPath: 'packs/pack/' }, 'flow', 'draft').packPath).toBe('packs/pack')
  })

  it('rejects unsafe Pack paths', () => {
    expect(canonicalPackPath('../pack')).toBeNull()
    expect(() => validateSnapshot({ ...base, packPath: '../pack' }, 'flow', 'draft')).toThrow(/Invalid response/)
  })

  it('accepts the supported root Pack migration over packs/<packId>', () => {
    expect(compatiblePackScope({ packId: 'pack', packPath: '' }, { packId: 'pack', packPath: 'packs/pack' })).toBe(true)
    expect(compatiblePackScope({ packId: 'pack', packPath: 'packs/pack' }, { packId: 'pack', packPath: '' })).toBe(true)
    expect(compatiblePackScope({ packId: 'pack', packPath: '' }, { packId: 'other', packPath: 'packs/other' })).toBe(false)
    expect(compatiblePackScope({ packId: 'pack', packPath: '' }, { packId: 'pack', packPath: 'other' })).toBe(false)
  })
})

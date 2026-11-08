import { describe, expect, it } from 'vitest'
import { canonicalPackPath, validateSnapshot } from '../packVersionSession'

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
})

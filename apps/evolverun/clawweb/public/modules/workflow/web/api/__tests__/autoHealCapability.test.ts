import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { readAutoHealUnifiedCapability } from '../autoHealUnifiedBaseline'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
afterEach(() => vi.resetAllMocks())

describe('AutoHeal save capability', () => {
  it.each([false, null])('uses the existing save path for explicit disabled value %s', async value => {
    vi.mocked(fetchJson).mockResolvedValue({ autoHealUnifiedSave: value })
    await expect(readAutoHealUnifiedCapability()).resolves.toBe(false)
  })

  it('does not silently treat an unrecognized response as a disabled capability', async () => {
    vi.mocked(fetchJson).mockResolvedValue({ autoHealUnifiedSave: 'future' })
    await expect(readAutoHealUnifiedCapability()).rejects.toThrow('无法识别')
  })
})

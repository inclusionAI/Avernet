import { describe, expect, it } from 'vitest'
import { resolveBotName } from '../utils'

describe('resolveBotName', () => {
  it('keeps same bot IDs isolated by owner', () => {
    const options = [
      { ownerUserId: '146836', botId: 'default', botName: '栖真' },
      { ownerUserId: '162296', botId: 'default', botName: '澄松' },
      { ownerUserId: '236240', botId: 'default', botName: '卢克尔乔丹' },
    ]
    expect(resolveBotName(options, '162296', 'default')).toBe('澄松')
    expect(resolveBotName(options, '236240', 'default')).toBe('卢克尔乔丹')
    expect(resolveBotName(options, '66743', 'default')).toBe('default')
  })

  it('never falls back to another owner when owner metadata is absent', () => {
    expect(resolveBotName([{ botId: 'default', botName: '栖真' }], '162296', 'default')).toBe('default')
  })
})

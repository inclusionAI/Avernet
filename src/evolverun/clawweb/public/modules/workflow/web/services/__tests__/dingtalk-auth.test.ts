// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { requestDingTalkAuthCode } from '../dingtalk-auth'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.dd
})

describe('requestDingTalkAuthCode', () => {
  it('uses the current H5 API with clientId and corpId', async () => {
    window.dd = {
      ready: (callback) => callback(),
      requestAuthCode: ({ clientId, corpId, success }) => {
        expect({ clientId, corpId }).toEqual({ clientId: 'app-key', corpId: 'ding-corp' })
        success({ code: 'one-time-code' })
      },
    }

    await expect(requestDingTalkAuthCode({ clientId: 'app-key', corpId: 'ding-corp' }))
      .resolves.toEqual({ ok: true, authCode: 'one-time-code' })
  })

  it('returns the DingTalk failure reason instead of hiding it', async () => {
    window.dd = {
      ready: (callback) => callback(),
      requestAuthCode: ({ fail }) => fail({ errorMessage: '应用没有免登权限' }),
    }

    await expect(requestDingTalkAuthCode({ clientId: 'app-key', corpId: 'ding-corp' }))
      .resolves.toEqual({ ok: false, error: '应用没有免登权限' })
  })
})

// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { requestDingTalkAuthCode } from '../dingtalk-auth'

vi.mock('dingtalk-jsapi', () => new Promise(() => {}))

afterEach(() => {
  vi.useRealTimers()
  delete window.dd
})

describe('requestDingTalkAuthCode SDK loading timeout', () => {
  it('times out when the packaged DingTalk JSAPI chunk never loads', async () => {
    vi.useFakeTimers()

    let settled: Awaited<ReturnType<typeof requestDingTalkAuthCode>> | undefined
    void requestDingTalkAuthCode({ clientId: 'app-key', corpId: 'ding-corp' })
      .then((result) => { settled = result })

    await vi.advanceTimersByTimeAsync(5001)

    expect(settled).toEqual({ ok: false, error: '钉钉环境检测超时' })
  })
})

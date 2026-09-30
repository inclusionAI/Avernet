// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import {
  ApprovalLoginRequiredError,
  beginApprovalLogin,
  clearApprovalLoginAttempt,
  fetchApprovalJson,
} from '../approval-session-fetch'

function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}): Response {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
  } as Response
}

describe('approval session fetch', () => {
  it('asks the gateway for JSON and returns approval data', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ id: 1, status: 'pending' }))

    const result = await fetchApprovalJson<{ id: number; status: string }>(
      '/api/approval/1',
      undefined,
      fetchImpl,
    )

    expect(result).toEqual({ id: 1, status: 'pending' })
    const request = fetchImpl.mock.calls[0]
    expect(new Headers(request[1]?.headers).get('Accept')).toBe('application/json')
  })

  it('treats the IAM login envelope as a login requirement even when HTTP is 200', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({
      actionType: 'LOGIN',
      buserviceErrorCode: 'USER_NOT_LOGIN',
      buserviceErrorMsg: 'https://pubbuservice.example/login',
    }))

    await expect(fetchApprovalJson('/api/approval/1', undefined, fetchImpl))
      .rejects.toBeInstanceOf(ApprovalLoginRequiredError)
  })

  it('treats a redirected HTML login page as a login requirement', async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      status: 200,
      redirected: true,
      url: 'https://pubbuservice.example/dingTalkAuth.htm',
      headers: new Headers({ 'content-type': 'text/html;charset=UTF-8' }),
      json: async () => { throw new SyntaxError("Unexpected token '<'") },
    } as Response))

    await expect(fetchApprovalJson('/api/approval/1', undefined, fetchImpl))
      .rejects.toBeInstanceOf(ApprovalLoginRequiredError)
  })

  it('treats an HTTP 401 JSON response as a login requirement', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({
      error: 'Unauthorized',
      message: '未登录',
    }, { ok: false, status: 401 }))

    await expect(fetchApprovalJson('/api/approval/1/resolve/session', undefined, fetchImpl))
      .rejects.toBeInstanceOf(ApprovalLoginRequiredError)
  })
})

describe('approval login handoff', () => {
  it('reloads the complete historical approval URL only once', () => {
    const values = new Map<string, string>()
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value) },
      removeItem: (key: string) => { values.delete(key) },
    }
    const location = {
      href: 'https://clawweb-pre.example/approval/1?empId=legacy&corpId=legacy-corp',
      reload: vi.fn(),
    }

    expect(beginApprovalLogin(location, storage)).toBe(true)
    expect(location.reload).toHaveBeenCalledTimes(1)
    expect(beginApprovalLogin(location, storage)).toBe(false)
    expect(location.reload).toHaveBeenCalledTimes(1)

    clearApprovalLoginAttempt(storage)
    expect(beginApprovalLogin(location, storage)).toBe(true)
    expect(location.reload).toHaveBeenCalledTimes(2)
  })
})

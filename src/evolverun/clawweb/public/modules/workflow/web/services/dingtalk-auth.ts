export type DingTalkPublicConfig = {
  clientId: string
  corpId: string
}

type DingTalkFailure = Error | { errorMessage?: string; message?: string; error?: string } | unknown

type DingTalkApi = {
  ready?: (callback: () => void) => void
  requestAuthCode?: (params: {
    clientId: string
    corpId: string
    success: (result: { code: string }) => void
    fail: (error: DingTalkFailure) => void
  }) => void
  runtime?: {
    permission?: {
      requestAuthCode?: (params: {
        corpId: string
        onSuccess: (result: { code: string }) => void
        onFail: (error: DingTalkFailure) => void
      }) => void
    }
  }
}

declare global {
  interface Window {
    dd?: DingTalkApi
  }
}

export type DingTalkAuthCodeResult =
  | { ok: true; authCode: string }
  | { ok: false; error: string }

export async function loadDingTalkConfig(): Promise<DingTalkPublicConfig> {
  const res = await fetch('/api/approval/auth/dingtalk/config')
  const body = await res.json().catch(() => ({})) as Partial<DingTalkPublicConfig> & { message?: string }
  if (!res.ok || !body.clientId || !body.corpId) {
    throw new Error(body.message || '钉钉免登未配置')
  }
  return { clientId: body.clientId, corpId: body.corpId }
}

function failureMessage(error: DingTalkFailure): string {
  if (error instanceof Error) return error.message
  if (error && typeof error === 'object') {
    const value = error as { errorMessage?: string; message?: string; error?: string }
    return value.errorMessage || value.message || value.error || '获取钉钉授权码失败'
  }
  return typeof error === 'string' ? error : '获取钉钉授权码失败'
}

export function requestDingTalkAuthCode(config: DingTalkPublicConfig): Promise<DingTalkAuthCodeResult> {
  return new Promise((resolve) => {
    const api = window.dd
    if (!api) {
      resolve({ ok: false, error: '非钉钉环境' })
      return
    }
    let settled = false
    const finish = (result: DingTalkAuthCodeResult) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      resolve(result)
    }
    const success = (result: { code: string }) => finish(
      result.code ? { ok: true, authCode: result.code } : { ok: false, error: '钉钉未返回授权码' },
    )
    const fail = (error: DingTalkFailure) => finish({ ok: false, error: failureMessage(error) })
    const request = () => {
      if (api.requestAuthCode) {
        api.requestAuthCode({ ...config, success, fail })
        return
      }
      const legacy = api.runtime?.permission?.requestAuthCode
      if (legacy) {
        legacy({ corpId: config.corpId, onSuccess: success, onFail: fail })
        return
      }
      finish({ ok: false, error: '钉钉 JSAPI 不支持免登' })
    }
    const timeout = window.setTimeout(() => finish({ ok: false, error: '钉钉环境检测超时' }), 5000)
    if (api.ready) api.ready(request)
    else request()
  })
}

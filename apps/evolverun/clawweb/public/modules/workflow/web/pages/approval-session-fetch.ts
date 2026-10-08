export class ApprovalLoginRequiredError extends Error {
  constructor() {
    super('未登录或登录状态已失效')
    this.name = 'ApprovalLoginRequiredError'
  }
}

type FetchLike = typeof fetch

type LoginLocation = {
  href: string
  reload: () => void
}

type LoginStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

type LoginEnvelope = {
  actionType?: string
  buserviceErrorCode?: string
  message?: string
  error?: string
}

const LOGIN_ATTEMPT_KEY = 'clawweb.approval.login-return-url'

function isLoginEnvelope(value: unknown): value is LoginEnvelope {
  if (!value || typeof value !== 'object') return false
  const body = value as LoginEnvelope
  return body.actionType === 'LOGIN' || body.buserviceErrorCode === 'USER_NOT_LOGIN'
}

export async function fetchApprovalJson<T>(
  input: RequestInfo | URL,
  init?: RequestInit,
  fetchImpl: FetchLike = fetch,
): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('Accept', 'application/json')

  const response = await fetchImpl(input, { ...init, headers })
  const contentType = response.headers?.get?.('content-type') ?? ''
  if (contentType.toLowerCase().includes('text/html')) {
    throw new ApprovalLoginRequiredError()
  }
  if (response.status === 401) throw new ApprovalLoginRequiredError()

  let body: unknown
  try {
    body = await response.json()
  } catch (error) {
    if (response.redirected) throw new ApprovalLoginRequiredError()
    throw error
  }

  if (isLoginEnvelope(body)) throw new ApprovalLoginRequiredError()
  if (!response.ok) {
    const errorBody = body && typeof body === 'object' ? body as LoginEnvelope : {}
    throw new Error(errorBody.message || errorBody.error || `HTTP ${response.status}`)
  }

  return body as T
}

export function beginApprovalLogin(
  location: LoginLocation = window.location,
  storage: LoginStorage = window.sessionStorage,
): boolean {
  if (storage.getItem(LOGIN_ATTEMPT_KEY) === location.href) return false
  storage.setItem(LOGIN_ATTEMPT_KEY, location.href)
  location.reload()
  return true
}

export function clearApprovalLoginAttempt(storage: LoginStorage = window.sessionStorage): void {
  storage.removeItem(LOGIN_ATTEMPT_KEY)
}

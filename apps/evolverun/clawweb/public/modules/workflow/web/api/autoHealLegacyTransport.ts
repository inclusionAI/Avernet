/** A timeout bounds both dispatch and body consumption. A timeout is never proof
 * that the server stopped writing; late responses cannot clear any caller lock. */
export const AUTO_HEAL_LEGACY_TIMEOUT_MS = 30_000
export async function readLegacyAutoHealResponse(url: string, init: RequestInit, operation: 'save' | 'deploy'): Promise<{ status: number; text: string; bodyRead: boolean }> {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([(async () => {
      const response = await fetch(url, { ...init, signal: controller.signal })
      if (!(response instanceof Response)) throw new Error('写入响应无效，结果未知。')
      const status = Object.getOwnPropertyDescriptor(Response.prototype, 'status')!.get!.call(response) as number
      try { return { status, text: await Response.prototype.text.call(response), bodyRead: true } }
      catch { return { status, text: response.statusText, bodyRead: false } }
    })(), new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        reject(new Error(`${operation === 'save' ? '兼容保存' : '兼容部署'}超时，结果未知；请人工核对数据库和 Git，勿重试。`))
        controller.abort()
      }, AUTO_HEAL_LEGACY_TIMEOUT_MS)
    })])
  } finally { clearTimeout(timer) }
}

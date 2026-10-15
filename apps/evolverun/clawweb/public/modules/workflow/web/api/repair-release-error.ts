/** Only bounded structured application errors are user-facing. Never dump an
 * HTTP response, Git stderr, ticket or upstream HTML into the workbench. */
export function repairReleaseError(value: unknown): string {
  const failure = value && typeof value === 'object' ? value as { status?: number; body?: string } : {}
  if (failure.status === 401 || failure.status === 403) return '当前没有此操作权限，请核对登录、工作流与部署目标权限。'
  try {
    const body = JSON.parse(failure.body ?? '{}')
    if (['STATE_CONFLICT', 'CAPABILITY_UNAVAILABLE', 'INVALID_INPUT', 'IDEMPOTENCY_MISMATCH'].includes(body.code)
      && typeof body.error === 'string' && body.error.length <= 512) return body.error
  } catch { /* Opaque errors remain recoverable, not a raw JSON or HTML dump. */ }
  return '操作结果尚未确认，请先核对状态，不要重复发起部署。'
}

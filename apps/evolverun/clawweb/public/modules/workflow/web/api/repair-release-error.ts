/** Only bounded structured application errors are user-facing. Never dump an
 * HTTP response, Git stderr, ticket or upstream HTML into the workbench. */
export function repairReleaseError(value: unknown, stage: 'prepare' | 'publish' = 'publish'): string {
  const failure = value && typeof value === 'object' ? value as { status?: number; body?: string } : {}
  if (failure.status === 401 || failure.status === 403) return '当前没有此操作权限，请核对登录、工作流与部署目标权限。'
  let requestId = ''
  try {
    const body = JSON.parse(failure.body ?? '{}')
    if (typeof body.requestId === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(body.requestId)) requestId = `（错误编号：${body.requestId}）`
    if (['STATE_CONFLICT', 'CAPABILITY_UNAVAILABLE', 'INVALID_INPUT', 'IDEMPOTENCY_MISMATCH', 'SOURCE_CHANGED', 'CONTENT_MISMATCH', 'PAYLOAD_TOO_LARGE', 'TASK_NOT_FOUND', 'REVISION_NOT_FOUND'].includes(body.code)
      && typeof body.error === 'string' && body.error.length <= 512) return body.error
  } catch { /* Opaque errors remain recoverable, not a raw JSON or HTML dump. */ }
  return (stage === 'prepare' ? '发布信息读取失败，尚未提交发布。请重试读取。' : '未收到服务器确认。候选已保留，请重试同一请求，系统不会重复发布。') + requestId
}

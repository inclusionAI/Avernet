/** Present a safe read failure, never the upstream response body or an assumed permission cause. */
export function repairReadError(error: unknown): string {
  const failure = error && typeof error === 'object' ? error as { status?: number; body?: string; message?: string } : {}
  let requestId = ''
  try {
    const id = JSON.parse(failure.body ?? '{}').requestId
    if (typeof id === 'string' && /^[a-f0-9-]{36}$/i.test(id)) requestId = ` 请求编号：${id}`
  } catch { /* Non-JSON upstream errors are not user-facing diagnostics. */ }
  const message = failure.status === 401 ? '登录身份未确认（401），请确认登录后重试。'
    : failure.status === 403 ? '修复数据访问被拒绝（403），需核对登录身份与工作流权限；问题与证据仍可查看。'
    : failure.status && failure.status >= 500 ? `修复数据服务异常（${failure.status}），请重试；问题与证据仍可查看。`
    : failure.message?.includes('读取超时') ? '修复数据读取超时，请重试；问题与证据仍可查看。'
    : '修复任务与处理状态加载失败；问题与证据仍可查看。'
  return message + requestId
}

/** Present a safe read failure, never the upstream response body or an assumed permission cause. */
export function repairReadError(error: unknown): string {
  const failure = error && typeof error === 'object' ? error as { status?: number; body?: string; message?: string } : {}
  let requestId = ''
  let payloadMessage = ''
  try {
    const diagnostic = JSON.parse(failure.body ?? '{}')
    const id = diagnostic.requestId
    if (typeof id === 'string' && /^[a-f0-9-]{36}$/i.test(id)) requestId = ` 请求编号：${id}`
    if (failure.status === 413 && diagnostic.code === 'PAYLOAD_TOO_LARGE') {
      const labels: Record<string,string> = { request_body: '请求内容过大', candidates_response: '列表响应过大', item_response: '详情响应过大', frozen_item: '生成所需完整建议过大', frozen_batch: '生成所需完整输入过大' }
      const label = labels[diagnostic.stage]
      if (label) payloadMessage = `${label}（413）。`
      if (label && Number.isSafeInteger(diagnostic.actualBytes) && diagnostic.actualBytes >= 0 && Number.isSafeInteger(diagnostic.limit) && diagnostic.limit > 0) payloadMessage += ` ${diagnostic.actualBytes} / ${diagnostic.limit} 字节。`
    }
  } catch { /* Non-JSON upstream errors are not user-facing diagnostics. */ }
  const message = failure.status === 401 ? '登录身份未确认（401），请确认登录后重试。'
    : failure.status === 413 ? payloadMessage || '内容超过限制，无法确认限制来源（413），请保留请求编号供核查。'
    : failure.status === 403 ? '修复数据访问被拒绝（403），需核对登录身份与工作流权限；问题与证据仍可查看。'
    : failure.status && failure.status >= 500 ? `修复数据服务异常（${failure.status}），请重试；问题与证据仍可查看。`
    : failure.message?.includes('读取超时') ? '修复数据读取超时，请重试；问题与证据仍可查看。'
    : '修复任务与处理状态加载失败；问题与证据仍可查看。'
  return message + requestId
}

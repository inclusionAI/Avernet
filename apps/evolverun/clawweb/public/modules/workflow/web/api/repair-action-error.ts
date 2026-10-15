export type RepairRecoveryAction = 'retry-result' | 'refresh-task' | 'refresh-sources'

/** The HTTP client retains status/body. Interpret the contract, never its English message. */
export function repairActionFailure(error: unknown): { message: string; action: RepairRecoveryAction | null } {
  const failure = error && typeof error === 'object' ? error as { status?: number; body?: string } : {}
  let body: Record<string, unknown> = {}
  try {
    const parsed: unknown = JSON.parse(failure.body ?? '{}')
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>
  } catch { /* Upstream HTML and unstructured errors are not user-facing diagnostics. */ }
  if (body.recoveryAction === 'retry-result') return {
    message: body.reasonCode === 'EXECUTION_RUNNING' ? '上一轮仍在执行，暂不能生成新一轮。请先核对原执行与结果。'
      : '上一轮候选结果尚未确认，请先核对原结果；不会重复派发任务。', action: 'retry-result',
  }
  if (body.reasonCode === 'RESULT_RECOVERED') return { message: '原候选已恢复，请返回审核后再确认本轮调整。', action: 'refresh-task' }
  if (body.code === 'SOURCE_CHANGED') return { message: '建议来源已更新，请重新读取建议并确认选择；你的文字调整仍保留。', action: 'refresh-sources' }
  if (failure.status === 401 || failure.status === 403) return { message: '当前身份没有确认修改权限，请核对登录与工作流权限。', action: 'refresh-task' }
  if (body.code === 'CAPABILITY_UNAVAILABLE') return { message: '生成服务当前不可用，请稍后核对任务状态。', action: 'refresh-task' }
  if (body.code === 'PAYLOAD_TOO_LARGE' || body.code === 'INVALID_INPUT') return { message: '提交内容不符合要求，请检查选择数量与文字长度。', action: null }
  return { message: '提交结果尚未确认，请先核对任务状态。重试相同内容会沿用本次请求编号。', action: 'refresh-task' }
}

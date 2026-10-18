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
  if (body.reasonCode === 'PROPOSAL_BASELINE_CHANGED' || body.code === 'SOURCE_CHANGED' && body.error === '原建议基线已变化，请重新评估') return { message: '原建议基线已变化，请核对配置差异。原选择与要求已保留，不会自动提交或派发。', action: null }
  if (body.reasonCode === 'BASELINE_IDENTITY_UNKNOWN') return { message: '原建议缺少可核对的配置摘要，无法确认基线。选择与要求已保留。', action: null }
  if (body.reasonCode === 'BASELINE_CONTENT_MISMATCH') return { message: '生效部署记录与 Git Pack 配置不一致。选择与要求已保留，请核对部署记录。', action: null }
  if (body.reasonCode === 'BASELINE_STALE') return { message: '生效版本在准备期间再次变化。选择与要求已保留，请确认最新版本后再次提交。', action: null }
  if (body.reasonCode === 'PLAN_SELECTION_INCOMPLETE') return { message: '整合方案的成员已变化。原始选择已保留，请确认受影响方案的范围；不会自动纳入新建议。', action: null }
  if (body.reasonCode === 'PLAN_SCOPE_INCOMPLETE') return { message: '部分文字建议缺少明确修改范围，请单独处理或补充方案后再整合。选择与要求已保留。', action: null }
  if (body.reasonCode === 'PLAN_CONFLICT') return { message: '所选建议对同一设置要求不同修改，请保留一种目标或先完成整合；原选择与要求保留。', action: null }
  if (body.reasonCode === 'PLAN_NOT_READY') return { message: '重叠修改尚待整合，请等待整理或只选择一项原建议；原选择与要求保留。', action: null }
  if (body.reasonCode === 'EVIDENCE_INCOMPLETE') return { message: '原始证据缺失、被裁剪或校验失败，请补充完整证据；选择与要求保留，刷新列表不会修复证据。', action: null }
  if (body.reasonCode === 'SOURCE_READ_UNAVAILABLE') return { message: '原始建议或证据读取失败，选择与要求保留。请重试核对；本次未创建新任务。', action: 'refresh-sources' }
  if (body.code === 'PAYLOAD_TOO_LARGE') return { message: body.reasonCode === 'REPAIR_FROZEN_ITEM_TOO_LARGE' ? '单条原始建议或证据超过安全冻结上限，无法生成。选择与要求保留，请先整理原始证据。' : '请求或冻结证据超过大小上限，请缩小范围或缩短说明；选择与要求保留。', action: null }
  if (body.code === 'SOURCE_CHANGED' || body.code === 'ITEM_BUSY' || body.code === 'ITEM_NOT_FOUND') return { message: '正在逐项核对已选建议，只需重新确认受影响条目；其余选择与文字要求保留。', action: 'refresh-sources' }
  if (failure.status === 401 || failure.status === 403) return { message: '当前身份没有确认修改权限，请核对登录与工作流权限。', action: 'refresh-task' }
  if (body.code === 'CAPABILITY_UNAVAILABLE') return { message: '生成服务当前不可用，请稍后核对任务状态。', action: 'refresh-task' }
  if (body.code === 'PAYLOAD_TOO_LARGE' || body.code === 'INVALID_INPUT') return { message: '提交内容不符合要求，请检查选择数量与文字长度。', action: null }
  return { message: '提交结果尚未确认，请先核对任务状态。重试相同内容会沿用本次请求编号。', action: 'refresh-task' }
}

/** Safe, allowlisted wording. Exception message/body is never a display fallback. */
const errors: Record<string, string> = {
  EXECUTION_DISABLED: '补偿执行尚未启用，当前只能预览。',
  INVALID_FLOW_SELECTION: '请选择 1–100 个不重复的有效运行 ID。',
  INVALID_COMPLETION_RANGE: '请选择有效且不超过 31 天的绝对时间范围。',
  AMBIGUOUS_RANGE: '请选择时间范围或运行 ID，不可同时提交。',
  CONFIG_REQUIRED: '请选择接收配置。',
  CONFIG_NOT_FOUND: '所选接收配置已不可用，请重新预览。',
  INVALID_INPUT: '补偿请求格式不正确，请检查输入。',
  REASON_REQUIRED_10_500: '补偿原因必须为 10–500 字。',
  INVALID_REQUEST_KEY: '请求标识无效，不能创建补偿批次。',
  REQUEST_KEY_CONFLICT: '请求标识已绑定其他内容；不得更换标识重复创建。',
  INVALID_ITEM_SELECTION: '请选择有效的补偿项。',
  SELECTION_NOT_ELIGIBLE: '所选项已不符合补偿条件。',
  DELIVERY_ALREADY_RESERVED: '通知已被投递批次占用，不可重复补发。',
  PREVIEW_EXPIRED: '预览已过期；已提交的请求只能沿用原请求标识核对结果。',
  PREVIEW_NOT_FOUND: '预览不存在或当前身份不可访问。',
  BATCH_NOT_FOUND: '批次不存在或当前身份不可访问，不会自动创建新批次。',
  RUN_NOT_FOUND_IN_WORKFLOW: '所选运行不属于当前工作流或不可访问。',
  DATABASE_UNAVAILABLE: '补偿服务存储暂不可用，请稍后重试同一请求。',
  SERVICE_CLOSED: '补偿服务暂不可用，请稍后重试同一请求。',
  COMPENSATION_UNAVAILABLE: '补偿服务暂不可用，请稍后重试同一请求。',
  COMPENSATION_SECURITY_NOT_MOUNTED: '补偿服务安全验证尚未就绪。',
  COMPENSATION_ADMIN_REQUIRED: '当前身份无补偿管理权限。',
  IDENTITY_REQUIRED: '当前身份未通过补偿管理验证。',
  UNAUTHENTICATED: '请先完成登录验证。',
  CSRF_REJECTED: '请求安全验证未通过，请重新验证登录状态。',
  ORIGIN_NOT_ALLOWED: '当前页面来源未通过补偿安全验证。',
};
const reasons: Record<string, string> = {
  ...errors,
  PROTECTION_KEY_MISSING: '未配置补偿快照保护密钥',
  TRUSTED_EVIDENCE_PROVIDER_MISSING: '未配置可信幂等证据提供方',
  TRUSTED_EVIDENCE_MISSING: '缺少该运行的可信幂等证据',
  NOT_SUCCEEDED: '运行尚未成功结束',
  TRIAL_RUN: '试运行不允许补偿',
  RUN_ENVIRONMENT_UNVERIFIED: '运行环境尚未验证',
  COMPLETION_TIME_MISSING: '缺少可信结束时间',
  RETRY_EPOCH_UNVERIFIED: '运行重试代次尚未验证',
  NODE_OWNERSHIP_MISMATCH: '节点与运行归属不一致',
  RESULT_MISSING: '缺少运行结果',
  INVALID_RUN_JSON: '运行数据结构无效',
  INVALID_STATE_JSON: '运行状态结构无效',
  FANOUT_REQUIRES_REVIEW: '并行分支运行需要人工核实',
  CONFIG_DISABLED: '接收配置已停用',
  SUCCESS_EVENT_NOT_SUBSCRIBED: '配置未订阅成功事件',
  INVALID_SUBSCRIPTION: '事件订阅配置无效',
  UNSAFE_TARGET: '接收地址不满足安全条件',
  ATTESTATION_MISMATCH: '幂等证明与当前运行不一致',
  ATTESTATION_EXPIRED: '幂等证明已过期',
  GENERATION_OR_RECEIVER_UNVERIFIED: '可信代次或逻辑接收者尚未验证',
  HISTORICAL_RECEIVER_UNVERIFIED: '历史接收者映射尚未验证',
  HISTORICAL_ROUTE_UNVERIFIED: '历史路由尚未验证',
  LEGACY_LATE_RETENTION_IDEMPOTENCY_UNVERIFIED: '历史、迟到请求或保留期幂等契约尚未验证',
  NULL_PHASE_NOT_APPROVED: '空阶段值未获接收契约认可',
  RECEIVER_ALREADY_DELIVERED: '接收者已有投递记录',
  EXISTING_RECEIVER_ATTEMPT: '存在历史投递尝试，需要人工核实',
  NO_DELIVERY_EVIDENCE_NOT_PROOF_OF_NONDELIVERY: '没有投递记录不代表从未送达；仅凭服务端可信幂等证明获得资格',
  CONFIG_DRIFT: '接收配置已变化',
  EVIDENCE_DRIFT_OR_EXPIRED: '可信证明已变化或过期',
  RUN_SNAPSHOT_DRIFT: '运行快照已变化',
  RUN_MISSING: '运行记录已不可用',
  FROZEN_OWNERSHIP_INVALID: '补偿快照归属无效',
  FROZEN_SNAPSHOT_INVALID: '补偿快照无效',
  PROTECTED_SNAPSHOT_INVALID: '受保护快照无法验证',
  ADMIN_STOP: '管理员已停止尚未发送的通知',
  ADMIN_STOP_BEFORE_POST: '通知发送前已被管理员停止',
  SENDING_LEASE_EXPIRED: '发送状态租约已过期，结果未知且不可重发',
  HTTP_OUTCOME_UNKNOWN: 'HTTP 投递结果未知，不可重发',
  HTTP_RECEIVED_NOT_BUSINESS_CONFIRMATION: 'HTTP 已接收，不代表业务成功',
};
export function callbackCompensationErrorStatus(error: unknown) {
  return error && typeof error === 'object' && 'status' in error ? error.status : undefined;
}
export function callbackCompensationErrorMessage(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object') return fallback;
  const data = 'data' in error ? error.data : error;
  const code = data && typeof data === 'object' && 'code' in data ? data.code : undefined;
  return typeof code === 'string' && Object.prototype.hasOwnProperty.call(errors, code) ? errors[code] : fallback;
}
export function callbackCompensationReason(code: string): string {
  if (Object.prototype.hasOwnProperty.call(reasons, code)) return reasons[code];
  return '服务端尚未确认安全投递条件';
}

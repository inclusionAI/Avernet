/** Keep deployment failures explicit without exposing raw upstream messages. */
/** 后端信封里的领域错误码（无则 null），供调用方按码分流。 */
export function rsiDomainCode(error: unknown): string | null {
  return (
    (error as { data?: { error?: { code?: string } } } | null | undefined)?.data?.error?.code ?? null
  );
}

export function rsiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.startsWith('当前会话未解析出用户身份')) {
    return error.message;
  }
  if (error instanceof Error && 'status' in error && error.status === 401) {
    return '登录凭证未被服务接受（401）。请在当前浏览器、当前站点重新登录后核对任务记录；重试会复用原幂等键。';
  }
  if (error instanceof Error && 'status' in error && error.status === 403) {
    return '当前身份无权操作此 Bot（403）。请核对登录账号与 Bot 归属，不要反复提交。';
  }
  const domainCode = rsiDomainCode(error);
  if (domainCode === 'evolution_app_not_granted') {
    return '进化服务无法访问这个 Bot。请确认 Bot 仍然存在，并检查 evolution 应用是否已获得访问授权，再配置或启动 RSI。';
  }
  if (domainCode === 'no_genome_baseline') {
    return '这个 Bot 还没有基因组基线（没有 active 版本）。保存配置时会自动以当前配置建立初始基线；请重试，或换用有权限的账号。';
  }
  if (domainCode === 'candidate_ambiguous') {
    return '同一候选哈希出现在这个 Bot 的多个运行中，无法唯一定位。请从对应运行的候选列表与证据事件核对，不要重复提交。';
  }
  if (domainCode === 'already_decided') {
    return '该候选已有评审结论，不能重复审批。界面会刷新为最新状态。';
  }
  if (domainCode === 'not_reviewable') {
    return '该候选不在评审队列中（可能已自动晋级或不可晋升），无需人工审批。';
  }
  if (domainCode === 'idempotency_key_reused') {
    return '同一幂等键提交了不同的审批内容，已被拒绝。请刷新核对评审状态后再操作。';
  }
  if (error instanceof Error && 'status' in error && error.status === 409) {
    return '操作冲突（409）。保存配置需重新读取后再编辑；提交任务请先核对已有运行，重试会复用原幂等键。';
  }
  if (error instanceof Error && 'status' in error && error.status === 503) {
    return '独立 Evolution 服务未接通或暂不可用，当前无法确认任务状态。请检查部署与路由，不要重复创建任务。';
  }
  return fallback;
}

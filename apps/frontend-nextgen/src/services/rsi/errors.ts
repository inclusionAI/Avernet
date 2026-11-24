/** Keep deployment failures explicit without exposing raw upstream messages. */
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
  const domainCode = (error as { data?: { error?: { code?: string } } }).data?.error?.code;
  if (domainCode === 'no_genome_baseline') {
    return '这个 Bot 还没有基因组基线（从未创建过版本），无法配置或启动 RSI。请先为它创建第一个版本（capture + promote），或换一个已有版本的 Bot。';
  }
  if (error instanceof Error && 'status' in error && error.status === 409) {
    return '操作冲突（409）。保存配置需重新读取后再编辑；提交任务请先核对已有运行，重试会复用原幂等键。';
  }
  if (error instanceof Error && 'status' in error && error.status === 503) {
    return '独立 Evolution 服务未接通或暂不可用，当前无法确认任务状态。请检查部署与路由，不要重复创建任务。';
  }
  return fallback;
}

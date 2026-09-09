/**
 * 协作权限页加载范围：由当前工作身份决定页面实际需要请求哪些数据源。
 * 页面展示早已按工作身份收敛（用户身份只渲染身份卡、Bot 身份只渲染命中的 1 张卡），
 * 请求面沿用同一条可见性规则，避免为不渲染的 Bot 付出 mine 之外的 N+1 读取。
 */
export type CollaborationPrivacyLoadScope =
  /** 用户身份(或工作身份尚未选定)：只解析当前用户，不请求 Bot 列表与其画像公开配置。 */
  | { target: 'currentUser' }
  /** Bot 工作身份：读取 Bot 列表，但只对 botId 命中的 Bot 做部门回显与 BCSFuse config 读取。 */
  | { target: 'activeBot'; botId: string }
  /**
   * 遗留全量：对列表内所有 Bot 做 hydrate。仅供未显式传 loadScope 的既有调用与全量回归测试使用，
   * 生产入口(useCollaborationPrivacy)必须传 currentUser / activeBot，不得依赖此默认值。
   */
  | { target: 'allBots' };

/** Work 区 Bot 身份 ID 可能携带 `:` 后缀（如 `bot-real-1:447147`），比较前归一到 Bot UUID 部分。 */
export function normalizeBotIdentityId(id: string): string {
  const separator = id.indexOf(':');
  return separator >= 0 ? id.slice(0, separator) : id;
}

/** Bot 列表项与当前工作身份是否指向同一个 Bot。 */
export function matchesBotIdentity(botId: string, identityId: string): boolean {
  return botId === identityId || normalizeBotIdentityId(botId) === normalizeBotIdentityId(identityId);
}

/** 由当前工作身份推导加载范围；身份未就绪时按用户身份处理（此时页面只渲染身份卡）。 */
export function resolvePrivacyLoadScope(
  identity: { id: string; kind: 'user' | 'bot' } | null | undefined,
): CollaborationPrivacyLoadScope {
  if (identity?.kind === 'bot') return { target: 'activeBot', botId: identity.id };
  return { target: 'currentUser' };
}

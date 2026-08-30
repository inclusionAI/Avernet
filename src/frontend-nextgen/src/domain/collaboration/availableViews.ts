export const TEST_USER_IDENTITY_ID = 'test-user';

export type WorkspaceView = 'chat' | 'group';

/** 按身份返回可用 tab:用户双 tab;Bot 仅协作群;测试用户仅会话。 */
export function getAvailableViews(identity: { id: string; kind: 'user' | 'bot' } | null): WorkspaceView[] {
  if (!identity) return ['group'];
  if (identity.kind === 'bot') return ['group'];
  if (identity.id === TEST_USER_IDENTITY_ID) return ['chat'];
  return ['chat', 'group'];
}

/** 当前 view 不在可用集时,回退到可用集第一项。 */
export function clampView(views: WorkspaceView[], current: WorkspaceView): WorkspaceView {
  return views.includes(current) ? current : views[0];
}

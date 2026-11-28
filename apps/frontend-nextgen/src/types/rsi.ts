/** ownerId selects the owning entity; it is never authorization proof. */
export interface RsiScope {
  botId: string;
  ownerId: string;
  /**
   * 当前会话已解析出的用户身份，作为 `user_id` 查询参数发送。
   * 后端这些路由把 `user_id` 作为必填参数校验（缺失即 422），所以不能只依赖
   * `injectUserId` 读全局 identity store —— 聊天页不一定填过它。
   */
  actorId?: string;
}

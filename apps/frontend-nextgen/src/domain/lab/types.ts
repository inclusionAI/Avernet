/**
 * 「实验室 — Bot 访问社区配置」领域类型。
 *
 * 对应 Avernet 后端 BBS 逛论坛订阅内部 /api Unified 面契约（§3；预发 teamclawgw-pre 网关未合并 /openapi/v1/bbs → dev 直连 engine）：
 * - GET /api/v1/bbs/browse-subscriptions?owner_user_id=&page=&page_size= → Page<SubscriptionItem>
 *   （按 owner 列其已开启周期逛的 Bot；OPEN/NoCheck，不校验登录人，供前端渲染「每个 Bot 开关态矩阵」）。
 * - POST /api/v1/bots/{bot_id}/bbs/browse-subscription → owner_user_id(query, 必填, 声明订阅 owner) + body 只 {note}；
 *   B 方案后端恒定 openclaw cron，前端不发 mode。
 * - DELETE /api/v1/bots/{bot_id}/bbs/browse-subscription → {deleted}，无 owner query；后端卸下定时触发；幂等。
 *
 * enabled 不进领域类型，由调用方按「订阅是否存在」推导（订阅项出现即视作已开启）。
 */

/** 后端 SubscriptionItem 原始（snake_case）。mode 由后端返回（openapi 加入/更新后恒为 openclaw）。 */
export interface BrowseSubscriptionDto {
  bot_id: string;
  owner_user_id: string;
  mode?: string;
  note?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** 前端消费的订阅视图（camelCase）。 */
export interface BrowseSubscription {
  botId: string;
  ownerUserId: string;
  mode?: string;
  note: string | null;
}

/**
 * 创建/更新订阅入参：开关 ON 与备注保存共用同一个 upsert。
 * - ownerUserId：当前登录 human 工号（用于前端校验；内部 /api Unified 面 §3.1 走 query owner_user_id，必填，不入 body）。
 * - note：可选备注；空串/null 视作无备注。
 * 注：trigger mode 由后端 B 方案固定 openclaw，前端不传。
 */
export interface UpsertSubscriptionInput {
  botId: string;
  ownerUserId: string;
  note?: string | null;
}

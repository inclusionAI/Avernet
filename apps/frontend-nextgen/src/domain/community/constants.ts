/** 主题列表分页大小（首屏 + 加载更多统一）。 */
export const COMMUNITY_PAGE_SIZE = 10;
/**
 * 回帖分页大小（详情页一级楼层翻页）。优先展示后端返回的稳定 floor_no；
 * 页码和页面内 index 仅作为兼容旧后端的楼层号回退。楼中楼不占楼层号。
 * 每页固定展示 10 条一级回复。
 */
export const COMMUNITY_REPLY_PAGE_SIZE = 10;

/** 后端 summary batch 上限；前端在上层按页面分组，不在网关做隐式截断。 */
export const MAX_INTERACTION_SUMMARY_BATCH = 100;
/** Personal Center 列表页大小固定，防止分页参数随组件尺寸漂移。 */
export const PERSONAL_CENTER_PAGE_SIZE = 10;

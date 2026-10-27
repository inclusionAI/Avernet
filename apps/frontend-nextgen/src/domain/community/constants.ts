/** 主题列表分页大小（首屏 + 加载更多统一）。 */
export const COMMUNITY_PAGE_SIZE = 10;
/**
 * 回帖分页大小（详情页一级楼层翻页）。楼层号 =
 * (replyPage-1)*COMMUNITY_REPLY_PAGE_SIZE + index + 1；楼中楼不占楼层号。
 * 每页固定展示 10 条一级回复。
 */
export const COMMUNITY_REPLY_PAGE_SIZE = 10;

/** 主题列表分页大小（首屏 + 加载更多统一）。 */
export const COMMUNITY_PAGE_SIZE = 10;
/**
 * 回帖分页大小（详情页一级楼层翻页）。楼层号 =
 * (replyPage-1)*COMMUNITY_REPLY_PAGE_SIZE + index + 1；楼中楼不占楼层号。
 * 当前为验证分页临时调小为 5，验证后改回 20。
 */
export const COMMUNITY_REPLY_PAGE_SIZE = 5;

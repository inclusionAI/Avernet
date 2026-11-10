import { useCallback, useRef, useState } from 'react';

/**
 * 协作广场列表的分页状态（从 useCollaborationSquareList 拆出以控制 hook 文件规模）：
 * - currentBotOffset/currentGroupOffset：BCS 原始 offset 游标（上一页返回的 nextOffset），群独立；
 * - reloadPendingRef：新查询防抖窗口标记——effect 体同步置位、首屏真正开始时清除，
 *   期间旧游标已复位、hasMore 已收起，续读整体暂缓（避免「新关键词 + 旧游标」或重复首屏）；
 * - hasMore/hasMoreRef/applyHasMore：分页标记及其同步镜像——setBots（store 同步通知）与
 *   setHasMore（批处理）可能分属两次渲染，深链定位与 loadMore 守卫需同轮次读到实时值。
 */
export function useCollaborationSquarePagination() {
  const currentBotOffset = useRef(0);
  const currentGroupOffset = useRef(0);
  const reloadPendingRef = useRef(false);
  const [hasMore, setHasMore] = useState(false);
  const hasMoreRef = useRef(false);
  const applyHasMore = useCallback((value: boolean) => {
    hasMoreRef.current = value;
    setHasMore(value);
  }, []);
  return { currentBotOffset, currentGroupOffset, reloadPendingRef, hasMore, hasMoreRef, applyHasMore };
}

import { listSessionMessages } from '@/services/backendApi/collaboration/sessionController';
import type { ChatMessage } from '@tc-chat/core';
import { mapGroupHistoryMessages } from './groupMessageMapper';

/** 协作群历史消息分页大小：与旧「我的协作」一致（open-claw MESSAGE_PAGE_SIZE=50）。 */
const GROUP_MESSAGE_PAGE_SIZE = 50;

/** 取已映射消息中最旧的时间戳（毫秒），作为 `before` 游标向更早翻页；无有效时间戳返回 null。 */
function getOldestTimestamp(messages: ChatMessage[]): number | null {
  let oldest: number | null = null;
  for (const message of messages) {
    const ts = Number(message.createdAt);
    if (!Number.isFinite(ts) || ts <= 0) continue;
    if (oldest === null || ts < oldest) oldest = ts;
  }
  return oldest;
}

/**
 * 协作群历史消息分页器——从 GroupChatProvider 拆分，专注向上翻页游标管理：
 *  - 首屏拉取最新一页（不带 before），后端返回新→旧降序，翻转为旧→新升序后交由 Mapper 聚合
 *  - 以当前最旧时间戳为 `before` 游标请求更早一页；游标未前进即视作后端已无更早消息
 *  - 满页（≥ page size）置 hasMore=true，不足一页置 hasMore=false，避免同 before 死循环
 *
 * 与旧「我的协作」群聊历史加载逻辑对齐；view_bot_id = 当前身份 identityId 供后端按视角返回。
 */
export class GroupChatHistoryPaginator {
  private readonly sessionId: string;
  private readonly identityId: string;
  /** 是否还有更早的历史消息可加载。 */
  private hasMoreFlag = false;
  /** 是否正在向上翻页加载更早的消息。 */
  private loadingMoreFlag = false;
  /** 向上翻页游标：当前已加载消息中最旧的时间戳（毫秒），作为下次请求的 `before`。 */
  private cursor: number | null = null;

  constructor(sessionId: string, identityId: string) {
    this.sessionId = sessionId;
    this.identityId = identityId;
  }

  /** 是否还有更早的历史消息可加载（供 Hook / UI 控制顶部「加载更多」显隐）。 */
  get hasMore(): boolean {
    return this.hasMoreFlag;
  }

  /** 是否正在向上翻页加载更早的消息（供 UI 展示顶部加载指示器）。 */
  get isLoadingMore(): boolean {
    return this.loadingMoreFlag;
  }

  /**
   * 首屏加载：重置游标，拉取最新一页（不带 before）。
   * 返回旧→新升序的 ChatMessage[]；满页置 hasMore=true。
   */
  async loadLatest(): Promise<ChatMessage[]> {
    this.cursor = null;
    this.hasMoreFlag = false;
    const { mapped, rawCount } = await this.fetchPage();
    this.cursor = getOldestTimestamp(mapped);
    // 拉满一页说明后端可能还有更早消息；不足一页则视为已到顶。
    this.hasMoreFlag = rawCount >= GROUP_MESSAGE_PAGE_SIZE;
    return mapped;
  }

  /**
   * 向上翻页加载更早的历史消息——以当前最旧时间戳为 `before` 游标请求上一页，
   * 返回更早的 ChatMessage[]（旧→新升序），由 Hook 前置拼接到 SDK chat.messages。
   *
   * 游标未前进（拉到的最旧时间戳不早于当前游标）即视作后端已无更早消息，
   * 置 hasMore=false 停止翻页，避免同 `before` 死循环。
   * 加载失败不修改游标 / hasMore，保留原值允许重试。
   */
  async loadOlder(): Promise<ChatMessage[]> {
    if (!this.hasMoreFlag || this.loadingMoreFlag || this.cursor === null) {
      return [];
    }
    this.loadingMoreFlag = true;
    try {
      const prevCursor = this.cursor;
      const { mapped, rawCount } = await this.fetchPage(prevCursor);
      const newCursor = getOldestTimestamp(mapped);
      if (newCursor === null || newCursor >= prevCursor) {
        // 游标未前进：边界消息被重复返回（含 inclusive before）或已无更早消息 → 停止翻页。
        this.hasMoreFlag = false;
      } else {
        this.cursor = newCursor;
        this.hasMoreFlag = rawCount >= GROUP_MESSAGE_PAGE_SIZE;
      }
      return mapped;
    } finally {
      this.loadingMoreFlag = false;
    }
  }

  /**
   * 拉取一页历史消息（GET /openapi/v1/collaboration/sessions/{sid}/messages）。
   * 后端返回扁平数组（新→旧，降序），翻转为旧→新升序后交由 Mapper 聚合。
   * `before` 为当前最旧时间戳游标；缺省拉最新一页（首屏）。view_bot_id =
   * 当前身份 identityId（bot/human 的 bot_id），供后端按身份视角返回消息。
   */
  private async fetchPage(before?: number): Promise<{ mapped: ChatMessage[]; rawCount: number }> {
    const params: { limit: number; view_bot_id: string; include_pending: boolean; before?: string } = {
      limit: GROUP_MESSAGE_PAGE_SIZE,
      view_bot_id: this.identityId,
      include_pending: true,
    };
    if (before !== undefined) params.before = String(before);
    const resp = await listSessionMessages(this.sessionId, params);
    const rawCount = resp?.data?.length ?? 0;
    const items = [...(resp?.data ?? [])].reverse();
    const mapped = mapGroupHistoryMessages(items, this.sessionId);
    return { mapped, rawCount };
  }
}

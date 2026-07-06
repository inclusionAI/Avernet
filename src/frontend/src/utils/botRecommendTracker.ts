/**
 * Bot 智能推荐埋点 Tracker
 *
 * 遵循后端埋点方案约定
 *
 * 核心事件：
 * 1. query_result - /recommend 接口返回后上报
 * 2. bot_select - 用户点击选择/移除 Bot 时上报
 *
 * 关联方式：通过 trace_id 关联请求与选择
 */

import { useUserStore } from '@/stores/userStore';
import { Tracert } from './tracert';

/** query_result 事件参数 */
export interface QueryResultEvent {
  /** 会话ID */
  session_id: string;
  /** 追踪ID，后端返回 */
  trace_id_n: string;
  /** 查询类型 */
  type: 'search' | 'recommend';
  /** 搜索关键词 */
  keyword?: string;
  /** 返回Bot数量 */
  bot_count: number;
  /** 是否有结果 */
  has_result: 'true' | 'false';
  /** Bot ID 列表（逗号分隔的字符串） */
  bot_list: string;
  /** 请求耗时（毫秒） */
  latency_ms: number;
  /** 时间戳 */
  event_time: number;
}

/** bot_select 事件参数 */
export interface BotSelectEvent {
  /** 会话ID */
  session_id: string;
  /** 追踪ID，关联 query_result */
  trace_id_n: string;
  /** Bot ID */
  bot_id: string;
  /** 操作类型 */
  action: 'add' | 'remove';
  /** 列表位置（0-based） */
  position: number;
  /** 时间戳 */
  event_time: number;
}

/** 生成 session_id */
function generateSessionId(): string {
  const userId = useUserStore.getState().userId || '';
  return `sess_${Date.now()}_${Math.random()
    .toString(36)
    .slice(2, 10)}_${userId}`;
}

/**
 * Bot 智能推荐埋点 Tracker 类
 *
 * 使用方式：
 * ```typescript
 * const tracker = new BotRecommendTracker();
 *
 * // 1. 搜索结果返回后上报
 * tracker.onQueryResult({
 *   trace_id: response.trace_id,
 *   type: response.type,
 *   keyword: searchKeyword,
 *   bot_count: bots.length,
 *   latency_ms: 450
 * });
 *
 * // 2. 用户选择 Bot 时上报
 * tracker.onBotSelect(botId, 'add', position);
 * ```
 */
export class BotRecommendTracker {
  private sessionId: string;
  private currentTraceId: string | null = null;
  private currentBots: { bot_id: string; position: number }[] = [];

  constructor(sessionId?: string) {
    this.sessionId = sessionId || generateSessionId();
  }

  /**
   * 获取当前 session_id
   */
  getSessionId(): string {
    return this.sessionId;
  }

  /**
   * 获取当前 trace_id
   */
  getCurrentTraceId(): string | null {
    return this.currentTraceId;
  }

  /**
   * 搜索结果返回后调用，上报 query_result 事件
   */
  onQueryResult(params: {
    trace_id?: string;
    type?: 'search' | 'recommend';
    keyword?: string;
    bot_count: number;
    latency_ms: number;
    bots?: { bot_id: string }[];
  }) {
    // 保存 trace_id 用于后续选择关联
    this.currentTraceId = params.trace_id || null;
    this.currentBots =
      params.bots?.map((bot, index) => ({
        bot_id: bot.bot_id,
        position: index,
      })) || [];

    // 构造埋点事件数据
    const eventData: QueryResultEvent = {
      session_id: this.sessionId,
      trace_id_n: params.trace_id || '',
      type: params.type || 'recommend',
      keyword: params.keyword,
      bot_count: params.bot_count,
      has_result: params.bot_count > 0 ? 'true' : 'false',
      bot_list: params.bots?.map((bot) => bot.bot_id).join(',') || '',
      latency_ms: params.latency_ms,
      event_time: Date.now(),
    };

    // 上报埋点（使用 Tracert.click 发送自定义事件）
    Tracert.click('a5377.b184510.c472175.d700751', {
      ...eventData,
    });

    console.log('[BotRecommendTracker] query_result:', eventData);
  }

  /**
   * 用户选择/移除 Bot 时调用，上报 bot_select 事件
   */
  onBotSelect(botId: string, action: 'add' | 'remove', position?: number) {
    // 如果没有 trace_id，使用空字符串（后端可能还没返回，但埋点仍要上报）
    const traceId = this.currentTraceId || '';

    // 如果没有传入 position，尝试从当前列表中查找
    let finalPosition = position;
    if (finalPosition === undefined) {
      const found = this.currentBots.find((bot) => bot.bot_id === botId);
      finalPosition = found?.position ?? -1;
    }

    const eventData: BotSelectEvent = {
      session_id: this.sessionId,
      trace_id_n: traceId,
      bot_id: botId,
      action,
      position: finalPosition,
      event_time: Date.now(),
    };

    // 上报埋点
    Tracert.click('a5377.b184510.c472175.d700750', {
      ...eventData,
    });

    console.log('[BotRecommendTracker] bot_select:', eventData);
  }

  /**
   * 重置当前查询状态（切换搜索词时调用）
   */
  resetQuery() {
    this.currentTraceId = null;
    this.currentBots = [];
  }

  /**
   * 生成新的 session_id（用于新会话）
   */
  newSession() {
    this.sessionId = generateSessionId();
    this.currentTraceId = null;
    this.currentBots = [];
  }
}

/** 全局 tracker 实例 */
let globalTracker: BotRecommendTracker | null = null;

/**
 * 获取全局 BotRecommendTracker 实例
 */
export function getBotRecommendTracker(): BotRecommendTracker {
  if (!globalTracker) {
    globalTracker = new BotRecommendTracker();
  }
  return globalTracker;
}

/**
 * 重置全局 tracker（用于新会话）
 */
export function resetBotRecommendTracker(): BotRecommendTracker {
  globalTracker = new BotRecommendTracker();
  return globalTracker;
}

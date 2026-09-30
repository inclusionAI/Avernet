/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bot 状态轮询共享工具
 *
 * bootstrap（initBot.ts）和运行时（useBot.ts）均需要轮询 Bot 状态，
 * 本模块提取核心轮询循环，通过回调和结果类型让调用方自行处理各自的副作用。
 */

import type {
  Bot,
  BotStatusResponse,
} from '@/services/backend-api/BotController';
import * as BotController from '@/services/backend-api/BotController';
import { resolveBotOwnerId } from './activeBotContext';

const DEFAULT_POLL_INTERVAL = 3000; // 3 秒
const DEFAULT_POLL_TIMEOUT = 5 * 60 * 1000; // 5 分钟

/** getBotStatus 返回的 data 字段类型 */
export type BotStatusData = BotStatusResponse['data'];

/** 轮询结果（discriminated union） */
export type PollBotResult =
  | { outcome: 'active'; bot: Bot }
  | { outcome: 'failed'; message: string }
  | { outcome: 'timeout' }
  | { outcome: 'cancelled' }
  | { outcome: 'aborted' }
  | { outcome: 'error'; error: unknown; message: string };

export interface PollBotOptions {
  timeout?: number;
  interval?: number;
  /**
   * 协作场景的 owner_id。
   * 启动期 bot 还没进 store，调用方需要显式传入；运行时可省略，
   * 由 botPolling 内部通过 `resolveBotOwnerId(botId)` 反查兜底。
   */
  ownerId?: string;
  /** 每轮轮询前调用，返回 false 取消轮询（outcome: cancelled） */
  shouldContinue?: () => boolean;
  /**
   * 每轮获取到 status data 后调用，可用于：
   * - 实时更新 store 状态
   * - 自定义终止条件（如 deviceStuck 检测）
   * 返回 false 将终止轮询（outcome: aborted）
   */
  onPollData?: (data: BotStatusData) => boolean;
}

function sleep(ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });
}

/**
 * 轮询 Bot 状态直到终态（ACTIVE / FAILED / 超时 / 取消）
 *
 * 纯数据层：不包含任何 UI 副作用（toast / step 更新 / deviceStuck），
 * 由调用方根据返回的 PollBotResult 自行处理。
 */
export async function pollBotUntilSettled(
  botId: string,
  options?: PollBotOptions,
): Promise<PollBotResult> {
  const timeout = options?.timeout ?? DEFAULT_POLL_TIMEOUT;
  const interval = options?.interval ?? DEFAULT_POLL_INTERVAL;
  const deadline = Date.now() + timeout;
  // 协作场景：owner_id 优先用调用方传入的；未传则从 store 反查（启动期可能拿不到，
  // 取不到时不传，由后端按当前登录用户兜底——与原 Controller pickOwnerId 行为一致）
  const owner_id = options?.ownerId ?? resolveBotOwnerId(botId);

  while (Date.now() < deadline) {
    // 检查取消
    if (options?.shouldContinue && !options.shouldContinue()) {
      return { outcome: 'cancelled' };
    }

    try {
      const res = await BotController.getBotStatus(
        { bot_id: botId, owner_id },
        { skipErrorHandler: true },
      );

      // API 返回 success: false（BotStatusResponse 类型未声明 message，用 any 取）
      if (!(res as any).success) {
        const msg: string = (res as any).message || '查询 Bot 状态失败';
        return { outcome: 'error', error: new Error(msg), message: msg };
      }

      // data 为 null（服务端异常），跳过本轮继续
      if (!res.data) {
        await sleep(interval);
        continue;
      }

      // 让调用方检查 status data
      if (options?.onPollData) {
        const shouldContinue = options.onPollData(res.data);
        if (!shouldContinue) {
          return { outcome: 'aborted' };
        }
      }

      const { bot_status, error_message } = res.data;
      const { start_status, start_message } = res.data.ext ?? {};

      // 容器启动失败 → Bot 无法启动，立即终止（不等 bot_status 更新）
      if (start_status === 'FAILED') {
        return {
          outcome: 'failed',
          message: start_message || error_message || 'Bot 容器启动失败',
        };
      }

      // ACTIVE — 获取完整详情
      if (bot_status === 'ACTIVE') {
        const detail = await BotController.getBotDetail({
          bot_id: botId,
          owner_id,
        });
        if (detail.success && detail.data) {
          return { outcome: 'active', bot: detail.data };
        }
        // 详情获取失败但 Bot 已激活，等待下轮重试
        await sleep(interval);
        continue;
      }

      // FAILED
      if (bot_status === 'FAILED') {
        return {
          outcome: 'failed',
          message: error_message || 'Bot 激活失败',
        };
      }

      // PENDING — 继续轮询
      await sleep(interval);
    } catch (error: any) {
      // 【方案二】Bot 已被删除（404），优雅处理为取消轮询，避免报错
      const statusCode = error?.response?.status || error?.status;
      if (statusCode === 404) {
        console.log(
          `[botPolling] Bot ${botId} not found (deleted), cancelling poll`,
        );
        return { outcome: 'cancelled' };
      }

      const msg: string =
        error?.data?.message ||
        error?.response?.data?.message ||
        error?.message ||
        '查询 Bot 状态失败';
      return { outcome: 'error', error, message: msg };
    }
  }

  return { outcome: 'timeout' };
}

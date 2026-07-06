/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bot Context - Bot 种类上下文 + 埋点公共字段合并
 *
 * 提供：
 *   1. BotKind 7 类枚举 + getCurrentBotKindContext() / getCurrentBotKind()
 *   2. mergeBotContextParams() — 合并 5 个公共字段供 Tracert 埋点使用
 *
 * 统一供业务埋点与 requestConfig.ts 全局错误上报使用，
 * 保证 engine_type / template_type / bot_kind 三个维度口径一致。
 *
 * 关联设计文档：docs/架构与规范/埋点/应用Bot埋点方案.md
 *
 * ─── bot_kind 枚举规则 ─────────────────────────────────────────
 *
 * | bot_kind            | engine_type   | template_type        |
 * |---------------------|---------------|----------------------|
 * | openclaw            | openclaw      | -                    |
 * | personal_aicoding   | aicoding      | personalCoding / 空  |
 * | normal_cc           | claude_code   | normalCC / 空 / 其它 |
 * | app_coding          | claude_code   | applicationCoding    |
 * | moltis              | moltis        | -                    |
 * | hermes              | hermes        | -                    |
 * | unknown             | （无 / 未知） | -                    |
 *
 * 取值逻辑：
 *   - 优先使用调用方传入的 botId 推断（适用于操作目标 Bot ≠ 当前活跃 Bot 的场景）
 *   - 否则取当前 activeBot
 *   - engine_type 取自 connectionStore.activeEngine（最贴合"正在跟谁交互"的语义），
 *     兜底取 bot.active_engine
 *   - template_type 取自 bot.template_type
 *   - bot_kind 由 engine + template 推断
 */

import { useBotStore } from '@/stores/botStore';
import { useConnectionStore } from '@/stores/connectionStore';
import { useUserStore } from '@/stores/userStore';

export type BotKind =
  | 'openclaw'
  | 'personal_aicoding'
  | 'normal_cc'
  | 'app_coding'
  | 'moltis'
  | 'hermes'
  | 'unknown';

export interface BotKindContext {
  bot_id: string;
  engine_type: string;
  template_type: string;
  bot_kind: BotKind;
}

/** template_type 后端常量（与 BotController / CreateBotModal 保持一致） */
const TEMPLATE_APP_CODING = 'applicationCoding';

/**
 * 根据 engine + template 推断 bot_kind
 *
 * 见文件头注释里的枚举规则表。
 */
function inferBotKind(
  engineType: string | undefined,
  templateType: string | undefined,
): BotKind {
  switch (engineType) {
    case 'openclaw':
      return 'openclaw';
    case 'aicoding':
      // 个人 AICoding：template_type 通常是 personalCoding，缺省也归此类
      return 'personal_aicoding';
    case 'claude_code':
      // 应用 Coding 与普通 CC 共用 claude_code 引擎，按 template 区分
      return templateType === TEMPLATE_APP_CODING ? 'app_coding' : 'normal_cc';
    case 'moltis':
      return 'moltis';
    case 'hermes':
      return 'hermes';
    default:
      return 'unknown';
  }
}

/**
 * 获取当前 Bot 上下文（同步，可在非组件代码中使用）
 *
 * @param botId 可选；不传则取当前 activeBot
 */
export function getCurrentBotKindContext(botId?: string): BotKindContext {
  const botState = useBotStore.getState();
  const connectionState = useConnectionStore.getState();

  // 1. 解析 bot
  const resolvedBotId = botId ?? botState.activeBotId ?? '';
  const bot = resolvedBotId
    ? botState.bots.find((b) => b.bot_id === resolvedBotId)
    : undefined;

  // 2. engine_type 优先用当前激活引擎，兜底用 bot.active_engine
  const engineType =
    connectionState.activeEngine ||
    (bot as { active_engine?: string } | undefined)?.active_engine ||
    '';

  // 3. template_type
  const templateType =
    (bot as { template_type?: string } | undefined)?.template_type ?? '';

  // 4. bot_kind 推断
  const botKind = inferBotKind(engineType, templateType);

  return {
    bot_id: resolvedBotId,
    engine_type: engineType,
    template_type: templateType,
    bot_kind: botKind,
  };
}

/** 仅获取 bot_kind（兜底场景：error handler 不关心 bot_id） */
export function getCurrentBotKind(): BotKind {
  return getCurrentBotKindContext().bot_kind;
}

// ─── 埋点公共字段合并 ──────────────────────────────────────────

export interface MergeBotContextParamsOptions {
  /**
   * 显式指定目标 Bot ID
   * 不传则使用当前 activeBot；创建场景下 bot 还未存在，可不传
   */
  botId?: string;
}

/**
 * 合并 5 个公共字段（user_id / bot_id / engine_type / template_type / bot_kind）
 * 与业务扩展参数，业务参数优先级更高（允许显式覆盖）。
 *
 * 用法示例见业务侧代码（AntCodeProjectSelect / DomainBotSelect / CreateBotModal 等）。
 */
export function mergeBotContextParams(
  bizParams?: Record<string, unknown>,
  options?: MergeBotContextParamsOptions,
): Record<string, unknown> {
  const ctx = getCurrentBotKindContext(options?.botId);
  const userId = useUserStore.getState().userId || '';

  return {
    user_id: userId,
    bot_id: ctx.bot_id,
    engine_type: ctx.engine_type,
    template_type: ctx.template_type,
    bot_kind: ctx.bot_kind,
    ...(bizParams || {}),
  };
}

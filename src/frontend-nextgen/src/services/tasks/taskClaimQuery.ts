import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { isEnvelopeSuccessAnyDialect } from '@/services/backendApi/types';

/**
 * 查询当前会话所属 Bot 是否开启「任务认领」（task_claim_mode）。
 *
 * 供卡片「执行」前的门禁消费：只有明确读到 task_claim_mode===false 才阻断；
 * 查询/鉴权异常或业务错误信封无法判定时放行（避免误伤演示主流程）。
 *
 * 数据源必须用 mine(`/collaboration/bots/mine`)：它回填各 Bot 的 task_claim_mode，
 * 与协作权限页展示开关状态同源——用户在页面上看到开关已开启，门禁也读到开启。
 * 详情(`detail`)接口的 task_claim_mode 不保证回填，不能作为门禁依据。
 *
 * 信封判成败必须用 `isEnvelopeSuccessAnyDialect`：mine 走 BCS 方言（5 位成功码 20000），
 * `isEnvelopeSuccess` 仅认 6 位段（200000）会把成功误判为失败、导致开关已开启仍被阻断。
 *
 * 分层：Service（本文件）下沉 API Controller 编排，Hook 层不得直接调 Controller。
 */
export async function isBotTaskClaimEnabled(botId: string): Promise<boolean> {
  try {
    const env = await listMyBots({ kind: 'bot', limit: 100 });
    if (!isEnvelopeSuccessAnyDialect(env)) return true; // 业务错误信封：无法判定，放行不阻断
    const items = env?.data?.items;
    if (!Array.isArray(items)) return true; // 非预期结构：放行
    const bot = items.find((item) => item?.bot_id === botId);
    if (!bot) return true; // 不在当前用户可管理 Bot 列表：认领不适用，放行
    return Boolean(bot.task_claim_mode); // true→放行；false→阻断（与协作权限页映射一致）
  } catch {
    return true; // 网络/鉴权异常：放行
  }
}

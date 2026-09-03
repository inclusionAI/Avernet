// Bot 工坊引擎可选清单收口 Hook（筛选下拉 / 创建弹窗共用唯一事实源）。
//
// - Open Core 默认：仅 openclaw（不暴露 Claude Code 原生创建入口）；
// - internal overlay：extensions/internal.ts 覆盖为全量 4 项。
// 纯读 capability，无状态无副作用；引擎领域映射规则（服务化矩阵、cluster_name 等）
// 属后端契约事实，保留在 botWorkshopService/botMapper 全量，不随可见清单收窄。
import { getCapabilities, type BotEngineOption } from '@/capabilities';

export function useBotEngineOptions(): BotEngineOption[] {
  return getCapabilities().getBotEngineOptions().value;
}

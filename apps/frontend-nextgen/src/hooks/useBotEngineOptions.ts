// Bot 工坊引擎可选清单收口 Hook（筛选下拉 / 创建弹窗共用唯一事实源）。
//
// - Open Core 默认：openclaw + claude_code（原生 CC 直建入口，阿里云部署依赖，不可删除）；
// - internal overlay：extensions/internal.ts 覆盖为 4 项（CC 创建由 AgentCoding 接管）。
// 纯读 capability，无状态无副作用；引擎领域映射规则（服务化矩阵、cluster_name 等）
// 属后端契约事实，保留在 botWorkshopService/botMapper 全量，不随可见清单收窄。
import { getCapabilities, type BotEngineOption } from '@/capabilities';

export function useBotEngineOptions(): BotEngineOption[] {
  return getCapabilities().getBotEngineOptions().value;
}

// 模型契约已上移至 src/extensions/contracts/groupRouting.ts（collab-form Task 17 源切片类型完备）：
// collab 保留链不再 type-only 依赖真实 hooks 文件（useServiceBotRouting 属 pages 裁剪面）。
import type { ServiceBotRoutingModel } from '@/extensions/contracts/groupRouting';

/**
 * collab 形态的 service-bot 选路占位（无 TC Bot 分类服务）：
 * 恒 ready（不阻塞建群提交）、states 空（picker 过滤保持全量候选）、applyTags 原样透传。
 * 使用方与真实 hook 同签名，React hooks 规则满足（按构建恒定）。
 */
export function useNoopServiceBotRouting(): ServiceBotRoutingModel {
  return {
    states: {},
    ready: true,
    selectRoutingMode: () => {},
    selectStage: () => {},
    retry: async () => {},
    applyTags: <T extends { actor_id: string }>(participants: T[]) => participants,
  };
}

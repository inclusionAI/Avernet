import { submitTaskCardAction } from '@/services/tasks/taskCardBridge';
import { useCallback } from 'react';

/**
 * 任务卡片动作 Hook（Component → Hook → Service 收口）。
 *
 * TaskReadyCard / TaskMultiSelectCard 等展示卡片仅经此 Hook 发出语义动作
 * （执行 / 暂存 / 丢弃 / 修改 / 选择），不直接 import @/services/tasks/taskCardBridge，
 * 遵守 `Component → Hook → Service → API Controller` 分层。
 *
 * 底层 submitTaskCardAction 经宿主 ChatBridge 把动作交给当前会话/任务执行编排。
 * 返回稳定回调，便于卡片内 useEffect/useCallback 依赖。
 */
export function useTaskCardAction(): (content: string, extra?: Record<string, unknown>) => void {
  // 按 card 实际传入参数个数透传，缺省 extra 不追加 undefined，保持与直调 Service 一致的调用签名。
  return useCallback((content: string, extra?: Record<string, unknown>) => {
    if (extra === undefined) {
      submitTaskCardAction(content);
    } else {
      submitTaskCardAction(content, extra);
    }
  }, []);
}

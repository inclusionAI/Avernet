import { chatBridge } from '@/services/workspace/chatBridge';

/**
 * 公开任务卡片动作桥。
 *
 * TaskLoopCard 只负责展示和发出语义动作，不直接依赖 window.aixBridge 或内部卡片运行时；
 * 宿主的 ChatBridge 负责把动作交给当前会话/任务执行编排。
 */
export function submitTaskCardAction(content: string, extra?: Record<string, unknown>): void {
  chatBridge.submit(content, extra);
}

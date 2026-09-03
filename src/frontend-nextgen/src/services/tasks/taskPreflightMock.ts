import type { TaskComposerForm } from './taskMapper';

export interface TaskPreflightMockResult {
  matched: boolean;
  message: string;
}

const DEMO_OKR_PREFLIGHT_MESSAGE = `我已收到任务需求，正在进行分析。

需求分析：
这是一个包含 GMV 增长目标和大促营销场景的 OKR 任务，核心需要补齐完整的大促营销策略。

自我评估：
当前 Bot 无法独立完成该需求，原因是当前 Bot 不具备完整的大促营销策略制定能力。

需求匹配：
经分析，该任务需要“营销策略制定”和“大促活动策划”能力。

专家搜推：
已发现「大促营销策略专家 Bot」，其能力满足当前任务要求。

任务指派：
现将该任务指派给「大促营销策略专家 Bot」执行，后续将由任务协作中心启动任务流程。`;

function containsAny(text: string, keywords: string[]): boolean {
  return keywords.some((keyword) => text.includes(keyword));
}

/** 演示规则：同时命中 GMV 增长语义和大促营销场景时，模拟 OKR 前置判断。 */
export function isDemoOkrMarketingTask(form: TaskComposerForm): boolean {
  const text = [form.title, form.objective, form.instruction, form.background ?? ''].join('\n').toLowerCase();

  return (
    containsAny(text, ['gmv', '交易额', '销售额', '增长目标']) &&
    containsAny(text, ['大促', '双十一', '618', '营销活动', '活动营销'])
  );
}

/**
 * 演示用前置判断：只生成一条本地 assistant 回复，不请求 Bot，也不请求后端。
 * 正式 execute 仍由调用方继续走现有 executeTaskService。
 */

export interface StreamMockMessageOptions {
  chunkSize?: number;
  intervalMs?: number;
}

/** 以分块+延迟模拟聊天流式输出；回调由 UI 层负责更新本地 assistant 消息。 */
export async function streamMockMessage(
  content: string,
  onUpdate: (partial: string, done: boolean) => void,
  { chunkSize = 10, intervalMs = 80 }: StreamMockMessageOptions = {},
): Promise<void> {
  if (!content) {
    onUpdate('', true);
    return;
  }

  onUpdate('', false);
  for (let index = 0; index < content.length; index += chunkSize) {
    const partial = content.slice(0, index + chunkSize);
    await new Promise<void>((resolve) => {
      setTimeout(resolve, intervalMs);
    });
    onUpdate(partial, index + chunkSize >= content.length);
  }
}

export async function runTaskPreflightMock(form: TaskComposerForm): Promise<TaskPreflightMockResult> {
  if (!isDemoOkrMarketingTask(form)) {
    return { matched: false, message: '' };
  }

  await new Promise<void>((resolve) => {
    setTimeout(resolve, 800);
  });

  return { matched: true, message: DEMO_OKR_PREFLIGHT_MESSAGE };
}

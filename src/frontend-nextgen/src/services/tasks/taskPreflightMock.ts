import type { TaskComposerForm } from './taskMapper';

export interface TaskPreflightMockResult {
  matched: boolean;
  message: string;
}

const DEMO_OKR_PREFLIGHT_MESSAGE = `我已收到任务需求，正在进行分析。

需求分析：
这是 18 周年店庆的运营增长任务，需在活动窗口（2026.10.15-11.15，共 32 天）内实现客流与护理双增长，在不伤老客口碑的前提下沉淀可复购会员，并严守促销预算与备货现金占用约束。

核心目标拆解：
O：18 周年店庆实现客流与护理双增长，不伤老客口碑，沉淀可复购会员。
KR：新客到店 1500、券核销 ≥ 1000，护理售卖 200 份（转化 ≥ 15%），新增会员 ≥ 800，客诉差评 ≤ 日常 1.2 倍。
经营约束：促销预算 ≤ 20 万、备货新增现金占用 ≤ 8 万、活动窗口 2026.10.15-11.15（32 天）。

自我评估：
当前 Bot 无法独立完成该需求，原因是当前 Bot 不掌握门店客流运营、护理品售卖与会员复购经营的完整决策链路，难以在预算与口碑约束下自主达成 KR。

专家搜推：
经搜推，未发现具备完整门店经营链路的能力型 Bot，自动执行无法满足该需求。

任务指派：
现将该任务指派给「店主Bot」执行，由店主统筹经营资源、把控口碑与预算，后续将由任务协作中心启动任务流程。`;

function containsAny(text: string, keywords: string[]): boolean {
  return keywords.some((keyword) => text.includes(keyword));
}

/** 演示规则：同时命中「周年店庆」语义和「客流/护理/会员复购增长」场景时，模拟 OKR 前置判断。 */
export function isDemoOkrMarketingTask(form: TaskComposerForm): boolean {
  const text = [form.title, form.objective, form.instruction, form.background ?? ''].join('\n').toLowerCase();

  return containsAny(text, ['周年', '店庆', '周年庆']) && containsAny(text, ['护理', '客流', '会员', '复购']);
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

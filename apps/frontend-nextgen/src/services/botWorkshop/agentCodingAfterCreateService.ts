import { updateBotExt } from '@/services/backendApi/bots/botController';
import type { AgentCodingTemplate } from './agentCodingTemplateService';

export interface AgentCodingAfterCreateAction {
  key: string;
  retryable: boolean;
}
export interface AfterCreateContext {
  botId: string;
  ownerId?: string;
  template: AgentCodingTemplate;
  values: Record<string, unknown>;
}
export interface AfterCreateFailure {
  action: AgentCodingAfterCreateAction;
  error: Error;
}

const text = (value: unknown) => (typeof value === 'string' ? value.trim() : '');
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

/**
 * open-claw 只根据 architect_name 字段推断架构 Bot 初始化，不要求模板工厂显式返回
 * afterCreate 声明。这里保留同样的兼容语义；动作仍进入固定白名单。
 */
function shouldUpdateArchitectBotExt(template: AgentCodingTemplate): boolean {
  return template.fields.some((field) => {
    const key = String(field.field_key ?? field.key ?? '')
      .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
      .toLowerCase();
    return key === 'architect_name';
  });
}

/**
 * 模板声明只能进入白名单，禁止模板配置携带任意 URL / 函数。
 * 架构域名称写入必须通过固定白名单动作进入，模板不能注入任意 URL 或函数。
 */
export function resolveAfterCreateActions(template: AgentCodingTemplate): AgentCodingAfterCreateAction[] {
  const raw =
    template.raw.afterCreate ??
    template.raw.after_create ??
    record(template.config).afterCreate ??
    record(template.config).after_create;
  const list = Array.isArray(raw) ? raw : raw ? [raw] : [];
  const declared = list
    .map((item) => (typeof item === 'string' ? item : record(item).key ?? record(item).action ?? ''))
    .map(String);
  // 模板能力保持 legacy 的隐式 afterCreate 语义，同时仍经过动作白名单。
  const declaredArchitectAction = declared.some((key) =>
    ['architect_name', 'architectName', 'update_architect_bot_ext'].includes(key),
  );
  if (!declaredArchitectAction && shouldUpdateArchitectBotExt(template)) {
    declared.push('update_architect_bot_ext');
  }
  return declared
    .filter((key) => ['architect_name', 'architectName', 'update_architect_bot_ext'].includes(key))
    .filter((key, index, all) => all.indexOf(key) === index)
    .map((key) => ({ key, retryable: true }));
}

async function runAction(action: AgentCodingAfterCreateAction, context: AfterCreateContext): Promise<void> {
  if (['architect_name', 'architectName', 'update_architect_bot_ext'].includes(action.key)) {
    const config = { ...record(context.template.config), ...context.values };
    const name = text(config.architect_name ?? config.arch_domain ?? context.values.architectName);
    if (!name) return;
    await updateBotExt(context.botId, { arch_domain: name, is_domain_bot: true });
    return;
  }
  throw new Error(`不支持的创建后动作：${action.key}`);
}

export async function runAfterCreateActions(
  context: AfterCreateContext,
  actions: AgentCodingAfterCreateAction[] = resolveAfterCreateActions(context.template),
): Promise<AfterCreateFailure[]> {
  const failures: AfterCreateFailure[] = [];
  for (const action of actions) {
    try {
      await runAction(action, context);
    } catch (cause) {
      failures.push({ action, error: cause instanceof Error ? cause : new Error(String(cause)) });
    }
  }
  return failures;
}

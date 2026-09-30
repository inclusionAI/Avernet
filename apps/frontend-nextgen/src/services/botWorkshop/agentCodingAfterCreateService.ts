import { updateBotExt } from '@/services/backendApi/bots/botController';
import { ensureDimaWorkspaceAndCron, shouldEnsureDimaWorkspace } from './agentCodingDimaService';
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
 * 模板声明只能进入白名单，禁止模板配置携带任意 URL / 函数。
 * 架构域名称写入和 DIMA/7×24 初始化都必须通过固定白名单动作进入，模板不能注入任意 URL 或函数。
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
  // 老版对模板能力做隐式 afterCreate；新版保留同样语义，同时仍经过动作白名单。
  if (shouldEnsureDimaWorkspace(template)) declared.push('ensure_dima_workspace');
  return declared
    .filter((key) =>
      ['architect_name', 'architectName', 'update_architect_bot_ext', 'ensure_dima_workspace'].includes(key),
    )
    .filter((key, index, all) => all.indexOf(key) === index)
    .map((key) => ({ key, retryable: true }));
}

async function runAction(action: AgentCodingAfterCreateAction, context: AfterCreateContext): Promise<void> {
  if (['architect_name', 'architectName', 'update_architect_bot_ext'].includes(action.key)) {
    const config = { ...record(context.template.config), ...context.values };
    const name = text(config.architect_name ?? config.arch_domain ?? config.architectName);
    if (!name) return;
    await updateBotExt(context.botId, { arch_domain: name, is_domain_bot: true });
    return;
  }
  if (action.key === 'ensure_dima_workspace') {
    await ensureDimaWorkspaceAndCron({
      botId: context.botId,
      ownerId: context.ownerId,
      template: context.template,
      values: context.values,
    });
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

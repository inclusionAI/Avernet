import { getCapabilities, type RuntimeEnvironment } from '@/capabilities';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from '../types';

export interface BotTemplateFieldOption {
  label?: string;
  value: string | number | boolean;
  description?: string;
}
export interface BotTemplateField {
  field_key: string;
  field_name?: string;
  field_type?: string;
  required?: boolean;
  value?: unknown;
  default_value?: unknown;
  placeholder?: string;
  description?: string;
  options?: BotTemplateFieldOption[];
  enum_values?: BotTemplateFieldOption[];
  [key: string]: unknown;
}
export type TemplateFactoryEnv = 'pre' | 'prod';

/**
 * 模板工厂环境与 TeamClaw 业务环境保持一致：
 * LOCAL/DEV/PRE 均传 `pre`，仅 PROD 传 `prod`。
 * 运行环境识别由 `getRuntimeEnvironment` capability 承载，Open Core 不写部署域名。
 */
export function getTemplateFactoryEnv(runtimeEnv?: RuntimeEnvironment): TemplateFactoryEnv {
  const currentEnv = runtimeEnv ?? getCapabilities().getRuntimeEnvironment().value;
  return currentEnv === 'PROD' ? 'prod' : 'pre';
}

export interface BotTemplateDto extends BackendUnknownRecord {
  id?: string | number;
  template_key?: string;
  template_name?: string;
  template_type?: string;
  engine_type?: string;
  version?: string;
  template_version_id?: string | number;
  template_category?: string;
  description?: string;
  custom_field_config?: BotTemplateField[];
  template_config?: BackendUnknownRecord;
  bot_template_config?: BackendUnknownRecord;
  /** 模板发布状态：pre_published/whitelist 表示白名单阶段。 */
  status?: string;
}

function unwrap(data: BackendApiEnvelope<unknown> | BackendUnknownRecord | unknown): unknown {
  if (data && typeof data === 'object' && 'data' in data) return (data as BackendApiEnvelope<unknown>).data;
  return data;
}

export async function listAgentCodingTemplates() {
  const response = await backendRequest<BackendApiEnvelope<unknown> | unknown>(
    '/template-factory/bot-templates/available-tc-list',
    {
      method: 'POST',
      data: { env: getTemplateFactoryEnv(), page: 1, pageSize: 50 },
      operation: 'list-agent-coding-templates',
    },
  );
  const payload = unwrap(response);
  const items = Array.isArray(payload)
    ? payload
    : payload && typeof payload === 'object' && Array.isArray((payload as BackendUnknownRecord).items)
    ? (payload as BackendUnknownRecord).items
    : [];
  return items as BotTemplateDto[];
}

import type { BotTemplateDto, BotTemplateField } from '@/services/backendApi/bots/botTemplateController';

// AgentCoding 模板能力位判定：自 services/botWorkshop/agentCodingTemplateService 迁入
// （外部 Bot 服务链在 botWorkshop 禁入前缀外复用，见 collab-form spec §4）。
// 本模块保持纯函数：除 type-only 依赖外不引入任何运行时 import。
export interface AgentCodingTemplate {
  key: string;
  versionId: string;
  name: string;
  description?: string;
  engine: string;
  templateType: string;
  source: 'official' | 'market';
  fields: BotTemplateField[];
  config: Record<string, unknown>;
  raw: BotTemplateDto;
  capabilityTags: string[];
  templateCategory?: string;
  ownerName?: string;
  manualUrl?: string;
  afterCreate?: unknown;
  templateReleaseStage?: 'whitelist' | 'online';
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
const list = (value: unknown): string[] => {
  if (Array.isArray(value))
    return value
      .map(String)
      .map((item) => item.trim())
      .filter(Boolean);
  if (typeof value === 'string')
    return value
      .split(/[,，|]/)
      .map((item) => item.trim())
      .filter(Boolean);
  return [];
};

/**
 * 判断 AgentCoding 模板是否声明支持创建服务 Bot。
 *
 * 模板工厂的能力位使用 `capabilities.upgrade_service_bot`，且只有显式
 * `true` 才算支持；缺失、字符串或其他 truthy 值都不能误开放服务化。
 * 应用 Coding Bot 是前端内置的固定模板，即使未来接口返回能力位，也不
 * 应被当作可服务化模板。
 */
export function getServiceBotCapability(template?: AgentCodingTemplate): boolean | undefined {
  if (!template) return undefined;
  if (
    String(template.templateType ?? '')
      .toLowerCase()
      .replace(/[\s_-]/g, '') === 'applicationcoding'
  )
    return false;

  const configs = [
    record(template.config),
    record(record(template.config).bot_template_config),
    record(template.raw),
    record(record(template.raw).template_config),
    record(record(template.raw).bot_template_config),
    record(record(template.raw).engine_properties),
    record(record(record(template.raw).engine_properties).template_config),
  ];
  const capabilityEntries = configs
    .map((config) => record(config.capabilities))
    .filter((capabilities) => Object.prototype.hasOwnProperty.call(capabilities, 'upgrade_service_bot'));
  if (!capabilityEntries.length) return undefined;
  return capabilityEntries.some((capabilities) => capabilities.upgrade_service_bot === true);
}

/**
 * 判断 AgentCoding 模板是否声明了 BCN 能力。
 * 支持显式 `true`、结构化 `{ enabled: true }`，以及历史 `capability_tags` 中的 `BCN` 标签。
 * 缺失、字符串或其他 truthy 值不视为支持。
 */
export function getBcnCapability(template?: Partial<Pick<AgentCodingTemplate, 'config' | 'raw'>>): boolean | undefined {
  if (!template) return undefined;
  const configSources = [
    record(template.config),
    record(record(template.config).bot_template_config),
    record(template.raw),
    record(record(template.raw).template_config),
    record(record(template.raw).bot_template_config),
    record(record(template.raw).engine_properties),
    record(record(record(template.raw).engine_properties).template_config),
  ];
  const capabilities = configSources.map((source) => record(source.capabilities));
  const isBcnEnabled = (item: Record<string, unknown>) =>
    item.enable_bcn_network === true || item.bcn === true || record(item.bcn).enabled === true;
  if (capabilities.some(isBcnEnabled)) return true;

  const tagSources = configSources.flatMap((source) => list(source.capability_tags));
  if (tagSources.some((tag) => tag.toLowerCase() === 'bcn')) return true;
  if (capabilities.some((item) => 'enable_bcn_network' in item || 'bcn' in item)) return false;
  return undefined;
}

export function supportsServiceBot(template?: AgentCodingTemplate): boolean {
  return getServiceBotCapability(template) === true;
}

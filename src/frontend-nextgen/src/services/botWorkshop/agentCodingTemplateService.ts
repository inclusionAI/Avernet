import {
  listAgentCodingTemplates,
  type BotTemplateDto,
  type BotTemplateField,
} from '@/services/backendApi/bots/botTemplateController';
import { buildAgentConfigEnvConfig } from './agentConfigEnv';
export type { BotTemplateField } from '@/services/backendApi/bots/botTemplateController';

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

const asFieldType = (field: BotTemplateField) => String(field.field_type ?? field.type ?? 'string').toLowerCase();
const isRequired = (field: BotTemplateField) =>
  field.required === true || String(field.required ?? '').toLowerCase() === 'true';
const isEmpty = (value: unknown) =>
  value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0);

function sanitizeTemplateValues(fields: BotTemplateField[], values: Record<string, unknown>) {
  const next = { ...values };
  fields.forEach((field) => {
    const key = String(field.field_key);
    const type = asFieldType(field);
    const value = next[key];
    if (type === 'antcode' || type === 'yuque') {
      next[key] = Array.isArray(value)
        ? value.filter(
            (item) => item && typeof item === 'object' && Object.values(item as Record<string, unknown>).some(Boolean),
          )
        : [];
    } else if (['string_array', 'multi_select'].includes(type)) {
      next[key] = Array.isArray(value) ? value : [];
    } else if (type === 'object_array' && typeof value === 'string') {
      try {
        next[key] = JSON.parse(value);
      } catch {
        /* validate() reports malformed JSON */
      }
    } else if (['image', 'architect_bot_id', 'architect_bot', 'domain_bot_id'].includes(key)) {
      next[key] = typeof value === 'string' ? value.trim() : value;
    } else if (['is_hosted_24x7', 'is_hosted_7x24', 'enable_24x7_hosting', 'enable_7x24_hosting'].includes(key)) {
      next[key] = value === true || value === 1 || ['1', 'true', 'yes'].includes(String(value ?? '').toLowerCase());
      next.is_hosted_24x7 = next[key] ? 1 : 0;
    }
  });
  return next;
}
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value.trim() : undefined;
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
const capabilityLabels: Record<string, string> = {
  multiEngine: '多引擎',
  multi_engine: '多引擎',
  bcn: 'BCN',
  memberManagement: '成员协作',
  member_management: '成员协作',
  dingtalk: '钉钉渠道',
  channelManagement: '钉钉渠道',
  channel_management: '钉钉渠道',
  harness: 'Harness',
  workflow: '工作流',
  aix: '工作流',
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

export function supportsServiceBot(template?: AgentCodingTemplate): boolean {
  return getServiceBotCapability(template) === true;
}

const normalizeSupportEngines = (value: unknown): string[] =>
  Array.isArray(value)
    ? Array.from(
        new Set(value.map((item) => (typeof item === 'string' ? item.trim().toLowerCase() : '')).filter(Boolean)),
      )
    : [];

const getCodingRuntimeFamily = (runtime: string): 'cc' | 'codex' | undefined => {
  const normalized = runtime.replace(/[\s_-]/g, '');
  if (['cc', 'claude', 'claudecode', 'antcc', 'codefuseantcc'].includes(normalized)) return 'cc';
  if (['codex', 'antcodex', 'codefusecodex'].includes(normalized)) return 'codex';
  return undefined;
};

const isMultiEngineTemplate = (
  item: BotTemplateDto,
  nested: Record<string, unknown>,
  botConfig: Record<string, unknown>,
) => {
  if (String(item.template_type ?? nested.template_type ?? '').trim() === 'applicationCoding') return true;
  const supportEngines = normalizeSupportEngines(
    nested.support_engines ?? botConfig.support_engines ?? item.support_engines,
  );
  const families = new Set(supportEngines.map(getCodingRuntimeFamily).filter(Boolean));
  return families.has('cc') && families.has('codex');
};

const capabilityTagsFromConfig = (config: Record<string, unknown>): string[] => {
  const capabilities = record(config.capabilities);
  const tags: string[] = [];
  const enabled = (key: string, nested?: string) => {
    const value = capabilities[key] ?? (nested ? capabilities[nested] : undefined);
    return (
      value === true || (value && typeof value === 'object' && (value as Record<string, unknown>).enabled === true)
    );
  };
  if (enabled('multiEngine', 'multi_engine')) tags.push('多引擎');
  if (enabled('enable_bcn_network', 'bcn')) tags.push('BCN');
  if (enabled('member_management')) tags.push('成员协作');
  if (enabled('channel_management')) tags.push('钉钉渠道');
  if (enabled('aix_harness', 'harness')) tags.push('Harness');
  if (enabled('aix', 'workflow')) tags.push('工作流');
  return tags;
};
const manualUrlFrom = (config: Record<string, unknown>): string | undefined => {
  const ext = record(config.ext_config);
  return (
    text(config.manualUrl) ||
    text(config.manual_url) ||
    text(config.docUrl) ||
    text(config.doc_url) ||
    text(config.user_manual_url) ||
    text(ext.manualUrl) ||
    text(ext.manual_url) ||
    text(ext.docUrl) ||
    text(ext.doc_url)
  );
};

export function getTemplateReleaseStage(item: BotTemplateDto): AgentCodingTemplate['templateReleaseStage'] {
  const nested = record(item.template_config);
  const botConfig = record(item.bot_template_config ?? nested.bot_template_config);
  // 与旧版 CreateBotAgentCodingExtension 保持一致：优先读取 bot_template_config，
  // 再回退到模板项和 template_config.bot_template_config 中的状态。
  const status = String(
    botConfig.status || item.bot_template_config?.status || record(nested.bot_template_config).status || '',
  ).toLowerCase();
  if (status === 'pre_published' || status === 'whitelist') return 'whitelist';
  if (status === 'published' || status === 'online') return 'online';
  return undefined;
}

function mapTemplate(item: BotTemplateDto): AgentCodingTemplate | undefined {
  const nested = record(item.template_config);
  const botConfig = record(item.bot_template_config ?? nested.bot_template_config);
  const templateType = String(item.template_type ?? nested.template_type ?? '').trim();
  const key = String(item.template_key ?? nested.template_key ?? botConfig.template_key ?? '').trim();
  const versionId = String(item.template_version_id ?? nested.template_version_id ?? botConfig.id ?? '').trim();
  const engineType = String(item.engine_type ?? '')
    .trim()
    .toLowerCase();
  const normalizedTemplateType = templateType.toLowerCase().replace(/[\s_-]/g, '');
  // normal / normalCC 是普通 Claude Code 模板，不能展示在 AgentCoding
  // 选择区；generalCC 属于合法的模板 Bot，需要保留。提交层仍保留同样的
  // 校验，防止外部调用绕过列表直接提交普通模板。
  const isOrdinaryClaudeCodeTemplate = ['normal', 'normalcc'].includes(normalizedTemplateType);
  if (
    !templateType ||
    !key ||
    !versionId ||
    isOrdinaryClaudeCodeTemplate ||
    (engineType && !['aicoding', 'claude_code', 'claudecode'].includes(engineType))
  )
    return undefined;
  const fields = (
    Array.isArray(item.custom_field_config)
      ? item.custom_field_config
      : Array.isArray(botConfig.custom_field_config)
      ? botConfig.custom_field_config
      : []
  ) as BotTemplateField[];
  const templateCategory =
    String(item.template_category ?? botConfig.template_category ?? nested.template_category ?? '').trim() || undefined;
  const normalizedCategory = templateCategory?.toLowerCase().replace(/[\s_-]/g, '');
  const rawTags = [
    ...(isMultiEngineTemplate(item, nested, botConfig) ? ['多引擎'] : []),
    ...list(item.capability_tags),
    ...list(item.capabilities),
    ...list(item.tags),
    ...list(item.template_tags),
    ...list(botConfig.capability_tags),
    ...list(botConfig.capabilities),
    ...list(botConfig.tags),
    ...list(botConfig.template_tags),
    ...capabilityTagsFromConfig(record(item.template_config)),
    ...capabilityTagsFromConfig(botConfig),
  ];
  const capabilityTags = Array.from(new Set(rawTags.map((tag) => capabilityLabels[tag] || tag)));
  const ownerName = [
    text(item.template_owner_name),
    text(item.owner_name),
    text(item.owner),
    text(item.created_by),
    text(nested.template_owner_name),
    text(nested.owner_name),
    text(botConfig.template_owner_name),
    text(botConfig.owner_name),
    text(botConfig.owner),
    text(botConfig.created_by),
  ]
    .find(Boolean)
    ?.replace(/\s*[（(][^（）()]*[）)]\s*$/, '');
  const templateReleaseStage = getTemplateReleaseStage(item);
  const manualUrl =
    manualUrlFrom(item) ||
    manualUrlFrom(nested) ||
    manualUrlFrom(botConfig) ||
    (templateType === 'applicationCoding'
      ? 'https://yuque.antfin.com/aixcoding/manual/application-coding-bot'
      : templateType.toLowerCase().includes('personal')
      ? 'https://yuque.antfin.com/aixcoding/manual/personal-coding-bot'
      : templateType.toLowerCase().includes('architect')
      ? 'https://yuque.antfin.com/aixcoding/manual/ha96b9hak3s188s3'
      : templateType === 'generalCC'
      ? 'https://yuque.antfin.com/aixcoding/manual/fdxxefzolo9flc8s'
      : undefined);
  return {
    key,
    versionId,
    name: String(item.template_name ?? botConfig.template_name ?? key),
    description: String(item.description ?? botConfig.description ?? ''),
    engine: String(item.engine_type ?? 'aicoding'),
    templateType,
    source:
      templateType === 'applicationCoding' ||
      ['official', 'recommend', 'recommended', 'officialrecommend'].includes(normalizedCategory ?? '')
        ? 'official'
        : 'market',
    fields,
    config: { ...nested, bot_template_config: item.bot_template_config ?? nested.bot_template_config },
    raw: item,
    capabilityTags: Array.from(new Set(capabilityTags)),
    templateCategory,
    ownerName,
    manualUrl,
    afterCreate:
      item.afterCreate ??
      item.after_create ??
      botConfig.afterCreate ??
      botConfig.after_create ??
      nested.afterCreate ??
      nested.after_create,
    templateReleaseStage,
  };
}

const STATIC_OFFICIAL_TEMPLATES: AgentCodingTemplate[] = [
  {
    key: 'app_coding',
    versionId: 'applicationCoding',
    name: '应用 Bot',
    description: '面向应用的 AI 编程助手，关联代码仓库与研发工作流',
    engine: 'claude_code',
    templateType: 'applicationCoding',
    source: 'official',
    fields: [],
    config: {},
    capabilityTags: ['多引擎', '成员协作', 'Harness', '工作流'],
    raw: {},
    templateCategory: 'official',
    manualUrl: 'https://yuque.antfin.com/aixcoding/manual/application-coding-bot',
  },
];

export const agentCodingTemplateService = {
  async list(): Promise<AgentCodingTemplate[]> {
    const templates = await listAgentCodingTemplates();
    const remote = templates.map(mapTemplate).filter((item): item is AgentCodingTemplate => Boolean(item));
    // 应用 Bot 等官方选项在旧版里是前端内置的，不能被模板工厂接口返回的同类型
    // 数据替换掉。模板工厂返回的是另一组可配置模板，两组数据需要同时展示。
    // 仅按 key + versionId 去重，避免接口重复返回完全相同的模板时出现重复卡片。
    const all = [...STATIC_OFFICIAL_TEMPLATES, ...remote];
    return all.filter(
      (item, index, items) =>
        items.findIndex((candidate) => candidate.key === item.key && candidate.versionId === item.versionId) === index,
    );
  },
  getInitialValues(template: AgentCodingTemplate): Record<string, unknown> {
    const context = template.config;
    return template.fields.reduce<Record<string, unknown>>((values, field) => {
      const type = asFieldType(field);
      const key = String(field.field_key);
      const semanticKey = key.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase();
      const semantic = ['antcode', 'yuque'].includes(type)
        ? []
        : ['workflow', 'devflow_workflow', 'workflow_id'].includes(semanticKey)
        ? context[key] ?? context.devflow_workflow ?? null
        : ['model', 'default_model', 'model_id'].includes(semanticKey)
        ? context[key] ?? (context.model ? { model: context.model, runtime: context.runtime } : {})
        : semanticKey === 'image'
        ? context[key] ?? context.image ?? ''
        : ['is_hosted_24x7', 'is_hosted_7x24', 'enable_24x7_hosting', 'enable_7x24_hosting'].includes(semanticKey)
        ? context.is_hosted_24x7 === 1 || context.is_hosted_24x7 === true
        : undefined;
      if (
        field.value !== undefined &&
        !(semantic !== undefined && (field.value === '' || (Array.isArray(field.value) && field.value.length === 0)))
      )
        values[key] = field.value;
      else if (
        field.default_value !== undefined &&
        !(
          semantic !== undefined &&
          (field.default_value === '' || (Array.isArray(field.default_value) && field.default_value.length === 0))
        )
      )
        values[key] = field.default_value;
      else if (semantic !== undefined) values[key] = semantic;
      else if (['boolean', 'checkbox', 'switch'].includes(type)) values[key] = false;
      else if (['multi_select', 'string_array', 'object_array'].includes(type)) values[key] = [];
      else values[key] = '';
      return values;
    }, {});
  },
  validate(template: AgentCodingTemplate | undefined, values: Record<string, unknown>): string | undefined {
    if (!template) return '请选择 AgentCoding 模板';
    for (const field of template.fields) {
      const value = values[field.field_key];
      const type = asFieldType(field);
      if (isRequired(field) && isEmpty(value)) return `请填写${field.field_name ?? field.field_key}`;
      if (!isEmpty(value) && type === 'object_array' && typeof value === 'string') {
        try {
          if (!Array.isArray(JSON.parse(value))) return `${field.field_name ?? field.field_key} 需填写 JSON 数组`;
        } catch {
          return `${field.field_name ?? field.field_key} 需填写合法 JSON 数组`;
        }
      }
      if (
        type === 'yuque' &&
        Array.isArray(value) &&
        value.some((item) => {
          const row = item as Record<string, unknown>;
          return Boolean(String(row.url ?? '').trim()) !== Boolean(String(row.token ?? '').trim());
        })
      )
        return `${field.field_name ?? field.field_key} 的地址和 Token 需要成对填写`;
    }
    return undefined;
  },
  toCreateFields(template: AgentCodingTemplate, values: Record<string, unknown>, botName?: string) {
    const sanitizedValues = sanitizeTemplateValues(template.fields, values);
    const templateConfig = {
      ...template.config,
      ...sanitizedValues,
      // 详情页和后续编辑依赖模板快照中的字段定义，不能丢失该元信息。
      ...(template.config.bot_template_config ? { bot_template_config: template.config.bot_template_config } : {}),
      ...buildAgentConfigEnvConfig(template.config.envs, sanitizedValues, botName),
      template_key: template.key,
      template_version_id: template.versionId,
    };
    return {
      engine_properties: {
        template_type: template.templateType,
        template_config: templateConfig,
      },
    };
  },
};

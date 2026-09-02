import {
  compactCodeRepoItems,
  compactYuqueKbRepoItems,
  validateCodeRepoItems,
  validateYuqueKbRepoBindings,
  validateYuqueKbRepoItems,
} from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/aicoding/configFields';
import type { ModelOption } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/CodingModelConfigField';
import type { BotTemplateField } from '@/services/botWorkshop/agentCodingTemplateService';
import { isTemplateFieldRequired } from './fieldUtils';
type TemplateConfig = Record<string, any>;

export function fieldTypeOf(field: BotTemplateField): string {
  return String(field.field_type ?? field.type ?? '').toLowerCase();
}

export interface SemanticFieldResult<T = any> {
  handled: boolean;
  value?: T;
  /** Values that should be merged into template_config root for composite semantic fields. */
  spreadValues?: Record<string, any>;
}

export interface SemanticFieldRendererProps {
  field: BotTemplateField;
  label: string;
  value: any;
  disabled?: boolean;
  errors?: Record<number, string>;
  onChange: (next: any) => void;
  contextValues?: Record<string, any>;
  modelOptions?: ModelOption[];
  modelsLoading?: boolean;
  modelsLoadError?: string | null;
  botId?: string;
  onReloadModels?: () => void | Promise<void>;
}

function normalizeSemanticFieldName(value?: string): string {
  return (value || '')
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .replace(/[\s-]/g, '_');
}

export function normalizeSemanticFieldKey(field: BotTemplateField): string {
  return normalizeSemanticFieldName(field.field_key);
}

export function isWorkflowField(field: BotTemplateField): boolean {
  const key = normalizeSemanticFieldKey(field);
  return key === 'devflow_workflow' || key === 'workflow' || key === 'workflow_id';
}

export function isModelTemplateField(field: BotTemplateField): boolean {
  const key = normalizeSemanticFieldKey(field);
  return key === 'model' || key === 'default_model' || key === 'model_id';
}

export function isImageTemplateField(field: BotTemplateField): boolean {
  return normalizeSemanticFieldKey(field) === 'image';
}

export function isArchitectBotField(field: BotTemplateField): boolean {
  const key = normalizeSemanticFieldKey(field);
  return (
    key === 'architect_bot_id' ||
    key === 'architect_bot' ||
    key === 'domain_bot_id' ||
    key === 'domain_architect_bot_id' ||
    key === 'architect_name'
  );
}

export function isHosting24x7TemplateField(field: BotTemplateField): boolean {
  const key = normalizeSemanticFieldKey(field);
  return (
    key === 'is_hosted_24x7' ||
    key === 'is_hosted_7x24' ||
    key === 'enable_24x7_hosting' ||
    key === 'enable_7x24_hosting' ||
    key === 'hosting_24x7' ||
    key === 'hosting_7x24'
  );
}

export function toHosting24x7Boolean(value: any): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value === 1;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (!normalized) return true;
    return normalized === '1' || normalized === 'true' || normalized === 'yes';
  }
  return false;
}

export function isSemanticTemplateField(field: BotTemplateField): boolean {
  return (
    fieldTypeOf(field) === 'antcode' ||
    fieldTypeOf(field) === 'yuque' ||
    isWorkflowField(field) ||
    isModelTemplateField(field) ||
    isImageTemplateField(field) ||
    isArchitectBotField(field) ||
    isHosting24x7TemplateField(field)
  );
}

export function getSemanticTemplateFieldInitialValue(
  field: BotTemplateField,
  contextValues?: Record<string, any>,
): SemanticFieldResult {
  if (!isSemanticTemplateField(field)) return { handled: false };
  if (fieldTypeOf(field) === 'antcode' || fieldTypeOf(field) === 'yuque') {
    return { handled: true, value: [] };
  }
  if (isWorkflowField(field)) return { handled: true, value: null };
  if (isModelTemplateField(field)) return { handled: true, value: {} };
  if (isImageTemplateField(field)) {
    // 模板根配置可能已带默认镜像（template_config.image），动态字段 key 可能叫 image/custom_image/sandbox_image。
    // 新建模板 Bot 时先用根配置 image 回填，避免用户看到空值并在保存时覆盖掉模板默认镜像。
    const image =
      typeof contextValues?.image === 'string'
        ? contextValues.image
        : typeof contextValues?.[field.field_key] === 'string'
        ? contextValues[field.field_key]
        : '';
    return { handled: true, value: image };
  }
  if (isArchitectBotField(field)) {
    const fieldKey = normalizeSemanticFieldKey(field);
    const architectValue =
      typeof contextValues?.[field.field_key] === 'string'
        ? contextValues[field.field_key]
        : fieldKey === 'architect_name' || typeof contextValues?.architect_bot_id !== 'string'
        ? ''
        : contextValues.architect_bot_id;
    return { handled: true, value: architectValue };
  }
  if (isHosting24x7TemplateField(field)) {
    const sourceValue = contextValues?.is_hosted_24x7 ?? contextValues?.[field.field_key];
    return { handled: true, value: toHosting24x7Boolean(sourceValue) };
  }
  return { handled: true, value: '' };
}

export function sanitizeSemanticTemplateFieldValue(field: BotTemplateField, value: any): SemanticFieldResult {
  if (fieldTypeOf(field) === 'antcode') {
    return { handled: true, value: compactCodeRepoItems(value) };
  }
  if (fieldTypeOf(field) === 'yuque') {
    return { handled: true, value: compactYuqueKbRepoItems(value) };
  }
  if (isWorkflowField(field)) {
    return {
      handled: true,
      value: value || undefined,
      spreadValues: { devflow_workflow: value || undefined },
    };
  }
  if (isModelTemplateField(field)) {
    const config =
      value && typeof value === 'object' && !Array.isArray(value)
        ? { ...value }
        : typeof value === 'string' && value
        ? { model: value }
        : {};
    delete (config as TemplateConfig & { __validation_error?: string }).__validation_error;
    return {
      handled: true,
      value: config,
      spreadValues: config,
    };
  }
  if (isImageTemplateField(field)) {
    const image = typeof value === 'string' ? value.trim() : '';
    return { handled: true, value: image, spreadValues: { image } };
  }
  if (isArchitectBotField(field)) {
    const architectValue = typeof value === 'string' ? value.trim() : '';
    if (normalizeSemanticFieldKey(field) === 'architect_name') {
      return { handled: true, value: architectValue };
    }
    return {
      handled: true,
      value: architectValue,
      spreadValues: { architect_bot_id: architectValue || undefined },
    };
  }
  if (isHosting24x7TemplateField(field)) {
    const enabled = toHosting24x7Boolean(value);
    return {
      handled: true,
      value: enabled,
      spreadValues: { is_hosted_24x7: enabled ? 1 : 0 },
    };
  }
  return { handled: false };
}

export function validateSemanticTemplateField(
  field: BotTemplateField,
  value: any,
  label: string,
): string | null | undefined {
  if (fieldTypeOf(field) === 'antcode') {
    return validateCodeRepoItems(label, value, {
      required: isTemplateFieldRequired(field),
    });
  }
  if (fieldTypeOf(field) === 'yuque') {
    return validateYuqueKbRepoItems(label, value, {
      required: isTemplateFieldRequired(field),
      requireTokenForFilledUrl: true,
    });
  }
  if (isWorkflowField(field)) {
    const empty = !value || (typeof value === 'object' && !value.path && !value.name);
    if (isTemplateFieldRequired(field) && empty) return `请选择${label}`;
    return null;
  }
  if (isModelTemplateField(field)) {
    const error = value && typeof value === 'object' ? value.__validation_error : null;
    if (error) return error;
    const empty = !value || typeof value !== 'object' || !value.model;
    if (isTemplateFieldRequired(field) && empty) return `请选择${label}`;
    return null;
  }
  if (isImageTemplateField(field)) {
    if (isTemplateFieldRequired(field) && !String(value || '').trim()) {
      return `请填写${label}`;
    }
    return null;
  }
  if (isArchitectBotField(field)) {
    if (isTemplateFieldRequired(field) && !String(value || '').trim()) {
      return `请选择${label}`;
    }
    return null;
  }
  if (isHosting24x7TemplateField(field)) {
    return null;
  }
  return undefined;
}

export interface SemanticTemplateFieldBindingValidationResult {
  success: boolean;
  errors: Record<number, string>;
}

export async function validateSemanticTemplateFieldBindingsDetailed(
  field: BotTemplateField,
  value: any,
): Promise<SemanticTemplateFieldBindingValidationResult | undefined> {
  if (field.field_type !== 'yuque') return undefined;
  return validateYuqueKbRepoBindings(Array.isArray(value) ? value : []);
}

export async function validateSemanticTemplateFieldBindings(
  field: BotTemplateField,
  value: any,
  label: string,
): Promise<string | null | undefined> {
  const result = await validateSemanticTemplateFieldBindingsDetailed(field, value);
  if (!result) return undefined;
  if (result.success) return null;
  const firstError = Object.values(result.errors)[0];
  return firstError ? `${label}：${firstError}` : `${label} Token 校验失败，请检查配置`;
}

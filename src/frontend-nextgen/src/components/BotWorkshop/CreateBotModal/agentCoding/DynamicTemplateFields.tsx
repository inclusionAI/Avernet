import type { ModelOption } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/CodingModelConfigField';
import type { BotTemplateField } from '@/services/botWorkshop/agentCodingTemplateService';
import React, { useEffect } from 'react';
import { DynamicTemplateFieldControl } from './DynamicTemplateFieldControl';
import { isTemplateFieldRequired } from './fields/fieldUtils';
import {
  getSemanticTemplateFieldInitialValue,
  isImageTemplateField,
  isSemanticTemplateField,
  renderSemanticTemplateField,
  sanitizeSemanticTemplateFieldValue,
  validateSemanticTemplateField,
  validateSemanticTemplateFieldBindingsDetailed,
} from './fields/semanticFieldRenderers';
type FieldValues = Record<string, any>;
interface DynamicTemplateFieldsProps {
  fields?: BotTemplateField[];
  value?: FieldValues;
  /** Static/root template_config values that semantic controls may need (for example support_engines). */
  contextValues?: FieldValues;
  onChange: (value: FieldValues) => void;
  onValidationChange?: (error?: string) => void;
  disabled?: boolean;
  fieldErrors?: Record<string, Record<number, string>>;
  onFieldChange?: (fieldKey: string) => void;
  modelOptions?: ModelOption[];
  modelsLoading?: boolean;
  modelsLoadError?: string | null;
  botId?: string;
  onReloadModels?: () => void | Promise<void>;
  renderField?: (context: {
    field: BotTemplateField;
    label: string;
    value: any;
    disabled?: boolean;
    required: boolean;
    onChange: (next: any) => void;
  }) => React.ReactNode | undefined;
}
function fieldTypeOf(field: BotTemplateField): string {
  return String(field.field_type ?? field.type ?? 'string').toLowerCase();
}
function shouldUseSemanticInitialValue(
  field: BotTemplateField,
  candidateValue: any,
  semanticInitialValue: ReturnType<typeof getSemanticTemplateFieldInitialValue>,
): boolean {
  if (!semanticInitialValue.handled) return false;
  if (!isImageTemplateField(field)) return false;
  // 目前只有“镜像地址”字段需要这个特殊回填：模板根 template_config.image
  // 是镜像默认值来源，custom_field_config 里的空值占位不能覆盖它。
  return candidateValue === '' || (Array.isArray(candidateValue) && candidateValue.length === 0);
}
function getFieldInitialValue(field: BotTemplateField, contextValues?: FieldValues): any {
  const semanticInitialValue = getSemanticTemplateFieldInitialValue(field, contextValues);
  if (field.value !== undefined) {
    if (shouldUseSemanticInitialValue(field, field.value, semanticInitialValue)) {
      return semanticInitialValue.value;
    }
    return field.value;
  }
  if (field.default_value !== undefined) {
    if (shouldUseSemanticInitialValue(field, field.default_value, semanticInitialValue)) {
      return semanticInitialValue.value;
    }
    return field.default_value;
  }
  if (contextValues && contextValues[field.field_key] !== undefined) {
    return contextValues[field.field_key];
  }
  if (['boolean', 'checkbox', 'switch'].includes(fieldTypeOf(field))) return false;
  if (semanticInitialValue.handled) return semanticInitialValue.value;
  if (['string_array', 'multi_select'].includes(fieldTypeOf(field))) {
    return [];
  }
  if (fieldTypeOf(field) === 'object_array') return [];
  return '';
}

export function getDynamicTemplateFieldInitialValues(
  fields: BotTemplateField[] = [],
  contextValues?: FieldValues,
): FieldValues {
  return fields.reduce<FieldValues>((next, field) => {
    next[field.field_key] = getFieldInitialValue(field, contextValues);
    return next;
  }, {});
}

function normalizeOptions(field: BotTemplateField) {
  return (field.options || field.enum_values || []).map((option) => ({
    label: option.label || String(option.value),
    value: String(option.value),
  }));
}

function setFieldValue(values: FieldValues | undefined, key: string, next: any): FieldValues {
  return { ...(values || {}), [key]: next };
}

export function sanitizeDynamicTemplateFieldValues(
  fields: BotTemplateField[] = [],
  values: FieldValues = {},
): FieldValues {
  const next = { ...values };
  fields.forEach((field) => {
    const value = next[field.field_key];
    const semanticValue = sanitizeSemanticTemplateFieldValue(field, value);
    if (semanticValue.handled) {
      next[field.field_key] = semanticValue.value;
      if (semanticValue.spreadValues) {
        Object.assign(next, semanticValue.spreadValues);
      }
    }
  });
  return next;
}

export function validateDynamicTemplateFields(
  fields: BotTemplateField[] = [],
  values: FieldValues = {},
): string | null {
  for (const field of fields) {
    const value = values[field.field_key];
    const empty = value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0);
    const label = field.field_name || field.field_key;
    const semanticError = validateSemanticTemplateField(field, value, label);
    if (semanticError !== undefined) {
      if (semanticError) return semanticError;
      continue;
    }
    if (isTemplateFieldRequired(field) && empty) return `请填写${label}`;
    if (!empty && fieldTypeOf(field) === 'object_array' && typeof value === 'string') {
      try {
        const parsed = JSON.parse(value || '[]');
        if (!Array.isArray(parsed)) {
          return `${field.field_name || field.field_key} 需填写 JSON 数组`;
        }
      } catch {
        return `${field.field_name || field.field_key} 需填写合法 JSON 数组`;
      }
    }
  }
  return null;
}

export interface DynamicTemplateFieldBindingValidationResult {
  success: boolean;
  message?: string;
  fieldErrors: Record<string, Record<number, string>>;
}

export async function validateDynamicTemplateFieldBindingsDetailed(
  fields: BotTemplateField[] = [],
  values: FieldValues = {},
): Promise<DynamicTemplateFieldBindingValidationResult> {
  const fieldErrors: Record<string, Record<number, string>> = {};
  let message: string | undefined;

  for (const field of fields) {
    const label = field.field_name || field.field_key;
    const result = await validateSemanticTemplateFieldBindingsDetailed(field, values[field.field_key]);
    if (!result || result.success) continue;

    fieldErrors[field.field_key] = result.errors;
    if (!message) {
      const firstError = Object.values(result.errors)[0];
      message = firstError ? `${label}：${firstError}` : `${label} Token 校验失败，请检查配置`;
    }
  }

  return {
    success: Object.keys(fieldErrors).length === 0,
    message,
    fieldErrors,
  };
}

export async function validateDynamicTemplateFieldBindings(
  fields: BotTemplateField[] = [],
  values: FieldValues = {},
): Promise<string | null> {
  const result = await validateDynamicTemplateFieldBindingsDetailed(fields, values);
  return result.success ? null : result.message || '模板字段校验失败';
}

export const DynamicTemplateFields: React.FC<DynamicTemplateFieldsProps> = ({
  fields = [],
  value = {},
  contextValues = {},
  onChange,
  onValidationChange,
  disabled,
  fieldErrors = {},
  onFieldChange,
  renderField,
  modelOptions,
  modelsLoading,
  modelsLoadError,
  botId,
  onReloadModels,
}) => {
  useEffect(() => {
    onValidationChange?.(validateDynamicTemplateFields(fields, value) ?? undefined);
  }, [fields, onValidationChange, value]);

  useEffect(() => {
    if (!fields.length) return;
    const initialValues = getDynamicTemplateFieldInitialValues(fields, contextValues);
    const next = { ...value };
    let changed = false;
    fields.forEach((field) => {
      if (next[field.field_key] === undefined) {
        next[field.field_key] = initialValues[field.field_key];
        changed = true;
      }
    });
    if (changed) onChange(next);
    // 初始化跟字段集合和模板根配置变化；只填充缺失字段，避免覆盖用户已输入内容。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fields, contextValues]);

  if (!fields.length) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-400">
        当前模板无需额外配置。
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {fields.map((field) => {
        const key = field.field_key;
        const label = field.field_name || key;
        const fieldValue = value[key] ?? getFieldInitialValue(field, contextValues);
        const options = normalizeOptions(field);
        const required = isTemplateFieldRequired(field);
        const customControl = renderField?.({
          field,
          label,
          value: fieldValue,
          disabled,
          required,
          onChange: (next) => {
            onFieldChange?.(key);
            onChange(setFieldValue(value, key, next));
          },
        });

        if (customControl !== undefined) {
          return <div key={key}>{customControl}</div>;
        }

        if (isSemanticTemplateField(field)) {
          return (
            <div key={key}>
              {renderSemanticTemplateField({
                field,
                label,
                value: fieldValue,
                disabled,
                errors: fieldErrors[key],
                onChange: (next) => {
                  onFieldChange?.(key);
                  onChange(setFieldValue(value, key, next));
                },
                contextValues: { ...contextValues, ...value },
                modelOptions,
                modelsLoading,
                modelsLoadError,
                botId,
                onReloadModels,
              })}
            </div>
          );
        }

        const control = (
          <DynamicTemplateFieldControl
            field={field}
            value={fieldValue}
            options={options}
            required={required}
            disabled={disabled}
            onChange={(next) => {
              onFieldChange?.(key);
              onChange(setFieldValue(value, key, next));
            }}
          />
        );

        return <div key={key}>{control}</div>;
      })}
    </div>
  );
};

export default DynamicTemplateFields;

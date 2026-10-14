import type { ModelOption } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/CodingModelConfigField';
import type { CodeRepositoryTenant } from '@/services/botWorkshop/agentCodingLegacyService';
import type { BotTemplateField } from '@/services/botWorkshop/agentCodingTemplateService';
import React, { useEffect } from 'react';
import { DynamicTemplateFieldControl } from './DynamicTemplateFieldControl';
import {
  FieldValues,
  getFieldInitialValue,
  getDynamicTemplateFieldInitialValues,
  normalizeOptions,
  setFieldValue,
  validateDynamicTemplateFields,
} from './DynamicTemplateFieldLogic';
import { isTemplateFieldRequired } from './fields/fieldUtils';
import { isSemanticTemplateField, renderSemanticTemplateField } from './fields/semanticFieldRenderers';

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
  /** 动态代码仓库字段的搜索租户，按 field_key 隔离。 */
  codeRepoTenants?: Record<string, CodeRepositoryTenant>;
  /** 切换动态代码仓库字段的搜索租户。 */
  onCodeRepoTenantChange?: (fieldKey: string, tenant: CodeRepositoryTenant) => void;
  renderField?: (context: {
    field: BotTemplateField;
    label: string;
    value: any;
    disabled?: boolean;
    required: boolean;
    onChange: (next: any) => void;
  }) => React.ReactNode | undefined;
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
  codeRepoTenants,
  onCodeRepoTenantChange,
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
                codeRepoTenants,
                onCodeRepoTenantChange,
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

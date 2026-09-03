import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Segmented } from '@/components/ui/Segmented';
import type { AgentCodingDraft } from '@/domain/botWorkshop';
import {
  agentCodingTemplateService,
  type AgentCodingTemplate,
  type BotTemplateField,
} from '@/services/botWorkshop/agentCodingTemplateService';
import { ArrowRight } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AgentCodingTemplateGrid } from './AgentCodingTemplateGrid';
import { ApplicationCodingConfigForm } from './ApplicationCodingConfigForm';
import { DynamicTemplateFields, validateDynamicTemplateFieldBindingsDetailed } from './DynamicTemplateFields';
import type { AppCodingConfigFormRef } from './legacy/appcoding/AppCodingConfigForm';

interface Props {
  templates: AgentCodingTemplate[];
  loading?: boolean;
  error?: string;
  value?: AgentCodingDraft;
  disabled?: boolean;
  onChange: (value: AgentCodingDraft | undefined) => void;
  onValidationChange?: (error?: string) => void;
  onRetry?: () => void;
  onValidateReady?: (validate: () => Promise<string | undefined>) => void;
}

export function AgentCodingSection({
  templates,
  loading,
  error,
  value,
  disabled,
  onChange,
  onValidationChange,
  onRetry,
  onValidateReady,
}: Props) {
  const [source, setSource] = useState<'official' | 'market'>('official');
  const [fieldErrors, setFieldErrors] = useState<Record<string, Record<number, string>>>({});
  const applicationFormRef = useRef<AppCodingConfigFormRef | null>(null);
  const visibleTemplates = useMemo(
    () => templates.filter((template) => template.source === source),
    [source, templates],
  );
  const selected = value?.template;
  const templateFactoryUrl = getCapabilities().getAgentCodingInternalResources().value.templateFactoryUrl;
  // 旧版 AppCodingConfigForm 会把 onChange 放进 effect 依赖。这里保持适配回调
  // 恒定，避免父层每次收到表单值后重新生成 callback，触发旧表单 effect 循环。
  const valueRef = useRef(value);
  const selectedRef = useRef(selected);
  const onChangeRef = useRef(onChange);
  valueRef.current = value;
  selectedRef.current = selected;
  onChangeRef.current = onChange;

  useEffect(() => {
    if (
      selected &&
      !templates.some((template) => template.key === selected.key && template.versionId === selected.versionId)
    )
      onChange(undefined);
  }, [onChange, selected, templates]);

  const selectTemplate = (template: AgentCodingTemplate) => {
    setFieldErrors({});
    onChange({
      kind: template.templateType === 'applicationCoding' ? 'applicationCoding' : 'template',
      template,
      values: agentCodingTemplateService.getInitialValues(template),
    });
    onValidationChange?.(undefined);
  };

  useEffect(() => {
    if (selected || source !== 'official') return;
    const firstOfficialTemplate = visibleTemplates[0];
    if (firstOfficialTemplate) selectTemplate(firstOfficialTemplate);
  }, [selected, source, visibleTemplates]);

  const updateValues = useCallback((values: Record<string, unknown>) => {
    const currentValue = valueRef.current;
    if (!selectedRef.current || !currentValue) return;
    onChangeRef.current({ ...currentValue, values });
  }, []);
  const clearFieldError = (fieldKey: string) => {
    setFieldErrors((current) => {
      if (!current[fieldKey]) return current;
      const next = { ...current };
      delete next[fieldKey];
      return next;
    });
  };
  const validateBeforeSubmit = useCallback(async () => {
    if (!selected || !value) return '请选择 AgentCoding 模板';
    const localError = agentCodingTemplateService.validate(selected as AgentCodingTemplate, value.values);
    if (localError) return localError;
    if (value.kind === 'applicationCoding') {
      return (await applicationFormRef.current?.validate?.()) ?? undefined;
    }
    const result = await validateDynamicTemplateFieldBindingsDetailed(
      selected.fields as BotTemplateField[],
      value.values,
    );
    setFieldErrors(result.fieldErrors);
    return result.success ? undefined : result.message || '模板字段校验失败';
  }, [selected, value]);

  useEffect(() => {
    onValidateReady?.(validateBeforeSubmit);
  }, [onValidateReady, validateBeforeSubmit]);
  const openTemplateFactory = () => {
    if (templateFactoryUrl) window.open(templateFactoryUrl, '_blank', 'noopener,noreferrer');
  };

  return (
    <section
      className="relative z-10 mt-1 overflow-visible rounded-xl border border-primary/30 bg-background px-3 py-3"
      data-testid="agent-coding-section"
    >
      <div>
        <div className="flex items-center justify-between gap-3">
          <Segmented
            value={source}
            onChange={setSource}
            options={[
              { value: 'official', label: '官方推荐' },
              { value: 'market', label: '模板市场' },
            ]}
            className="bg-muted/70 p-0.5 [&_button]:cursor-pointer [&_button]:px-3 [&_button]:!font-bold [&_button]:!text-foreground"
          />
          {templateFactoryUrl ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={openTemplateFactory}
              className="h-9 cursor-pointer border-transparent bg-primary/5 px-4 !font-bold text-primary shadow-none hover:bg-primary/10"
            >
              创建模板 <ArrowRight className="size-3.5" />
            </Button>
          ) : null}
        </div>
        <div className="mt-3">
          <AgentCodingTemplateGrid
            templates={visibleTemplates}
            selectedKey={selected?.key}
            selectedVersionId={selected?.versionId}
            disabled={disabled}
            loading={loading}
            error={error}
            onRetry={onRetry}
            onSelect={selectTemplate}
            onCreateTemplate={templateFactoryUrl ? openTemplateFactory : undefined}
          />
        </div>
      </div>
      {selected && value ? (
        <div className="relative mt-4 overflow-visible border-t border-border pt-3">
          <div className="mb-2.5 flex items-center gap-2 text-xs font-semibold text-foreground">
            <span className="size-1.5 rounded-full bg-primary" />
            Bot 配置
          </div>
          {value.kind === 'applicationCoding' ? (
            <ApplicationCodingConfigForm
              ref={applicationFormRef}
              key={`${selected.key}:${selected.versionId}`}
              value={value.values}
              initialConfig={{ ...selected.config, ...value.values }}
              disabled={disabled}
              onChange={updateValues}
              onValidationChange={onValidationChange}
            />
          ) : (
            <DynamicTemplateFields
              fields={selected.fields as BotTemplateField[]}
              value={value.values}
              contextValues={{ ...selected.config, ...value.values }}
              disabled={disabled}
              fieldErrors={fieldErrors}
              onFieldChange={clearFieldError}
              onChange={updateValues}
              onValidationChange={onValidationChange}
            />
          )}
        </div>
      ) : null}
    </section>
  );
}

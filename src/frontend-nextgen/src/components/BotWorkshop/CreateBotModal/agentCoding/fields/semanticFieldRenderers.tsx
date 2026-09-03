import { getCapabilities } from '@/capabilities';
import {
  CodeRepoListField,
  normalizeCodeRepoItems,
  normalizeYuqueKbRepoItems,
  YuqueKbReposField,
} from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/aicoding/configFields';
import { ArchitectBotConfigField } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/ArchitectBotConfigField';
import { ArchitectNameField } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/ArchitectNameField';
import { CodingWorkflowConfigField } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/CodingWorkflowConfigField';
import { CustomImageConfigField } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/CustomImageConfigField';
import { Hosting24x7ConfigField } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/Hosting24x7ConfigField';
import type { WorkflowItem } from '@/services/botWorkshop/agentCodingLegacyService';
import React from 'react';
import { isTemplateFieldRequired } from './fieldUtils';
import { ModelTemplateFieldControl } from './ModelTemplateFieldControl';
import {
  fieldTypeOf,
  isArchitectBotField,
  isHosting24x7TemplateField,
  isImageTemplateField,
  isModelTemplateField,
  isWorkflowField,
  normalizeSemanticFieldKey,
  toHosting24x7Boolean,
  type SemanticFieldRendererProps,
} from './semanticFieldLogic';
export * from './semanticFieldLogic';

type TemplateConfig = Record<string, any>;

function renderYuqueTokenGuideTooltip(): React.ReactNode {
  const appCodingYuqueTokenGuideVideoUrl = getCapabilities().getAppCodingYuqueTokenGuideVideoUrl().value;

  return (
    <>
      <p className="mb-2">用于 Bot 知识库检索与 Memory 增强。</p>
      <p>获取团队 Token 方式：</p>
      <p>1. 语雀管理员进入语雀团队文档</p>
      <p>2. 设置-选择更多设置</p>
      <p>3. 选择token, 创建一个拥有 读取知识库/文档 权限的token</p>
      <p>4. 查看并复制token 的 Access, 粘贴进这里即可</p>
      {appCodingYuqueTokenGuideVideoUrl && (
        <video
          src={appCodingYuqueTokenGuideVideoUrl}
          autoPlay
          muted
          loop
          playsInline
          controls
          className="mt-1.5"
          style={{ width: 360 }}
        />
      )}
    </>
  );
}

export function renderSemanticTemplateField({
  field,
  label,
  value,
  disabled,
  errors,
  onChange,
  contextValues,
  modelOptions,
  modelsLoading,
  modelsLoadError,
  botId,
  onReloadModels,
}: SemanticFieldRendererProps): React.ReactNode {
  if (fieldTypeOf(field) === 'antcode') {
    return (
      <CodeRepoListField
        label={label}
        required={isTemplateFieldRequired(field)}
        value={Array.isArray(value) ? value : []}
        onChange={(next) => onChange(normalizeCodeRepoItems(next))}
        disabled={disabled}
        description={field.description}
      />
    );
  }

  if (fieldTypeOf(field) === 'yuque') {
    return (
      <YuqueKbReposField
        label={label}
        required={isTemplateFieldRequired(field)}
        value={Array.isArray(value) ? value : []}
        onChange={(next) => onChange(normalizeYuqueKbRepoItems(next))}
        disabled={disabled}
        description={field.description}
        errors={errors}
        tooltipContent={renderYuqueTokenGuideTooltip()}
      />
    );
  }

  if (isWorkflowField(field)) {
    return (
      <CodingWorkflowConfigField
        label={label}
        required={isTemplateFieldRequired(field)}
        value={(value || null) as WorkflowItem | null}
        disabled={disabled}
        placeholder={field.placeholder || '选择研发工作流'}
        onChange={onChange}
      />
    );
  }

  if (isModelTemplateField(field)) {
    const modelValue =
      value && typeof value === 'object' && !Array.isArray(value)
        ? value
        : typeof value === 'string' && value
        ? { model: value }
        : {};
    const initialConfig = {
      ...((contextValues || {}) as TemplateConfig),
      ...(modelValue as TemplateConfig),
    };
    delete (initialConfig as TemplateConfig & { __validation_error?: string }).__validation_error;
    return (
      <ModelTemplateFieldControl
        label={label}
        required={isTemplateFieldRequired(field)}
        disabled={disabled}
        initialConfig={initialConfig as TemplateConfig}
        modelOptions={modelOptions}
        modelsLoading={modelsLoading}
        modelsLoadError={modelsLoadError}
        botId={botId}
        onReloadModels={onReloadModels}
        onChange={onChange}
      />
    );
  }

  if (isImageTemplateField(field)) {
    return (
      <CustomImageConfigField
        label={label}
        required={isTemplateFieldRequired(field)}
        value={typeof value === 'string' ? value : ''}
        disabled={disabled}
        placeholder={field.placeholder || undefined}
        description={field.description}
        onChange={onChange}
      />
    );
  }

  if (normalizeSemanticFieldKey(field) === 'architect_name') {
    return (
      <ArchitectNameField
        label={label}
        required={isTemplateFieldRequired(field)}
        value={typeof value === 'string' ? value : ''}
        disabled={disabled}
        placeholder={field.placeholder || undefined}
        description={field.description}
        onChange={onChange}
      />
    );
  }

  if (isArchitectBotField(field)) {
    return (
      <ArchitectBotConfigField
        label={label}
        required={isTemplateFieldRequired(field)}
        value={typeof value === 'string' ? value : ''}
        disabled={disabled}
        placeholder={field.placeholder || undefined}
        description={field.description}
        onChange={onChange}
      />
    );
  }

  if (isHosting24x7TemplateField(field)) {
    return (
      <Hosting24x7ConfigField
        label={label}
        value={toHosting24x7Boolean(value)}
        disabled={disabled}
        description={field.description}
        onChange={onChange}
      />
    );
  }

  return null;
}

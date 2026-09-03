/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * AppCodingConfigForm - 应用 Coding 专属配置表单
 *
 * 当用户在创建 Bot 时选择「应用 Coding」引擎类型后展示，
 * 收集 template_config 所需的各字段并向上层暴露 onChange 回调。
 */
import type { WorkflowItem } from '@/services/botWorkshop/agentCodingLegacyService';
import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { validateYuqueKbRepoBindings } from '../aicoding/configFields';
import { AppCodingAdvancedConfig } from './AppCodingAdvancedConfig';
import {
  findDuplicateRepoUrls,
  formatYuquePrecheckWarning,
  isPositiveIntegerInput,
  isResourceSpecEnabledFromUrl,
} from './AppCodingConfigUtils';
import { AppCodingRepositoryFields } from './AppCodingRepositoryFields';
import type { ModelOption } from './CodingModelConfigField';
import { Hosting24x7ConfigField } from './Hosting24x7ConfigField';
type TemplateConfig = Record<string, any>;
export interface AppCodingConfigFormProps {
  /** 外部禁用（如 iframe 授权期间） */
  disabled?: boolean;
  /** 仓库字段禁用（编辑模式下前后端/lib仓库不可修改） */
  repoFieldsDisabled?: boolean;
  /** 初始配置值（用于编辑模式回填） */
  initialConfig?: TemplateConfig;
  /** 表单值变更回调，返回组装好的 TemplateConfig */
  onChange: (config: TemplateConfig) => void;
  /** 校验状态变更回调，返回当前校验错误信息（无错误时为 null） */
  onValidationChange?: (error: string | null) => void;
  /** 警告状态变更回调，返回当前非阻塞警告信息（无警告时为 null） */
  onWarningChange?: (warning: string | null) => void;
  /** 编辑 Bot 配置时由外层按当前 Bot 容器(/api/models)传入模型列表；未传则保留新建 Bot 的静态+CodeFuse 逻辑。 */
  modelOptions?: ModelOption[];
  modelsLoading?: boolean;
  modelsLoadError?: string | null;
  botId?: string;
  onReloadModels?: () => void | Promise<void>;
}
/**
 * 应用 Coding 专属配置表单
 *
 * 内部管理所有字段状态，通过 onChange 实时向上层推送 TemplateConfig。
 */
export interface AppCodingConfigFormRef {
  validateYuqueKbRepos: () => Promise<boolean>;
  validate: () => Promise<string | undefined>;
}
export const AppCodingConfigForm = forwardRef<AppCodingConfigFormRef, AppCodingConfigFormProps>(
  (
    {
      disabled = false,
      repoFieldsDisabled = false,
      initialConfig,
      onChange,
      onValidationChange,
      onWarningChange,
      modelOptions,
      modelsLoading = false,
      modelsLoadError = null,
      botId,
      onReloadModels,
    }: AppCodingConfigFormProps,
    ref,
  ) => {
    const [backendRepos, setBackendRepos] = useState<string[]>(['']);
    const [frontendRepos, setFrontendRepos] = useState<string[]>(['']);
    const [libRepos, setLibRepos] = useState<string[]>(['']);
    const [architectBotId, setArchitectBotId] = useState('');
    const [devflowWorkflow, setDevflowWorkflow] = useState<WorkflowItem | null>(null);
    const [enable24x7Hosting, setEnable24x7Hosting] = useState(true);
    const [yuqueKbRepos, setYuqueKbRepos] = useState<{ url: string; token: string }[]>([{ url: '', token: '' }]);
    const [yuqueKbErrors, setYuqueKbErrors] = useState<Record<number, string>>({});
    const [validatingYuque, setValidatingYuque] = useState(false);
    const [yuqueTooltipOpen, setYuqueTooltipOpen] = useState(false);
    const [yuqueTokenWarning, setYuqueTokenWarning] = useState<string | null>(null);
    const [image, setImage] = useState('');
    const [resourceCpu, setResourceCpu] = useState<string>('');
    const [resourceMemory, setResourceMemory] = useState<string>('');
    const [resourceDisk, setResourceDisk] = useState<string>('');
    const [resourceSpecEnabled] = useState<boolean>(() => isResourceSpecEnabledFromUrl());
    const [modelConfig, setModelConfig] = useState<TemplateConfig>({});
    const [modelValidationError, setModelValidationError] = useState<string | null>(null);
    const hasInitializedRef = useRef(false);
    const lastInitConfigRef = useRef<TemplateConfig | undefined>(undefined);
    const duplicateRepoUrls = React.useMemo(
      () => findDuplicateRepoUrls(backendRepos, frontendRepos, libRepos),
      [backendRepos, frontendRepos, libRepos],
    );
    const validateYuqueKbRepos = useCallback(async (): Promise<boolean> => {
      const hasEntriesToValidate = yuqueKbRepos.some((repo) => repo.url?.trim() && repo.token?.trim());
      if (hasEntriesToValidate) setValidatingYuque(true);
      try {
        const result = await validateYuqueKbRepoBindings(yuqueKbRepos, {
          formatWarning: formatYuquePrecheckWarning,
          allowTeamYuqueExpand: false,
        });
        setYuqueKbErrors(result.errors);
        return result.success;
      } finally {
        if (hasEntriesToValidate) setValidatingYuque(false);
      }
    }, [yuqueKbRepos]);
    const validate = useCallback(async (): Promise<string | undefined> => {
      const yuqueValid = await validateYuqueKbRepos();
      if (!yuqueValid) return '语雀知识库校验失败，请检查标红的条目';
      return modelValidationError ?? undefined;
    }, [modelValidationError, validateYuqueKbRepos]);
    useImperativeHandle(ref, () => ({ validateYuqueKbRepos, validate }), [validate, validateYuqueKbRepos]);
    useEffect(() => {
      if (!initialConfig) return;
      if (lastInitConfigRef.current === initialConfig) return;
      lastInitConfigRef.current = initialConfig;
      if (initialConfig.backend_repo && initialConfig.backend_repo.length > 0) {
        setBackendRepos(initialConfig.backend_repo.map((r: { repo_url?: string }) => r.repo_url || ''));
      }
      if (initialConfig.frontend_repo && initialConfig.frontend_repo.length > 0) {
        setFrontendRepos(initialConfig.frontend_repo.map((r: { repo_url?: string }) => r.repo_url || ''));
      }
      if (initialConfig.lib_repo && initialConfig.lib_repo.length > 0) {
        setLibRepos(initialConfig.lib_repo.map((r: { repo_url?: string }) => r.repo_url || ''));
      }
      if (initialConfig.architect_bot_id) {
        setArchitectBotId(initialConfig.architect_bot_id);
      }
      if (initialConfig.devflow_workflow) {
        setDevflowWorkflow(initialConfig.devflow_workflow);
      }
      if (initialConfig.is_hosted_24x7 !== undefined) {
        setEnable24x7Hosting(initialConfig.is_hosted_24x7 === 1);
      }
      if (initialConfig.yuque_kb_repos && initialConfig.yuque_kb_repos.length > 0) {
        setYuqueKbRepos(
          initialConfig.yuque_kb_repos.map((r: { url?: string; token?: string }) => ({
            url: r.url || '',
            token: r.token || '',
          })),
        );
      }
      if (initialConfig.image) {
        setImage(initialConfig.image);
      }
      if (resourceSpecEnabled && initialConfig.resource_spec) {
        const { cpu, memory, disk } = initialConfig.resource_spec;
        setResourceCpu(typeof cpu === 'number' && Number.isFinite(cpu) ? String(cpu) : '');
        setResourceMemory(typeof memory === 'number' && Number.isFinite(memory) ? String(memory) : '');
        setResourceDisk(typeof disk === 'number' && Number.isFinite(disk) ? String(disk) : '');
      }
      hasInitializedRef.current = true;
    }, [initialConfig, resourceSpecEnabled]);
    const buildConfig = useCallback((): TemplateConfig => {
      let resource_spec: TemplateConfig['resource_spec'] | undefined;
      if (resourceSpecEnabled) {
        const validCpu =
          isPositiveIntegerInput(resourceCpu) && resourceCpu.trim() !== '' ? Number(resourceCpu.trim()) : undefined;
        const validMemory =
          isPositiveIntegerInput(resourceMemory) && resourceMemory.trim() !== ''
            ? Number(resourceMemory.trim())
            : undefined;
        const validDisk =
          isPositiveIntegerInput(resourceDisk) && resourceDisk.trim() !== '' ? Number(resourceDisk.trim()) : undefined;
        const filledCount = [validCpu, validMemory, validDisk].filter((v) => v !== undefined).length;
        if (filledCount === 0) {
        } else if (filledCount === 3) {
          resource_spec = {
            cpu: validCpu!,
            memory: validMemory!,
            disk: validDisk!,
          };
        }
      }
      return {
        backend_repo: (backendRepos || []).filter((r) => r?.trim()).map((r) => ({ repo_url: r?.trim() || '' })),
        frontend_repo: (frontendRepos || []).filter((r) => r?.trim()).map((r) => ({ repo_url: r?.trim() || '' })),
        lib_repo: (libRepos || []).filter((r) => r?.trim()).map((r) => ({ repo_url: r?.trim() || '' })),
        architect_bot_id: architectBotId?.trim() || undefined,
        devflow_workflow: devflowWorkflow || undefined,
        is_hosted_24x7: enable24x7Hosting ? 1 : 0,
        trigger_frequency: 'daily',
        concurrency_limit: 5,
        members: '',
        yuque_kb_repos: (yuqueKbRepos || [])
          .filter((r) => r?.url?.trim())
          .map((r: { url?: string; token?: string }) => ({
            url: r?.url?.trim() || '',
            token: r?.token?.trim() || undefined,
          })),
        image: image?.trim() || undefined,
        ...(resourceSpecEnabled && resource_spec ? { resource_spec } : {}),
        ...(initialConfig?.support_engines ? { support_engines: initialConfig.support_engines } : {}),
        ...modelConfig,
      };
    }, [
      backendRepos,
      frontendRepos,
      libRepos,
      architectBotId,
      devflowWorkflow,
      enable24x7Hosting,
      yuqueKbRepos,
      image,
      resourceSpecEnabled,
      resourceCpu,
      resourceMemory,
      resourceDisk,
      modelConfig,
      initialConfig?.support_engines,
    ]);
    useEffect(() => {
      const config = buildConfig();
      onChange(config);
      onValidationChange?.(modelValidationError);
      let warning: string | null = null;
      const hasYuqueMissingToken = yuqueKbRepos.some((r) => r.url?.trim() && !r.token?.trim());
      if (hasYuqueMissingToken) {
        warning = '填写了语雀知识库地址时，Token 为必填项';
      }
      setYuqueTokenWarning(warning);
      onWarningChange?.(warning);
    }, [buildConfig, onChange, onValidationChange, onWarningChange, modelValidationError, yuqueKbRepos]);
    return (
      <div className="space-y-4 rounded-xl border border-slate-200/60 bg-slate-50/80 p-4">
        <AppCodingRepositoryFields
          disabled={disabled}
          repoFieldsDisabled={repoFieldsDisabled}
          backendRepos={backendRepos}
          frontendRepos={frontendRepos}
          libRepos={libRepos}
          setBackendRepos={setBackendRepos}
          setFrontendRepos={setFrontendRepos}
          setLibRepos={setLibRepos}
          duplicateRepoUrls={duplicateRepoUrls}
          yuqueKbRepos={yuqueKbRepos}
          setYuqueKbRepos={setYuqueKbRepos}
          yuqueKbErrors={yuqueKbErrors}
          setYuqueKbErrors={setYuqueKbErrors}
          validatingYuque={validatingYuque}
          yuqueTokenWarning={yuqueTokenWarning}
          yuqueTooltipOpen={yuqueTooltipOpen}
          setYuqueTooltipOpen={setYuqueTooltipOpen}
          architectBotId={architectBotId}
          setArchitectBotId={setArchitectBotId}
          devflowWorkflow={devflowWorkflow}
          setDevflowWorkflow={setDevflowWorkflow}
        />
        <AppCodingAdvancedConfig
          disabled={disabled}
          initialConfig={initialConfig}
          modelOptions={modelOptions}
          modelsLoading={modelsLoading}
          modelsLoadError={modelsLoadError}
          botId={botId}
          onReloadModels={onReloadModels}
          onModelChange={setModelConfig}
          onModelValidationChange={setModelValidationError}
          image={image}
          setImage={setImage}
          resourceSpecEnabled={resourceSpecEnabled}
          resourceCpu={resourceCpu}
          resourceMemory={resourceMemory}
          resourceDisk={resourceDisk}
          setResourceCpu={setResourceCpu}
          setResourceMemory={setResourceMemory}
          setResourceDisk={setResourceDisk}
        />
        {/* 7x24 小时托管 */}
        <Hosting24x7ConfigField value={enable24x7Hosting} disabled={disabled} onChange={setEnable24x7Hosting} />
      </div>
    );
  },
);
export default AppCodingConfigForm;

import { getCapabilities } from '@/capabilities';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { CodefuseTokenField } from './CodefuseTokenField';
import { ANTCHAT_MODELS, CODEX_MODELS, fetchCfuseModels, type ModelOption } from './CodingModelData';
import { CodingModelSelector } from './CodingModelSelector';
import { isCodefuseAuthPlaceholder } from './compat/codefuse';
import CodefuseAuthModal from './compat/CodefuseAuthModal';
import { getCodingRuntimeGroupKey, getModelRuntime, isCodefuseRuntime } from './compat/runtime';
import {
  filterModelsBySupportEngines,
  getCodefuseAuthRuntimeGroups,
  isRuntimeSupported,
  normalizeSupportEngines,
  supportEnginesAllowCodefuse,
} from './compat/supportEngines';
import { validateCodefuseTokenOwner } from './compat/token';
type TemplateConfig = Record<string, any>;
export type { ModelOption } from './CodingModelData';
interface CodingModelConfigFieldProps {
  disabled?: boolean;
  initialConfig?: TemplateConfig;
  onChange: (config: TemplateConfig) => void;
  onValidationChange?: (error: string | null) => void;
  label?: string;
  required?: boolean;
  /** 编辑 Bot 配置时由外层按当前 Bot 容器(/api/models)传入模型列表；未传则保留新建 Bot 的静态+CodeFuse 逻辑。 */
  modelOptions?: ModelOption[];
  modelsLoading?: boolean;
  modelsLoadError?: string | null;
  botId?: string;
  onReloadModels?: () => void | Promise<void>;
}
const isSameModelSelection = (model: ModelOption, modelId?: string, runtime?: string): boolean =>
  model.id === modelId && (!runtime || getModelRuntime(model) === runtime);
export const CodingModelConfigField: React.FC<CodingModelConfigFieldProps> = ({
  disabled = false,
  initialConfig,
  onChange,
  onValidationChange,
  label = '默认模型',
  required = false,
  modelOptions,
  modelsLoading = false,
  botId,
}) => {
  const { identity } = useHumanIdentity();
  const userNo = identity?.userId?.trim() ?? '';
  const modelsUrl = getCapabilities().getCodefuseModelsUrl().value;
  const [showCodefuseAuth, setShowCodefuseAuth] = useState(false);
  const [cfuseModels, setCfuseModels] = useState<ModelOption[]>([]);
  const [cfuseModelsLoading, setCfuseModelsLoading] = useState(false);
  const usingExternalModelOptions = modelOptions !== undefined;
  const supportEngines = useMemo(
    () => normalizeSupportEngines(initialConfig?.support_engines),
    [initialConfig?.support_engines],
  );
  const allowCodefuseModels = supportEnginesAllowCodefuse(supportEngines);
  const [selectedModel, setSelectedModel] = useState<ModelOption | null>(null);
  const [cfuseToken, setCfuseToken] = useState(initialConfig?.token || '');
  const [showModelDropdown, setShowModelDropdown] = useState(false);
  const [activeRuntimeGroup, setActiveRuntimeGroup] = useState<string | null>(null);
  const selectedModelOptionRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    setCfuseToken(initialConfig?.token || '');
  }, [initialConfig?.token]);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (usingExternalModelOptions || !allowCodefuseModels) {
        setCfuseModels([]);
        setCfuseModelsLoading(false);
        return;
      }
      if (!userNo || !modelsUrl) {
        setCfuseModels([]);
        setCfuseModelsLoading(false);
        return;
      }
      setCfuseModelsLoading(true);
      const models = await fetchCfuseModels(userNo, modelsUrl);
      if (!cancelled) {
        setCfuseModels(models);
        setCfuseModelsLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [allowCodefuseModels, modelsUrl, userNo, usingExternalModelOptions]);
  const allModels = useMemo(() => {
    const rawModels = usingExternalModelOptions
      ? modelOptions || []
      : [...ANTCHAT_MODELS, ...CODEX_MODELS, ...cfuseModels];
    return filterModelsBySupportEngines(rawModels, supportEngines);
  }, [cfuseModels, modelOptions, supportEngines, usingExternalModelOptions]);
  const codefuseAuthPlaceholders = useMemo(() => allModels.filter(isCodefuseAuthPlaceholder), [allModels]);
  const selectableModels = useMemo(() => allModels.filter((model) => !isCodefuseAuthPlaceholder(model)), [allModels]);
  const hasCodefuseAuthPlaceholder = codefuseAuthPlaceholders.length > 0;
  const isResolvingInitialModel = !!initialConfig?.model && !selectedModel && (modelsLoading || cfuseModelsLoading);
  const groupedModelEntries = useMemo(() => {
    const grouped: Record<string, ModelOption[]> = {};
    for (const model of allModels) {
      if (isCodefuseAuthPlaceholder(model)) {
        for (const runtimeGroup of getCodefuseAuthRuntimeGroups(supportEngines)) {
          if (!grouped[runtimeGroup]) grouped[runtimeGroup] = [];
          grouped[runtimeGroup].push(model);
        }
        continue;
      }
      const runtimeGroup = getCodingRuntimeGroupKey(getModelRuntime(model));
      if (!grouped[runtimeGroup]) grouped[runtimeGroup] = [];
      grouped[runtimeGroup].push(model);
    }
    return Object.entries(grouped);
  }, [allModels, supportEngines]);
  useEffect(() => {
    if (!initialConfig?.model) {
      setSelectedModel(null);
      return;
    }
    const found = selectableModels.find((model) =>
      isSameModelSelection(model, initialConfig.model, initialConfig.runtime),
    );
    if (found) {
      setSelectedModel(found);
      return;
    }
    // 编辑态外部模型列表还在加载时，先不要用 model id 构造兜底选项，
    // 避免输入框短暂展示一段口径不友好的 model id；等列表返回后再回显模型名。
    if (modelsLoading || cfuseModelsLoading) {
      setSelectedModel(null);
      return;
    }
    if (supportEngines && !isRuntimeSupported(initialConfig.runtime, supportEngines)) {
      setSelectedModel(null);
      return;
    }
    setSelectedModel({
      id: initialConfig.model,
      provider: '',
      name: initialConfig.model,
      display_name: initialConfig.model,
      runtime: initialConfig.runtime,
    });
  }, [
    initialConfig?.model,
    initialConfig?.runtime,
    selectableModels,
    supportEngines,
    modelsLoading,
    cfuseModelsLoading,
  ]);
  const selectedRuntime = selectedModel ? getModelRuntime(selectedModel) : '';
  const isSelectedCfuse = isCodefuseRuntime(selectedRuntime);
  const codefuseTokenValidationError = useMemo(() => {
    if (!allowCodefuseModels || usingExternalModelOptions || !isSelectedCfuse) {
      return null;
    }
    return validateCodefuseTokenOwner(cfuseToken);
  }, [allowCodefuseModels, cfuseToken, isSelectedCfuse, usingExternalModelOptions]);
  useEffect(() => {
    if (!showModelDropdown) return;
    const firstRuntimeGroup = groupedModelEntries[0]?.[0] ?? null;
    const selectedRuntimeGroup = getCodingRuntimeGroupKey(selectedRuntime);
    const hasRuntimeGroup = (runtimeGroup: string | null) =>
      !!runtimeGroup && groupedModelEntries.some(([groupKey]) => groupKey === runtimeGroup);

    setActiveRuntimeGroup((current) => {
      if (hasRuntimeGroup(selectedRuntimeGroup)) return selectedRuntimeGroup;
      if (hasRuntimeGroup(current)) return current;
      return firstRuntimeGroup;
    });
  }, [groupedModelEntries, selectedRuntime, showModelDropdown]);

  useEffect(() => {
    if (!showModelDropdown || !selectedModel?.id) return;
    const frame = window.requestAnimationFrame(() => {
      selectedModelOptionRef.current?.scrollIntoView({ block: 'nearest' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeRuntimeGroup, selectedModel?.id, selectedRuntime, showModelDropdown]);

  useEffect(() => {
    if (isResolvingInitialModel) {
      onValidationChange?.(null);
      return;
    }

    onChange(
      selectedModel?.id
        ? {
            ...(initialConfig?.support_engines ? { support_engines: initialConfig.support_engines } : {}),
            model: selectedModel.id,
            runtime: selectedRuntime as TemplateConfig['runtime'],
            ...(!usingExternalModelOptions
              ? {
                  token: isSelectedCfuse && cfuseToken.trim() ? cfuseToken.trim() : '',
                }
              : {}),
          }
        : initialConfig?.support_engines
        ? { support_engines: initialConfig.support_engines }
        : {},
    );
    onValidationChange?.(codefuseTokenValidationError);
  }, [
    cfuseToken,
    isSelectedCfuse,
    allowCodefuseModels,
    codefuseTokenValidationError,
    initialConfig?.support_engines,
    isResolvingInitialModel,
    onChange,
    onValidationChange,
    selectedModel,
    selectedRuntime,
    usingExternalModelOptions,
  ]);

  return (
    <div className="space-y-1.5">
      <label className="flex items-center gap-1 text-xs font-semibold text-slate-600">
        {label}
        {required ? (
          <span className="ml-0.5 text-red-500">*</span>
        ) : (
          <span className="font-normal text-slate-400">（可选）</span>
        )}
        {cfuseModelsLoading || modelsLoading ? (
          <span className="text-[10px] text-slate-400">加载模型列表...</span>
        ) : null}
      </label>
      <CodingModelSelector
        disabled={disabled}
        selectedModel={selectedModel}
        selectedRuntime={selectedRuntime}
        isResolvingInitialModel={isResolvingInitialModel}
        showDropdown={showModelDropdown}
        setShowDropdown={setShowModelDropdown}
        groupedEntries={groupedModelEntries}
        activeRuntimeGroup={activeRuntimeGroup}
        setActiveRuntimeGroup={(runtime) => setActiveRuntimeGroup(runtime)}
        hasCodefuseAuthPlaceholder={hasCodefuseAuthPlaceholder}
        botId={botId}
        onSelect={(model) => {
          setSelectedModel(model);
          setShowModelDropdown(false);
        }}
        onClear={() => {
          setSelectedModel(null);
          setCfuseToken('');
          setShowModelDropdown(false);
        }}
        onAuthorize={() => {
          if (!botId) return;
          setShowModelDropdown(false);
          setShowCodefuseAuth(true);
        }}
      />
      {!usingExternalModelOptions && isSelectedCfuse ? (
        <CodefuseTokenField
          value={cfuseToken}
          disabled={disabled}
          validationError={codefuseTokenValidationError}
          onChange={setCfuseToken}
        />
      ) : null}
      <CodefuseAuthModal
        open={showCodefuseAuth}
        botId={botId}
        onClose={() => setShowCodefuseAuth(false)}
        onSuccess={async () => {
          if (!userNo || !modelsUrl) return;
          const models = await fetchCfuseModels(userNo, modelsUrl);
          setCfuseModels(models);
        }}
      />
    </div>
  );
};

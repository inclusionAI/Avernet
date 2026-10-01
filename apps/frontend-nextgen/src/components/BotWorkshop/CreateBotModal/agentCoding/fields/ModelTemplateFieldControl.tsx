import type { ModelOption } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/CodingModelConfigField';
import { CodingModelConfigField } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/CodingModelConfigField';
import React from 'react';

type TemplateConfig = Record<string, any>;

function getTemplateConfigSignature(config: TemplateConfig, error: string | null = null): string {
  const next = { ...config } as TemplateConfig & { __validation_error?: string };
  delete next.__validation_error;
  return JSON.stringify({ config: next, error: error || null });
}

interface ModelTemplateFieldControlProps {
  label: string;
  required: boolean;
  disabled?: boolean;
  initialConfig: TemplateConfig;
  modelOptions?: ModelOption[];
  modelsLoading?: boolean;
  modelsLoadError?: string | null;
  botId?: string;
  onReloadModels?: () => void | Promise<void>;
  onChange: (next: TemplateConfig & { __validation_error?: string }) => void;
}

export const ModelTemplateFieldControl: React.FC<ModelTemplateFieldControlProps> = ({
  label,
  required,
  disabled,
  initialConfig,
  modelOptions,
  modelsLoading,
  modelsLoadError,
  botId,
  onReloadModels,
  onChange,
}) => {
  const latestConfigRef = React.useRef<TemplateConfig>(initialConfig);
  const latestErrorRef = React.useRef<string | null>(null);
  const latestOnChangeRef = React.useRef(onChange);
  const lastEmittedSignatureRef = React.useRef<string | null>(null);
  const lastSyncedConfigSignatureRef = React.useRef<string>(getTemplateConfigSignature(initialConfig));
  const [modelConfig, setModelConfig] = React.useState<TemplateConfig>(initialConfig);

  React.useEffect(() => {
    latestOnChangeRef.current = onChange;
  }, [onChange]);

  React.useEffect(() => {
    const signature = getTemplateConfigSignature(initialConfig);
    if (lastSyncedConfigSignatureRef.current === signature) return;
    lastSyncedConfigSignatureRef.current = signature;
    latestConfigRef.current = initialConfig;
    setModelConfig(initialConfig);
  }, [initialConfig]);

  const emitChange = React.useCallback((config: TemplateConfig, error: string | null) => {
    const next = { ...config } as TemplateConfig & {
      __validation_error?: string;
    };
    if (error) {
      next.__validation_error = error;
    } else {
      delete next.__validation_error;
    }
    const signature = getTemplateConfigSignature(next, error);
    if (lastEmittedSignatureRef.current === signature) return;
    lastEmittedSignatureRef.current = signature;
    latestOnChangeRef.current(next);
  }, []);

  const handleModelChange = React.useCallback(
    (next: TemplateConfig) => {
      latestConfigRef.current = next;
      lastSyncedConfigSignatureRef.current = getTemplateConfigSignature(next);
      setModelConfig((prev) => (getTemplateConfigSignature(prev) === getTemplateConfigSignature(next) ? prev : next));
      emitChange(next, latestErrorRef.current);
    },
    [emitChange],
  );

  const handleValidationChange = React.useCallback(
    (error: string | null) => {
      latestErrorRef.current = error;
      emitChange(latestConfigRef.current, error);
    },
    [emitChange],
  );

  return (
    <CodingModelConfigField
      label={label}
      required={required}
      disabled={disabled}
      initialConfig={modelConfig}
      modelOptions={modelOptions}
      modelsLoading={modelsLoading}
      modelsLoadError={modelsLoadError}
      botId={botId}
      onReloadModels={onReloadModels}
      onChange={handleModelChange}
      onValidationChange={handleValidationChange}
    />
  );
};

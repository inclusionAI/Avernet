import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Switch } from '@/components/ui/Switch';
import { Textarea } from '@/components/ui/Textarea';
import { useBotEngineOptions } from '@/hooks/useBotEngineOptions';
import type { BotCreateInput, BotCreateSpace } from '@/services/botWorkshop';
import { supportsServiceBot, type AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import { cn } from '@/utils/cn';
import type React from 'react';
import { AgentCodingSection } from './agentCoding/AgentCodingSection';
import { CreateBotEngineSelector } from './CreateBotEngineSelector';
import { DesktopDeviceFields } from './DesktopDeviceFields';

interface CreateBotFormFieldsProps {
  values: BotCreateInput;
  setValues: React.Dispatch<React.SetStateAction<BotCreateInput>>;
  spaces: BotCreateSpace[];
  creating: boolean;
  error?: string;
  agentCodingError?: string;
  agentCodingTemplates: AgentCodingTemplate[];
  agentCodingTemplatesLoading?: boolean;
  agentCodingTemplatesError?: string;
  onRetryAgentCodingTemplates?: () => void;
  onValidateReady: (validator: (() => Promise<string | undefined>) | null) => void;
  onAgentCodingErrorChange: (error?: string) => void;
  onCancel: () => void;
  onSubmit: (event: React.FormEvent) => void;
}

const inputFocusClass =
  'focus-visible:border-muted-foreground/40 focus-visible:ring-1 focus-visible:ring-muted-foreground/15 focus-visible:ring-offset-0 placeholder:text-muted-foreground/50';

export function CreateBotFormFields({
  values,
  setValues,
  spaces,
  creating,
  error,
  agentCodingError,
  agentCodingTemplates,
  agentCodingTemplatesLoading,
  agentCodingTemplatesError,
  onRetryAgentCodingTemplates,
  onValidateReady,
  onAgentCodingErrorChange,
  onCancel,
  onSubmit,
}: CreateBotFormFieldsProps) {
  const configuredEngines = useBotEngineOptions();
  const engineOptions =
    values.scenario === 'local'
      ? configuredEngines.filter((option) => ['openclaw', 'hermes'].includes(option.value))
      : configuredEngines;
  const hasEngineOptions = engineOptions.length > 0;
  const isLocal = values.scenario === 'local';
  const isAgentCoding = values.engine === 'aicoding';
  const isApplicationCoding = values.agentCoding?.kind === 'applicationCoding';
  const templateSupportsService = supportsServiceBot(values.agentCoding?.template as AgentCodingTemplate | undefined);
  const agentCodingServiceDisabled = isApplicationCoding || !templateSupportsService;
  const serviceDisabled = isLocal || values.engine === 'hermes' || (isAgentCoding && agentCodingServiceDisabled);
  const nameHasInvalidCharacter = values.name.includes('@');
  const serviceHint = isLocal
    ? '本地 Bot 暂不支持'
    : values.engine === 'hermes'
    ? 'Hermes 暂不支持'
    : isApplicationCoding || (isAgentCoding && !templateSupportsService)
    ? '当前模板未开启服务 Bot 能力'
    : '开启后不可变更';

  const handleEngineChange = (engine: string) => {
    if (engine !== 'aicoding') onAgentCodingErrorChange(undefined);
    setValues((current) => ({
      ...current,
      engine,
      agentCoding: engine === 'aicoding' ? current.agentCoding : undefined,
      serviceMode: ['hermes', 'aicoding'].includes(engine) ? 'non-service' : current.serviceMode,
    }));
  };

  const renderEnginePanel = (option: (typeof engineOptions)[number]) =>
    option.value === 'aicoding' && option.createPanel === 'agent-coding' ? (
      <>
        <AgentCodingSection
          templates={agentCodingTemplates}
          loading={agentCodingTemplatesLoading}
          error={agentCodingTemplatesError}
          value={values.agentCoding}
          disabled={creating}
          onChange={(agentCoding) => {
            onAgentCodingErrorChange(undefined);
            setValues((current) => ({
              ...current,
              agentCoding,
              serviceMode:
                agentCoding?.kind === 'template' && supportsServiceBot(agentCoding.template as AgentCodingTemplate)
                  ? current.serviceMode
                  : 'non-service',
            }));
          }}
          onValidationChange={onAgentCodingErrorChange}
          onRetry={onRetryAgentCodingTemplates}
          onValidateReady={onValidateReady}
        />
        {agentCodingError && !error && values.agentCoding?.kind !== 'applicationCoding' ? (
          <p role="alert" className="mt-2 text-xs text-destructive">
            {agentCodingError}
          </p>
        ) : null}
      </>
    ) : null;

  return (
    <form className="space-y-4" onSubmit={onSubmit}>
      {isLocal ? <DesktopDeviceFields values={values} setValues={setValues} /> : null}
      <div className="space-y-4">
        <label className="block space-y-2 text-xs font-medium text-foreground">
          <span className="flex items-center justify-between gap-2">
            <span>
              Bot 名称 <span className="text-destructive">*</span>
            </span>
            <span className="text-[10px] font-normal text-muted-foreground">{values.name.length}/40</span>
          </span>
          <Input
            value={values.name}
            maxLength={40}
            placeholder="例如：项目知识助手"
            className={inputFocusClass}
            onChange={(event) => setValues((current) => ({ ...current, name: event.target.value }))}
            aria-invalid={Boolean(error || nameHasInvalidCharacter)}
          />
          {nameHasInvalidCharacter ? (
            <span className="block text-[10px] font-normal text-destructive">名称不能包含 @</span>
          ) : null}
        </label>

        <label className="block space-y-2 text-xs font-medium text-foreground">
          <span className="flex items-center justify-between gap-2">
            <span>描述</span>
            <span className="text-[10px] font-normal text-muted-foreground">{values.description.length}/200</span>
          </span>
          <Textarea
            rows={2}
            className={cn('min-h-[60px]', inputFocusClass)}
            value={values.description}
            maxLength={200}
            placeholder="简要说明这个 Bot 能帮助你完成什么"
            onChange={(event) => setValues((current) => ({ ...current, description: event.target.value }))}
          />
        </label>
      </div>

      <div className="flex flex-col gap-2 text-xs font-medium text-foreground">
        <span id="create-bot-engine-label" className="block">
          引擎类型
        </span>
        {hasEngineOptions ? (
          <CreateBotEngineSelector
            options={engineOptions}
            value={values.engine}
            onChange={handleEngineChange}
            renderPanel={renderEnginePanel}
          />
        ) : (
          <p role="alert" className="text-xs font-normal text-destructive">
            当前环境未提供可创建引擎
          </p>
        )}
      </div>

      {/* 归属空间与提供服务拆为同构字段并排一行：提供服务不再用独立卡片，结构对齐归属空间，单行更紧凑。 */}
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2 text-xs font-medium text-foreground">
          <span id="create-bot-space-label" className="block">
            归属空间
          </span>
          <div
            aria-labelledby="create-bot-space-label"
            className="flex h-9 w-full items-center truncate rounded-md border border-input bg-muted/30 px-3 text-xs font-normal text-foreground"
          >
            {spaces[0]?.name ?? '当前空间不可用'}
          </div>
          <span className="block text-[10px] font-normal text-muted-foreground">
            跟随当前工作空间，不支持在创建时切换
          </span>
        </div>

        <div className="flex flex-col gap-2 text-xs font-medium text-foreground">
          <span className="block">提供服务</span>
          {/* h-9 透明等高行：让开关行与左侧值框同高、开关垂直居中与框内文字对齐，下行提示也随之对齐；无边框无底色，非卡片。 */}
          <div className="flex h-9 w-full items-center">
            <Switch
              checked={values.serviceMode === 'service'}
              disabled={serviceDisabled}
              onCheckedChange={(checked) =>
                setValues((current) => ({ ...current, serviceMode: checked ? 'service' : 'non-service' }))
              }
              aria-label="是否提供服务"
            />
          </div>
          <span className="block text-[10px] font-normal text-muted-foreground">{serviceHint}</span>
        </div>
      </div>

      <div className="flex items-center justify-between gap-3">
        {error ? (
          <p role="alert" className="m-0 min-w-0 flex-1 text-xs leading-5 text-destructive">
            {error}
          </p>
        ) : (
          <span aria-hidden className="flex-1" />
        )}
        <div className="flex shrink-0 items-center justify-end gap-2">
          <Button type="button" variant="secondary" disabled={creating} onClick={onCancel}>
            取消
          </Button>
          <Button
            type="submit"
            disabled={!hasEngineOptions || creating || (isLocal && !values.local?.mountPath)}
            loading={creating}
          >
            {isLocal ? '创建本地 Bot' : '创建云端 Bot'}
          </Button>
        </div>
      </div>
    </form>
  );
}

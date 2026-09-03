import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import { Textarea } from '@/components/ui/Textarea';
import { useBotEngineOptions } from '@/hooks/useBotEngineOptions';
import type { BotCreateInput, BotCreateSpace } from '@/services/botWorkshop';
import { supportsServiceBot, type AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import { Sparkles } from 'lucide-react';
import type React from 'react';
import { AgentCodingSection } from './agentCoding/AgentCodingSection';

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
  const engineOptions = useBotEngineOptions();
  const isLocal = values.scenario === 'local';
  const isAgentCoding = values.engine === 'aicoding';
  const isApplicationCoding = values.agentCoding?.kind === 'applicationCoding';
  const templateSupportsService = supportsServiceBot(values.agentCoding?.template as AgentCodingTemplate | undefined);
  const agentCodingServiceDisabled = isApplicationCoding || !templateSupportsService;
  const serviceDisabled = isLocal || values.engine === 'hermes' || (isAgentCoding && agentCodingServiceDisabled);
  const nameHasInvalidCharacter = values.name.includes('@');

  return (
    <form className="space-y-5" onSubmit={onSubmit}>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-2 text-xs font-medium text-foreground sm:col-span-2">
          <span className="flex items-center justify-between gap-2">
            <span>
              Bot 名称 <span className="text-destructive">*</span>
            </span>
            <span className="text-[10px] font-normal text-muted-foreground">{values.name.length}/40</span>
          </span>
          <Input
            autoFocus
            value={values.name}
            maxLength={40}
            placeholder="例如：项目知识助手"
            className="focus-visible:border-ring focus-visible:ring-1 focus-visible:ring-ring/30 focus-visible:ring-offset-0"
            onChange={(event) => setValues((current) => ({ ...current, name: event.target.value }))}
            aria-invalid={Boolean(error || nameHasInvalidCharacter)}
          />
          {nameHasInvalidCharacter ? (
            <span className="block text-[10px] font-normal text-destructive">名称不能包含 @</span>
          ) : null}
        </label>

        <label className="space-y-2 text-xs font-medium text-foreground sm:col-span-2">
          <span className="flex items-center justify-between gap-2">
            <span>描述</span>
            <span className="text-[10px] font-normal text-muted-foreground">{values.description.length}/200</span>
          </span>
          <Textarea
            rows={2}
            className="min-h-[60px] focus-visible:border-ring focus-visible:ring-1 focus-visible:ring-ring/30 focus-visible:ring-offset-0"
            value={values.description}
            maxLength={200}
            placeholder="简要说明这个 Bot 能帮助你完成什么"
            onChange={(event) => setValues((current) => ({ ...current, description: event.target.value }))}
          />
        </label>

        <div className="flex flex-col gap-2 text-xs font-medium text-foreground">
          <span id="create-bot-engine-label" className="block">
            引擎类型
          </span>
          <Select
            value={values.engine}
            onValueChange={(engine) =>
              setValues((current) => ({
                ...current,
                engine,
                agentCoding: engine === 'aicoding' ? current.agentCoding : undefined,
                serviceMode: ['hermes', 'aicoding'].includes(engine) ? 'non-service' : current.serviceMode,
              }))
            }
          >
            <SelectTrigger
              aria-labelledby="create-bot-engine-label"
              className="focus-visible:ring-1 focus-visible:ring-ring/40 focus-visible:ring-offset-0"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="rounded-lg p-1.5 shadow-lg">
              {engineOptions.map((option) => (
                <SelectItem
                  key={option.value}
                  value={option.value}
                  className="rounded-md py-2 pl-3 pr-8 text-sm data-[highlighted]:bg-primary/10 data-[highlighted]:text-foreground [&>span]:left-auto [&>span]:right-2"
                >
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col text-xs font-medium text-foreground">
          <span id="create-bot-space-label" className="block">
            归属空间
          </span>
          <div
            aria-labelledby="create-bot-space-label"
            className="mt-2 flex h-9 items-center rounded-md border border-input bg-muted/30 px-3 text-xs font-normal"
          >
            {spaces[0]?.name ?? '当前空间不可用'}
          </div>
          <span className="mt-1.5 block text-[10px] font-normal text-muted-foreground">
            跟随当前工作空间，不支持在创建时切换
          </span>
        </div>

        {isAgentCoding ? (
          <div className="sm:col-span-2">
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
          </div>
        ) : null}
      </div>

      <div className="grid gap-3 rounded-lg border border-border bg-muted/30 p-4 sm:grid-cols-2">
        <label className="flex items-center justify-between gap-4">
          <span>
            <span className="block text-xs font-medium text-foreground">提供服务</span>
            <span className="mt-1 block text-[10px] text-muted-foreground">
              {isLocal
                ? '本地 Bot 暂不支持'
                : values.engine === 'hermes'
                ? 'Hermes 暂不支持'
                : isApplicationCoding || (isAgentCoding && !templateSupportsService)
                ? '当前模板未开启服务 Bot 能力'
                : '开启后不可变更'}
            </span>
          </span>
          <Switch
            checked={values.serviceMode === 'service'}
            disabled={serviceDisabled}
            onCheckedChange={(checked) =>
              setValues((current) => ({ ...current, serviceMode: checked ? 'service' : 'non-service' }))
            }
            aria-label="是否提供服务"
          />
        </label>
        <label className="flex items-center justify-between gap-4 sm:border-l sm:border-border sm:pl-4">
          <span>
            <span className="flex items-center gap-1 text-xs font-medium text-foreground">
              <Sparkles aria-hidden className="size-3" />
              初始化配置
            </span>
            <span className="mt-1 block text-[10px] text-muted-foreground">预装基础能力与资源</span>
          </span>
          <Switch
            checked={values.initialize}
            onCheckedChange={(initialize) => setValues((current) => ({ ...current, initialize }))}
            aria-label="初始化配置"
          />
        </label>
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
          <Button type="submit" loading={creating}>
            {isLocal ? '创建本地 Bot' : '创建云端 Bot'}
          </Button>
        </div>
      </div>
    </form>
  );
}

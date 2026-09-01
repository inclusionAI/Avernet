import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import { Textarea } from '@/components/ui/Textarea';
import type { BotCreateAuthorization, BotCreateInput, BotCreateScenario, BotCreateSpace } from '@/services/botWorkshop';
import { Cloud, ExternalLink, Laptop, ShieldCheck, Sparkles } from 'lucide-react';
import React, { useEffect, useState } from 'react';

interface CreateBotModalProps {
  scenario?: BotCreateScenario;
  spaces: BotCreateSpace[];
  creating: boolean;
  authorization?: BotCreateAuthorization & { message?: string; error?: string };
  onClose: () => void;
  onSubmit: (input: BotCreateInput) => Promise<void>;
}

const engineOptions = [
  { value: 'openclaw', label: 'OpenClaw' },
  { value: 'claude_code', label: 'Claudecode引擎-原生' },
  { value: 'aicoding', label: 'Claudecode引擎-AIcoding' },
  { value: 'hermes', label: 'Hermes' },
  { value: 'teclaw', label: 'TEClaw' },
];

const initialValues = (scenario: BotCreateScenario, spaces: BotCreateSpace[]): BotCreateInput => {
  const firstSpace = spaces[0] ?? { id: '', ownership: 'personal' as const };
  return {
    scenario,
    name: '',
    description: '',
    engine: 'openclaw',
    spaceId: firstSpace.id,
    ownership: firstSpace.ownership,
    serviceMode: 'non-service',
    initialize: true,
  };
};

const CreateBotModal: React.FC<CreateBotModalProps> = ({
  scenario,
  spaces,
  creating,
  authorization,
  onClose,
  onSubmit,
}) => {
  const [values, setValues] = useState<BotCreateInput>(() => initialValues('cloud', spaces));
  const [error, setError] = useState<string>();
  const open = Boolean(scenario);

  useEffect(() => {
    if (scenario) {
      setValues(initialValues(scenario, spaces));
      setError(undefined);
    }
  }, [scenario, spaces]);

  const selectedSpace = spaces[0];
  const isLocal = scenario === 'local';
  const serviceDisabled = isLocal || values.engine === 'hermes' || values.engine === 'aicoding';

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(undefined);
    try {
      await onSubmit(values);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '创建失败，请重试');
    }
  };

  return (
    <Modal open={open} onOpenChange={(nextOpen) => !nextOpen && !creating && onClose()}>
      <ModalContent size="lg" aria-describedby="create-bot-description">
        {authorization ? (
          <>
            <ModalHeader>
              <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                <ShieldCheck aria-hidden className="size-5" />
              </div>
              <ModalTitle>完成 AgentPass 授权</ModalTitle>
              <ModalDescription id="create-bot-description">
                请在下方完成授权。授权成功后系统会自动确认并完成 Bot 创建，请勿重复提交。
              </ModalDescription>
            </ModalHeader>
            {authorization.iframeUrl ? (
              <iframe
                title="AgentPass 授权"
                src={authorization.iframeUrl}
                className="h-[520px] w-full rounded-lg border border-border bg-background"
                referrerPolicy="strict-origin-when-cross-origin"
              />
            ) : (
              <div className="flex min-h-64 flex-col items-center justify-center rounded-lg border border-border bg-muted/30 p-6 text-center">
                <p className="m-0 text-xs text-muted-foreground">授权服务要求在新窗口中继续。</p>
                <Button
                  className="mt-4"
                  leftIcon={<ExternalLink className="size-4" />}
                  onClick={() => window.open(authorization.redirectUrl, '_blank', 'noopener,noreferrer')}
                >
                  打开授权页面
                </Button>
              </div>
            )}
            <div aria-live="polite" className="rounded-lg border border-border bg-muted/30 px-4 py-3 text-xs">
              {authorization.error ? (
                <span className="text-destructive">{authorization.error}</span>
              ) : (
                <span className="text-muted-foreground">
                  {authorization.message || '正在等待授权结果，完成后将自动关闭此窗口…'}
                </span>
              )}
            </div>
            <ModalFooter>
              <Button variant="secondary" onClick={onClose}>
                取消创建
              </Button>
              {authorization.redirectUrl && authorization.iframeUrl ? (
                <Button
                  variant="secondary"
                  leftIcon={<ExternalLink className="size-4" />}
                  onClick={() => window.open(authorization.redirectUrl, '_blank', 'noopener,noreferrer')}
                >
                  新窗口打开
                </Button>
              ) : null}
            </ModalFooter>
          </>
        ) : (
          <>
            <ModalHeader>
              <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                {isLocal ? <Laptop aria-hidden className="size-5" /> : <Cloud aria-hidden className="size-5" />}
              </div>
              <ModalTitle>{isLocal ? '创建本地 Bot' : '创建云端 Bot'}</ModalTitle>
              <ModalDescription id="create-bot-description">
                {isLocal
                  ? 'Bot 在个人设备中运行，不提供服务化能力。'
                  : 'Bot 在云端运行，可按引擎能力选择是否提供服务。'}
              </ModalDescription>
            </ModalHeader>

            <form className="space-y-5" onSubmit={submit}>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="space-y-1.5 text-xs font-medium text-foreground sm:col-span-2">
                  Bot 名称 <span className="text-destructive">*</span>
                  <Input
                    autoFocus
                    value={values.name}
                    maxLength={40}
                    placeholder="例如：项目知识助手"
                    onChange={(event) => setValues((current) => ({ ...current, name: event.target.value }))}
                    aria-invalid={Boolean(error)}
                  />
                  <span className="block text-[10px] font-normal text-muted-foreground">
                    名称不能包含 @，最多 40 个字符
                  </span>
                </label>

                <div className="space-y-1.5 text-xs font-medium text-foreground">
                  <span id="create-bot-engine-label">引擎类型</span>
                  <Select
                    value={values.engine}
                    onValueChange={(engine) =>
                      setValues((current) => ({
                        ...current,
                        engine,
                        serviceMode: ['hermes', 'aicoding'].includes(engine) ? 'non-service' : current.serviceMode,
                      }))
                    }
                  >
                    <SelectTrigger aria-labelledby="create-bot-engine-label">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {engineOptions.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5 text-xs font-medium text-foreground">
                  <span id="create-bot-space-label">归属空间</span>
                  <div
                    aria-labelledby="create-bot-space-label"
                    className="flex h-9 items-center rounded-md border border-input bg-muted/30 px-3 text-xs font-normal"
                  >
                    {selectedSpace?.name ?? '当前空间不可用'}
                  </div>
                  <span className="block text-[10px] font-normal text-muted-foreground">
                    跟随当前工作空间，不支持在创建时切换
                  </span>
                </div>

                <label className="space-y-1.5 text-xs font-medium text-foreground sm:col-span-2">
                  描述
                  <Textarea
                    value={values.description}
                    maxLength={200}
                    placeholder="简要说明这个 Bot 能帮助你完成什么"
                    onChange={(event) => setValues((current) => ({ ...current, description: event.target.value }))}
                  />
                </label>
              </div>

              <div className="grid gap-3 rounded-lg border border-border bg-muted/30 p-4 sm:grid-cols-2">
                <label className="flex items-center justify-between gap-4">
                  <span>
                    <span className="block text-xs font-medium text-foreground">提供服务</span>
                    <span className="mt-1 block text-[10px] text-muted-foreground">
                      {isLocal
                        ? '本地 Bot 暂不支持'
                        : values.engine === 'hermes' || values.engine === 'aicoding'
                        ? `${values.engine === 'hermes' ? 'Hermes' : 'AIcoding'} 暂不支持`
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

              {error && (
                <p role="alert" className="m-0 text-xs text-destructive">
                  {error}
                </p>
              )}
              <p className="m-0 text-[10px] text-muted-foreground">
                将通过 Avernet OpenAPI 创建。归属：{selectedSpace?.name ?? '请选择有效空间'}
              </p>

              <ModalFooter>
                <Button type="button" variant="secondary" disabled={creating} onClick={onClose}>
                  取消
                </Button>
                <Button type="submit" loading={creating}>
                  {isLocal ? '创建本地 Bot' : '创建云端 Bot'}
                </Button>
              </ModalFooter>
            </form>
          </>
        )}
      </ModalContent>
    </Modal>
  );
};

export default CreateBotModal;

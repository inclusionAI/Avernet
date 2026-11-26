import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { BotChannel, BotChannelInput, ChannelBindingMode, ChannelStage } from '@/domain/botAdvancedConfig';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { BcnChannelFields, ChannelSwitch } from './ChannelModeFields';

const stageOptions: Array<{ value: ChannelStage; label: string }> = [
  { value: 'draft', label: '草稿' },
  { value: 'verify', label: '预发' },
  { value: 'online', label: '线上' },
];

export const emptyChannelInput: BotChannelInput = {
  bindingMode: 'plugin',
  stage: undefined,
  description: '',
  clientId: '',
  clientSecret: '',
  enableStreamingCards: false,
  cardTemplateId: '',
  cardTemplateKey: '',
  dmPolicy: 'open',
  allowlist: ['*'],
  replyToMessage: true,
  aixEnable: true,
  includeSenderName: true,
  robotCode: '',
  groupChatScope: 'per_sender',
  outboundVisibility: 'full_transcript',
};

const fromChannel = (channel: BotChannel): BotChannelInput => ({
  bindingMode: channel.bindingMode,
  stage: channel.stage,
  description: channel.description ?? '',
  clientId: channel.clientId,
  clientSecret: '',
  enableStreamingCards: channel.enableStreamingCards,
  cardTemplateId: channel.cardTemplateId ?? '',
  cardTemplateKey: channel.cardTemplateKey ?? '',
  dmPolicy: channel.dmPolicy,
  allowlist: channel.allowlist,
  replyToMessage: channel.replyToMessage,
  aixEnable: channel.aixEnable,
  includeSenderName: channel.includeSenderName,
  robotCode: channel.robotCode ?? '',
  groupChatScope: channel.groupChatScope ?? 'per_sender',
  outboundVisibility: channel.outboundVisibility ?? 'full_transcript',
  forwardSenderIdentity: channel.forwardSenderIdentity ?? false,
});

export function ChannelFormModal({
  open,
  channel,
  bindingMode,
  serviceBot = false,
  onOpenChange,
  onSubmit,
}: {
  open: boolean;
  channel?: BotChannel;
  bindingMode: ChannelBindingMode;
  serviceBot?: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: BotChannelInput) => Promise<void>;
}) {
  const [form, setForm] = useState(emptyChannelInput);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) setForm(channel ? fromChannel(channel) : { ...emptyChannelInput, bindingMode });
  }, [bindingMode, channel, open]);
  const isPlugin = form.bindingMode === 'plugin';
  const selectStage = isPlugin && serviceBot;
  const valid = Boolean(
    form.clientId.trim() &&
      (Boolean(channel?.hasSecret) || form.clientSecret.trim()) &&
    (form.bindingMode !== 'bcn_gateway' || form.robotCode?.trim()) &&
      (!selectStage || Boolean(channel) || form.stage) &&
      (!form.enableStreamingCards || (form.cardTemplateId.trim() && (!isPlugin || form.cardTemplateKey.trim()))),
  );
  const submit = async () => {
    setSaving(true);
    try {
      await onSubmit({ ...form, allowlist: form.allowlist.filter(Boolean) });
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="xl">
        <ModalHeader>
          <ModalTitle>{channel ? '编辑钉钉渠道' : '新建钉钉机器人绑定'}</ModalTitle>
          <ModalDescription>
            {isPlugin
              ? '配置钉钉开放平台的 app_key、app_secret 与渠道场景。'
              : '配置钉钉开放平台的 Robot Code、app_key 与 app_secret。'}
          </ModalDescription>
        </ModalHeader>
        <div className="rounded-md border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
          绑定方式：
          <span className="font-medium text-foreground">
            {form.bindingMode === 'plugin' ? '基于开源插件' : '基于 BCN'}
          </span>
          {channel ? '。已创建的渠道不可切换绑定方式，如需切换请删除后重建。' : ''}
        </div>
        <div className="grid gap-x-5 gap-y-5 md:grid-cols-2 xl:grid-cols-3">
          {isPlugin ? (
            <label className="space-y-2 text-sm font-medium md:col-span-2 xl:col-span-3">
              <span>场景描述</span>
              <Textarea
                value={form.description}
                maxLength={32}
                placeholder="请输入场景描述（最多 32 个字符）"
                rows={2}
                className="min-h-20 resize-y"
                onChange={(event) => setForm({ ...form, description: event.target.value })}
              />
            </label>
          ) : null}
          {selectStage ? (
            <label className="space-y-2 text-sm font-medium">
              <span>
                生效环境 <span className="text-destructive">*</span>
              </span>
              <Select
                value={form.stage}
                disabled={Boolean(channel)}
                onValueChange={(stage) => setForm({ ...form, stage: stage as ChannelStage })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="请选择生效环境" />
                </SelectTrigger>
                <SelectContent>
                  {stageOptions.map((stage) => (
                    <SelectItem key={stage.value} value={stage.value}>
                      {stage.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {channel ? <span className="block text-muted-foreground">创建后不可修改</span> : null}
            </label>
          ) : null}
          {!isPlugin ? <BcnChannelFields form={form} onChange={setForm} /> : null}
          <label className="space-y-2 text-sm font-medium">
            <span>
              app_key <span className="text-destructive">*</span>
            </span>
            <Input
              value={form.clientId}
              className="h-11"
              placeholder="请输入 app_key"
              onChange={(event) => setForm({ ...form, clientId: event.target.value })}
            />
          </label>
          <label className="space-y-2 text-sm font-medium">
            <span>
              app_secret <span className="text-destructive">*</span>
            </span>
            <Input
              type="password"
              value={form.clientSecret}
              className="h-11"
              placeholder={channel?.hasSecret ? '留空则保持原 app_secret' : '请输入 app_secret'}
              onChange={(event) => setForm({ ...form, clientSecret: event.target.value })}
            />
          </label>
          <div className="md:col-span-2 xl:col-span-3">
            <ChannelSwitch
              label="启用钉钉端流式输出"
              description="使用互动卡片持续更新回复"
              checked={form.enableStreamingCards}
              onChange={(enableStreamingCards) => setForm({ ...form, enableStreamingCards })}
            />
          </div>
          {form.enableStreamingCards ? (
            <>
              <label className="space-y-2 text-sm font-medium md:col-span-2">
                <span>
                  卡片 templateId <span className="text-destructive">*</span>
                </span>
                <Input
                  value={form.cardTemplateId}
                  className="h-11"
                  onChange={(event) => setForm({ ...form, cardTemplateId: event.target.value })}
                />
              </label>
              {isPlugin ? (
                <label className="space-y-2 text-sm font-medium">
                  <span>
                    卡片变量 key 值 <span className="text-destructive">*</span>
                  </span>
                  <Input
                    value={form.cardTemplateKey}
                    className="h-11"
                    placeholder="请输入卡片变量 key 值"
                    onChange={(event) => setForm({ ...form, cardTemplateKey: event.target.value })}
                  />
                </label>
              ) : null}
            </>
          ) : null}
        </div>
        <ModalFooter>
          <Button variant="secondary" disabled={saving} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={!valid || saving}
            leftIcon={saving ? <Loader2 className="size-4 animate-spin" /> : undefined}
            onClick={() => void submit()}
          >
            保存
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotChannel, BotChannelInput, ChannelBindingMode } from '@/domain/botAdvancedConfig';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { BcnChannelFields, ChannelSwitch, PluginChannelFields } from './ChannelModeFields';

export const emptyChannelInput: BotChannelInput = {
  bindingMode: 'plugin',
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
});

export function ChannelFormModal({
  open,
  channel,
  bindingMode,
  onOpenChange,
  onSubmit,
}: {
  open: boolean;
  channel?: BotChannel;
  bindingMode: ChannelBindingMode;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: BotChannelInput) => Promise<void>;
}) {
  const [form, setForm] = useState(emptyChannelInput);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) setForm(channel ? fromChannel(channel) : { ...emptyChannelInput, bindingMode });
  }, [bindingMode, channel, open]);
  const valid =
    form.clientId.trim() &&
    (Boolean(channel?.hasSecret) || form.clientSecret.trim()) &&
    (form.bindingMode !== 'bcn_gateway' || form.robotCode?.trim()) &&
    (!form.enableStreamingCards || form.cardTemplateId.trim());
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
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>{channel ? '编辑钉钉渠道' : '新建钉钉机器人配置'}</ModalTitle>
          <ModalDescription>渠道配置仅修改当前草稿，随 Bot 发布流程进入后续阶段。</ModalDescription>
        </ModalHeader>
        <div className="rounded-md border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
          绑定方式：
          <span className="font-medium text-foreground">
            {form.bindingMode === 'plugin' ? '基于开源插件' : '基于 BCN'}
          </span>
          {channel ? '。已创建的渠道不可切换绑定方式，如需切换请删除后重建。' : ''}
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="space-y-1.5 text-xs font-medium sm:col-span-2">
            场景描述
            <Input
              value={form.description}
              placeholder="例如：研发答疑群机器人"
              onChange={(event) => setForm({ ...form, description: event.target.value })}
            />
          </label>
          <label className="space-y-1.5 text-xs font-medium">
            机器人 ID
            <Input value={form.clientId} onChange={(event) => setForm({ ...form, clientId: event.target.value })} />
          </label>
          <label className="space-y-1.5 text-xs font-medium">
            Client Secret
            <Input
              type="password"
              value={form.clientSecret}
              placeholder={channel?.hasSecret ? '留空则保持原 Secret' : '请输入 Client Secret'}
              onChange={(event) => setForm({ ...form, clientSecret: event.target.value })}
            />
          </label>
          {form.bindingMode === 'plugin' ? (
            <PluginChannelFields form={form} onChange={setForm} />
          ) : (
            <BcnChannelFields form={form} onChange={setForm} />
          )}
          <ChannelSwitch
            label="流式输出"
            description="使用互动卡片持续更新回复"
            checked={form.enableStreamingCards}
            onChange={(enableStreamingCards) => setForm({ ...form, enableStreamingCards })}
          />
          {form.enableStreamingCards ? (
            <>
              <label className="space-y-1.5 text-xs font-medium">
                互动卡片模板 ID
                <Input
                  value={form.cardTemplateId}
                  onChange={(event) => setForm({ ...form, cardTemplateId: event.target.value })}
                />
              </label>
              <label className="space-y-1.5 text-xs font-medium">
                卡片正文模板字段
                <Input
                  value={form.cardTemplateKey}
                  placeholder="可选"
                  onChange={(event) => setForm({ ...form, cardTemplateKey: event.target.value })}
                />
              </label>
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

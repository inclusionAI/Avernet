import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import type { BotChannel, BotChannelInput } from '@/domain/botAdvancedConfig';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';

export const emptyChannelInput: BotChannelInput = {
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
};

const fromChannel = (channel: BotChannel): BotChannelInput => ({
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
});

function ChannelSwitch({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded-md border border-border p-3 text-xs">
      <span>
        <span className="block font-medium">{label}</span>
        <span className="mt-1 block text-muted-foreground">{description}</span>
      </span>
      <Switch checked={checked} onCheckedChange={onChange} />
    </label>
  );
}

export function ChannelFormModal({
  open,
  channel,
  onOpenChange,
  onSubmit,
}: {
  open: boolean;
  channel?: BotChannel;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: BotChannelInput) => Promise<void>;
}) {
  const [form, setForm] = useState(emptyChannelInput);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) setForm(channel ? fromChannel(channel) : emptyChannelInput);
  }, [channel, open]);
  const valid =
    form.clientId.trim() &&
    (Boolean(channel?.hasSecret) || form.clientSecret.trim()) &&
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
          <ModalTitle>{channel ? '编辑钉钉渠道' : '绑定钉钉渠道'}</ModalTitle>
          <ModalDescription>渠道配置仅修改当前草稿，随 Bot 发布流程进入后续阶段。</ModalDescription>
        </ModalHeader>
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
          <label className="space-y-1.5 text-xs font-medium">
            私聊策略
            <Select
              value={form.dmPolicy}
              onValueChange={(dmPolicy: 'open' | 'disabled') => setForm({ ...form, dmPolicy })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="open">允许私聊</SelectItem>
                <SelectItem value="disabled">禁止私聊</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-1.5 text-xs font-medium">
            用户白名单
            <Input
              value={form.allowlist.join(',')}
              placeholder="* 或多个用户 ID，以逗号分隔"
              onChange={(event) =>
                setForm({ ...form, allowlist: event.target.value.split(',').map((value) => value.trim()) })
              }
            />
          </label>
          <ChannelSwitch
            label="回复原消息"
            description="回复与来源消息保持关联"
            checked={form.replyToMessage}
            onChange={(replyToMessage) => setForm({ ...form, replyToMessage })}
          />
          <ChannelSwitch
            label="包含发送者名称"
            description="将发送者名称加入 Bot 上下文"
            checked={form.includeSenderName}
            onChange={(includeSenderName) => setForm({ ...form, includeSenderName })}
          />
          <ChannelSwitch
            label="启用 AIX"
            description="开启钉钉 AI 卡片扩展"
            checked={form.aixEnable}
            onChange={(aixEnable) => setForm({ ...form, aixEnable })}
          />
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

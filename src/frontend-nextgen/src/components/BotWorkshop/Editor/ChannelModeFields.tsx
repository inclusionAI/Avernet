import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import type { BotChannelInput } from '@/domain/botAdvancedConfig';

export function ChannelSwitch({
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

export function PluginChannelFields({
  form,
  onChange,
}: {
  form: BotChannelInput;
  onChange: (form: BotChannelInput) => void;
}) {
  return (
    <>
      <label className="space-y-1.5 text-xs font-medium">
        私聊策略
        <Select
          value={form.dmPolicy}
          onValueChange={(dmPolicy: 'open' | 'disabled') => onChange({ ...form, dmPolicy })}
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
            onChange({ ...form, allowlist: event.target.value.split(',').map((value) => value.trim()) })
          }
        />
      </label>
      <ChannelSwitch
        label="回复原消息"
        description="回复与来源消息保持关联"
        checked={form.replyToMessage}
        onChange={(replyToMessage) => onChange({ ...form, replyToMessage })}
      />
      <ChannelSwitch
        label="包含发送者名称"
        description="将发送者名称加入 Bot 上下文"
        checked={form.includeSenderName}
        onChange={(includeSenderName) => onChange({ ...form, includeSenderName })}
      />
      <ChannelSwitch
        label="启用 AIX"
        description="开启钉钉 AI 卡片扩展"
        checked={form.aixEnable}
        onChange={(aixEnable) => onChange({ ...form, aixEnable })}
      />
    </>
  );
}

export function BcnChannelFields({
  form,
  onChange,
}: {
  form: BotChannelInput;
  onChange: (form: BotChannelInput) => void;
}) {
  return (
    <>
      <label className="space-y-1.5 text-xs font-medium sm:col-span-2">
        Robot Code
        <Input
          value={form.robotCode ?? ''}
          placeholder="请输入钉钉机器人 Robot Code"
          onChange={(event) => onChange({ ...form, robotCode: event.target.value })}
        />
      </label>
      <label className="space-y-1.5 text-xs font-medium">
        群聊会话模式
        <Select
          value={form.groupChatScope}
          onValueChange={(groupChatScope: 'per_sender' | 'conversation_shared') =>
            onChange({ ...form, groupChatScope })
          }
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="per_sender">按发送者隔离会话</SelectItem>
            <SelectItem value="conversation_shared">群内共享会话</SelectItem>
          </SelectContent>
        </Select>
      </label>
      <label className="space-y-1.5 text-xs font-medium">
        主动消息可见范围
        <Select
          value={form.outboundVisibility}
          onValueChange={(outboundVisibility: 'full_transcript' | 'lead_only') =>
            onChange({ ...form, outboundVisibility })
          }
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="full_transcript">完整会话可见</SelectItem>
            <SelectItem value="lead_only">仅发起人可见</SelectItem>
          </SelectContent>
        </Select>
      </label>
    </>
  );
}

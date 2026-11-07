import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
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
    <label className="flex cursor-pointer items-start gap-3 border-t border-divider py-4 first:border-t-0">
      <Checkbox className="mt-0.5" checked={checked} onCheckedChange={onChange} />
      <span className="min-w-0">
        <span className="block text-sm font-medium">{label}</span>
        <span className="mt-1 block text-sm leading-6 text-muted-foreground">{description}</span>
      </span>
    </label>
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
      <label className="space-y-2 text-sm font-medium xl:col-span-1">
        <span>
          Robot Code <span className="text-destructive">*</span>
        </span>
        <Input
          value={form.robotCode ?? ''}
          className="h-11"
          placeholder="钉钉开放平台 Robot Code"
          onChange={(event) => onChange({ ...form, robotCode: event.target.value })}
        />
      </label>
      <div className="md:col-span-2 xl:col-span-3">
        <ChannelSwitch
          label="透传钉钉发送人身份"
          description="开启后，Bot 可感知普通钉钉消息的发送人，便于按人回复。"
          checked={Boolean(form.forwardSenderIdentity)}
          onChange={(forwardSenderIdentity) => onChange({ ...form, forwardSenderIdentity })}
        />
      </div>
    </>
  );
}

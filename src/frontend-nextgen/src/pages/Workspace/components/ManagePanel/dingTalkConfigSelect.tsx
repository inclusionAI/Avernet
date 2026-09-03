import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui';
import type {
  DingTalkGroupChatScope,
  DingTalkOutboundVisibility,
  GroupDingTalkConfig,
} from '@/services/workspace/channelBindingService';

/** 会话模式（group_chat_scope）选项：对齐 open-claw 标签。 */
export const SCOPE_OPTIONS: { value: DingTalkGroupChatScope; label: string }[] = [
  { value: 'per_sender', label: '按发送人独立会话' },
  { value: 'conversation_shared', label: '共享同一个会话' },
];

/** 发送消息范围（outbound_visibility）选项：对齐 open-claw 标签。 */
export const VISIBILITY_OPTIONS: { value: DingTalkOutboundVisibility; label: string }[] = [
  { value: 'full_transcript', label: '完整群聊消息' },
  { value: 'lead_only', label: '仅 Driver 消息' },
];

export const scopeLabel = (v: GroupDingTalkConfig['groupChatScope']) =>
  SCOPE_OPTIONS.find((o) => o.value === v)?.label ?? v;
export const visibilityLabel = (v: GroupDingTalkConfig['outboundVisibility']) =>
  VISIBILITY_OPTIONS.find((o) => o.value === v)?.label ?? v;

/**
 * 钉钉配置下拉选择（会话模式 / 发送消息范围），对齐 open-claw 的 Select 样式，
 * 而非二元 Segmented 开关。
 */
export function ConfigSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div>
      <p className="mb-1.5 text-xs text-muted-foreground">{label}</p>
      <Select value={value} onValueChange={(v) => onChange(v as T)}>
        <SelectTrigger aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}（{o.value}）
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

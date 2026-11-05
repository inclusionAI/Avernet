/**
 * 协作能力与发布审批共用的纯展示开关行：左侧标题/说明，右侧状态徽标/原因 Tooltip/开关。
 * 从 settingsSections 原样提取，避免能力分区与发布审批分区各自复制行样式。
 */
import { Badge } from '@/components/ui/Badge';
import { Switch } from '@/components/ui/Switch';
import { cn } from '@/utils/cn';
import { ControlStateTooltip } from './HelpTooltip';

export interface SettingRowProps {
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  busy?: boolean;
  status?: string;
  statusReason?: string;
  onChange: (checked: boolean) => void;
  /** 容器布局不同时的行内边距覆盖；缺省为 divide-y 列表语义（py-3 first:pt-0 last:pb-0）。 */
  paddingClassName?: string;
}

export function SettingRow({
  label,
  description,
  checked,
  disabled,
  busy,
  status,
  statusReason,
  onChange,
  paddingClassName,
}: SettingRowProps) {
  return (
    <div className={cn('flex items-start justify-between gap-4', paddingClassName ?? 'py-3 first:pt-0 last:pb-0')}>
      <div className="min-w-0 flex-1">
        <p className="m-0 text-sm font-medium text-foreground">{label}</p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {status && <Badge tone="neutral">{status}</Badge>}
        {status && statusReason && <ControlStateTooltip label={label} status={status} content={statusReason} />}
        <Switch
          checked={checked}
          disabled={disabled || busy}
          aria-label={`${checked ? '关闭' : '开启'}${label}`}
          onCheckedChange={onChange}
        />
      </div>
    </div>
  );
}

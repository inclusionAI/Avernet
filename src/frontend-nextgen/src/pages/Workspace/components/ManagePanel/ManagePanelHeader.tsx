import { Badge, IconButton } from '@/components/ui';
import { X } from 'lucide-react';

export interface ManagePanelHeaderProps {
  title: string;
  description: string;
  subtitle?: string;
  statusLabel: '可管理' | '可查看';
  onClose: () => void;
}

/** 群/会话管理右侧栏头部：沿用 PRD 侧边栏的装饰与状态徽标样式。 */
export function ManagePanelHeader({ title, description, subtitle, statusLabel, onClose }: ManagePanelHeaderProps) {
  return (
    <div className="relative overflow-hidden border-b border-[var(--color-border)] px-5 py-4">
      <div className="pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full bg-[var(--color-primary-soft)] blur-2xl" />

      <div className="relative flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="m-0 text-base font-semibold text-[var(--color-fg)]">{title}</h3>
            <Badge tone={statusLabel === '可管理' ? 'success' : 'neutral'}>{statusLabel}</Badge>
          </div>
          <p className="m-0 mt-1 text-xs leading-5 text-[var(--color-muted)]">{description}</p>
          {subtitle ? <p className="m-0 mt-0.5 truncate text-xs text-[var(--color-muted)]">{subtitle}</p> : null}
        </div>
        <IconButton label="关闭管理面板" icon={<X className="h-4 w-4" />} size="sm" variant="ghost" onClick={onClose} />
      </div>
    </div>
  );
}

import { Button } from '@/components/ui';
import type { ParticipantDefinition } from '@/services/workspace/collaborationDefinitionService';
import { cn } from '@/utils/cn';

export interface ParticipantBindingPanelProps {
  definitions: ParticipantDefinition[];
  /** key → 绑定的 bot id（单选）。 */
  bindings: Record<string, string>;
  activeKey: string;
  onActiveKeyChange: (key: string) => void;
}

/**
 * 角色绑定面板：顶部 participant tab + 已绑定状态展示。
 * Bot 选择直接在下方「成员 Bot」picker 中完成（点击即绑定到当前选中角色），
 * 因此这里无需重复展示 Bot 列表。
 */
export function ParticipantBindingPanel({
  definitions,
  bindings,
  activeKey,
  onActiveKeyChange,
}: ParticipantBindingPanelProps) {
  const boundCount = Object.values(bindings).filter(Boolean).length;
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-[var(--color-border)] bg-white p-3">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-bold text-[var(--color-muted)]">角色绑定</span>
        <span className="text-[11px] text-[var(--color-muted)]">
          已绑定 {boundCount} / {definitions.length} 个角色
        </span>
      </div>
      <p className="text-[11px] leading-4 text-[var(--color-muted)]">
        在下方「成员 Bot」中点击 Bot 即可将它绑定到当前选中的角色。
      </p>
      <div className="flex flex-wrap gap-1.5 border-b border-[var(--color-border)] pb-2">
        {definitions.map((def) => {
          const isActive = def.key === activeKey;
          const boundBotId = bindings[def.key];
          const bound = Boolean(boundBotId);
          const label = def.displayName || def.key;
          return (
            <Button
              key={def.key}
              type="button"
              variant="ghost"
              onClick={() => onActiveKeyChange(def.key)}
              className={cn(
                'h-auto flex-col items-start gap-0.5 rounded-lg border px-2.5 py-1.5',
                isActive
                  ? 'border-[var(--color-primary)] bg-[var(--color-primary-soft)]'
                  : 'border-[var(--color-border)] bg-white hover:border-[var(--color-primary)]/30',
              )}
            >
              <span
                className={cn(
                  'text-xs font-medium',
                  isActive ? 'text-[var(--color-primary)]' : 'text-[var(--color-fg)]',
                )}
              >
                {label}
                {def.required && <span className="text-[var(--color-error)]"> *</span>}
              </span>
              <span className="text-[10px] text-[var(--color-muted)]">
                {bound ? '已绑定' : def.required ? '未绑定' : '可选'}
              </span>
            </Button>
          );
        })}
      </div>
    </div>
  );
}

export default ParticipantBindingPanel;

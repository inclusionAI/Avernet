import { Button } from '@/components/ui';
import type { ParticipantDefinition } from '@/services/workspace/collaborationDefinitionService';
import { cn } from '@/utils/cn';
import { X } from 'lucide-react';

export interface ParticipantBindingPanelProps {
  definitions: ParticipantDefinition[];
  /** key → 绑定的 bot id（单选）。 */
  bindings: Record<string, string>;
  activeKey: string;
  onActiveKeyChange: (key: string) => void;
  /** 通过 botId 解析展示名称。 */
  botNameResolver?: (botId: string) => string | undefined;
  /** 解除某角色的绑定。 */
  onUnbind?: (key: string) => void;
}

/** 角色绑定面板：每个角色一行，显示角色名 + 已绑定 Bot（可移除）或未绑定状态。 */
export function ParticipantBindingPanel({
  definitions,
  bindings,
  activeKey,
  onActiveKeyChange,
  botNameResolver,
  onUnbind,
}: ParticipantBindingPanelProps) {
  const boundCount = Object.values(bindings).filter(Boolean).length;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-background p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-muted-foreground">角色绑定</span>
        <span className="text-[11px] text-muted-foreground">
          已绑定 {boundCount} / {definitions.length} 个角色
        </span>
      </div>
      <div className="space-y-1.5">
        {definitions.map((def) => {
          const isActive = def.key === activeKey;
          const boundBotId = bindings[def.key];
          const bound = Boolean(boundBotId);
          const label = def.displayName || def.key;
          const botName = boundBotId ? botNameResolver?.(boundBotId) ?? boundBotId : undefined;
          return (
            <div
              key={def.key}
              role="button"
              tabIndex={0}
              onClick={() => onActiveKeyChange(def.key)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onActiveKeyChange(def.key);
                }
              }}
              className={cn(
                'flex min-w-0 cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 transition-colors',
                isActive ? 'border-primary bg-primary/10' : 'border-border bg-background hover:border-primary/30',
              )}
            >
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className={cn('text-xs font-semibold', isActive ? 'text-primary' : 'text-foreground')}>
                  {label}
                  {def.required && <span className="text-destructive"> *</span>}
                </span>
                {bound && botName ? (
                  <span className="truncate text-[11px] text-primary">@{botName}</span>
                ) : (
                  <span className="text-[10px] text-muted-foreground">{def.required ? '未绑定' : '可选'}</span>
                )}
              </div>
              {bound && onUnbind && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`移除${label}的绑定`}
                  className="h-6 w-6 shrink-0 rounded-full border-0 p-0 text-muted-foreground hover:bg-muted"
                  onClick={(event) => {
                    event.stopPropagation();
                    onUnbind(def.key);
                  }}
                >
                  <X className="h-3 w-3" aria-hidden />
                </Button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default ParticipantBindingPanel;

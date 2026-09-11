import { Badge, Button } from '@/components/ui';
import type { ParticipantDefinition } from '@/services/workspace/collaborationDefinitionService';
import { cn } from '@/utils/cn';
import { ChevronLeft, ChevronRight } from 'lucide-react';

export interface ParticipantBindingPanelProps {
  definitions: ParticipantDefinition[];
  /** key → 绑定的 bot id（单选）。 */
  bindings: Record<string, string>;
  activeKey: string;
  onActiveKeyChange: (key: string) => void;
  /** 保留兼容：Bot 名称在下方成员选择器的已选项中展示。 */
  botNameResolver?: (botId: string) => string | undefined;
  /** 保留兼容：解绑由下方当前角色的已选 Bot 移除入口完成。 */
  onUnbind?: (key: string) => void;
}

/** 横向角色绑定选择器：切换当前角色后，在下方 Bot 列表完成绑定或解绑。 */
export function ParticipantBindingPanel({
  definitions,
  bindings,
  activeKey,
  onActiveKeyChange,
}: ParticipantBindingPanelProps) {
  const boundCount = definitions.filter((definition) => Boolean(bindings[definition.key])).length;
  const botCount = new Set(Object.values(bindings).filter(Boolean)).size;
  const currentIndex = Math.max(
    0,
    definitions.findIndex((definition) => definition.key === activeKey),
  );

  const move = (offset: number) => {
    const target = definitions[currentIndex + offset];
    if (target) onActiveKeyChange(target.key);
  };

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background">
      <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2.5">
        <span className="text-xs font-semibold text-foreground">角色绑定</span>
        <span className="rounded-full bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary">
          已绑定 {boundCount} / {definitions.length} 个角色，共 {botCount} 个 Bot
        </span>
      </div>
      <div className="flex items-stretch gap-2 p-3">
        <Button
          variant="secondary"
          size="icon"
          aria-label="上一个角色"
          disabled={currentIndex <= 0}
          className="size-10 shrink-0"
          onClick={() => move(-1)}
        >
          <ChevronLeft className="size-4" aria-hidden />
        </Button>
        <div data-testid="role-binding-strip" className="app-scrollbar flex min-w-0 flex-1 gap-2 overflow-x-auto">
          {definitions.map((definition) => {
            const active = definition.key === activeKey;
            const bound = Boolean(bindings[definition.key]);
            const label = definition.displayName || definition.key;
            return (
              <Button
                key={definition.key}
                variant="ghost"
                size="sm"
                aria-label={`选择角色 ${label}`}
                aria-pressed={active}
                className={cn(
                  'h-10 min-w-[150px] flex-1 justify-between rounded-lg border px-3 text-left',
                  active
                    ? 'border-primary bg-primary/10 text-primary hover:bg-primary/10'
                    : 'border-border bg-background text-foreground hover:border-primary/30 hover:bg-primary/10',
                )}
                onClick={() => onActiveKeyChange(definition.key)}
              >
                <span className="min-w-0 truncate text-xs font-semibold">
                  {label}
                  {definition.required ? <span className="text-destructive"> *</span> : null}
                </span>
                <Badge
                  className="shrink-0 text-[10px]"
                  tone={bound ? 'success' : definition.required ? 'warning' : 'neutral'}
                >
                  {bound ? '已绑定' : definition.required ? '需绑定' : '可选'}
                </Badge>
              </Button>
            );
          })}
        </div>
        <Button
          variant="secondary"
          size="icon"
          aria-label="下一个角色"
          disabled={currentIndex >= definitions.length - 1}
          className="size-10 shrink-0"
          onClick={() => move(1)}
        >
          <ChevronRight className="size-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}

export default ParticipantBindingPanel;

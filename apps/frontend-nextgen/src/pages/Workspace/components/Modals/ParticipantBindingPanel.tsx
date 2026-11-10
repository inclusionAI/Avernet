import { Badge, Button } from '@/components/ui';
import type { ParticipantDefinition } from '@/services/workspace/collaborationDefinitionService';
import { cn } from '@/utils/cn';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useEffect, useRef } from 'react';

export interface ParticipantBindingPanelProps {
  definitions: ParticipantDefinition[];
  /** key → 绑定的 bot id（单选）。 */
  bindings: Record<string, string>;
  activeKey: string;
  onActiveKeyChange: (key: string) => void;
  /** 解析角色当前绑定的 Bot 名称。 */
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
  botNameResolver,
}: ParticipantBindingPanelProps) {
  const activeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { activeRef.current?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }); }, [activeKey]);
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
    <div data-role-binding-panel className="min-w-0 border-t border-border pt-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-foreground">角色绑定</span>
        <span className="text-xs text-muted-foreground">
          已绑定 {boundCount} / {definitions.length} 个角色，共 {botCount} 个 Bot
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="icon"
          aria-label="上一个角色"
          disabled={currentIndex <= 0}
          className="hidden size-7 shrink-0 rounded-full border-0 bg-muted/60 sm:inline-flex"
          onClick={() => move(-1)}
        >
          <ChevronLeft className="size-4" aria-hidden />
        </Button>
        <div data-testid="role-binding-strip" className="app-scrollbar flex min-w-0 flex-1 gap-2 overflow-x-auto p-px">
          {definitions.map((definition) => {
            const active = definition.key === activeKey;
            const bound = Boolean(bindings[definition.key]);
            const label = definition.displayName || definition.key;
            return (
              <Button
                key={definition.key}
                ref={active ? activeRef : undefined}
                variant="ghost"
                size="sm"
                aria-label={`选择角色 ${label}`}
                aria-pressed={active}
                className={cn(
                  'h-16 w-40 flex-none flex-col items-stretch justify-center gap-1 rounded-lg border px-3 py-2 text-left',
                  active
                    ? 'border-primary bg-primary/5 text-primary ring-1 ring-primary/15 hover:bg-primary/5'
                    : 'border-border bg-background text-foreground hover:border-primary/40 hover:bg-muted/40',
                )}
                onClick={() => onActiveKeyChange(definition.key)}
              >
                <span className="flex min-w-0 items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-xs font-medium">
                    {label}
                    {definition.required ? <span className="text-destructive"> *</span> : null}
                  </span>
                  <Badge
                    className="shrink-0 border-0 bg-transparent px-0 text-[10px]"
                    tone={bound ? 'success' : definition.required ? 'warning' : 'neutral'}
                  >
                    {bound ? '已绑定' : definition.required ? '需绑定' : '可选'}
                  </Badge>
                </span>
                <span className="truncate text-xs font-normal text-muted-foreground">
                  {bound ? botNameResolver?.(bindings[definition.key]) || '已选择 Bot' : '选择执行 Bot'}
                </span>
              </Button>
            );
          })}
        </div>
        <Button
          variant="secondary"
          size="icon"
          aria-label="下一个角色"
          disabled={currentIndex >= definitions.length - 1}
          className="hidden size-7 shrink-0 rounded-full border-0 bg-muted/60 sm:inline-flex"
          onClick={() => move(1)}
        >
          <ChevronRight className="size-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}

export default ParticipantBindingPanel;

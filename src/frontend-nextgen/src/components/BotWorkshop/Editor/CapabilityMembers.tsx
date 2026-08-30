import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { FileCheck, FileText, Plus, Trash2, User } from 'lucide-react';

interface CapabilityMembersProps {
  kind: 'skill' | 'mcp';
  items: Array<BotEditorSkill | BotEditorMcp>;
  editable: boolean;
  identities?: Record<string, 'caller' | 'owner'>;
  onIdentity?: (id: string) => void;
  onAdd: () => void;
  onRemove: (id: string) => Promise<void>;
}

export function CapabilityMembers({
  kind,
  items,
  editable,
  identities = {},
  onIdentity,
  onAdd,
  onRemove,
}: CapabilityMembersProps) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold text-muted-foreground">{kind === 'skill' ? 'Skills' : 'MCPs'}</span>
        <Button
          variant="ghost"
          size="sm"
          className="text-primary"
          disabled={!editable}
          leftIcon={<Plus className="size-3" />}
          onClick={onAdd}
        >
          添加
        </Button>
      </div>
      {items.length ? (
        items.map((item) => {
          const id = 'serverCode' in item ? item.serverCode : item.id;
          const identity = identities[id] ?? 'owner';
          return (
            <div
              key={id}
              className="mb-1 flex items-center gap-2 rounded-md border border-border bg-card px-2.5 py-1.5"
            >
              {kind === 'skill' ? (
                <FileText className="size-3.5 text-primary" />
              ) : (
                <FileCheck className="size-3.5 text-info" />
              )}
              <span className="min-w-0 flex-1 truncate text-xs">{item.name}</span>
              {'version' in item && item.version ? <Badge>{item.version}</Badge> : null}
              {kind === 'mcp' ? (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`切换${item.name}调用身份`}
                        leftIcon={
                          identity === 'caller' ? <FileCheck className="size-3.5" /> : <User className="size-3.5" />
                        }
                        onClick={() => onIdentity?.(id)}
                      />
                    </TooltipTrigger>
                    <TooltipContent>当前以 {identity} 身份调用；本地 Mock，待 OpenAPI 持久化</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              ) : null}
              <Button
                variant="ghost"
                size="icon"
                disabled={!editable}
                aria-label={`移除${item.name}`}
                leftIcon={<Trash2 className="size-3.5" />}
                onClick={() => void onRemove(id)}
              />
            </div>
          );
        })
      ) : (
        <div className="rounded-md border border-dashed border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
          未添加 {kind === 'skill' ? 'Skill' : 'MCP'}
        </div>
      )}
    </div>
  );
}

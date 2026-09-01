import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotEditorCli, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { FileCheck, FileText, Loader2, Plus, Terminal, Trash2, User } from 'lucide-react';
import { useState } from 'react';

interface CapabilityMembersProps {
  kind: 'skill' | 'mcp' | 'cli';
  items: Array<BotEditorSkill | BotEditorMcp | BotEditorCli>;
  editable: boolean;
  identities?: Record<string, 'caller' | 'owner'>;
  onIdentity?: (id: string) => void;
  onAdd?: () => void;
  onRemove?: (id: string) => Promise<void>;
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
  const [removingId, setRemovingId] = useState<string>();
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold text-muted-foreground">
          {kind === 'skill' ? 'Skills' : kind === 'mcp' ? 'MCPs' : 'CLIs'}
        </span>
        {kind !== 'cli' && onAdd ? (
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
        ) : null}
      </div>
      {items.length ? (
        items.map((item) => {
          const id = 'serverCode' in item ? item.serverCode : 'code' in item ? item.code : item.id;
          const identity = identities[id] ?? 'owner';
          return (
            <div
              key={id}
              className="mb-1 flex items-center gap-2 rounded-md border border-border bg-card px-2.5 py-1.5"
            >
              {kind === 'skill' ? (
                <FileText className="size-3.5 text-primary" />
              ) : kind === 'mcp' ? (
                <FileCheck className="size-3.5 text-info" />
              ) : (
                <Terminal className="size-3.5 text-success" />
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
              {kind !== 'cli' ? (
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={!editable || Boolean(removingId)}
                  aria-label={`移除${item.name}`}
                  leftIcon={
                    removingId === id ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />
                  }
                  onClick={() => {
                    setRemovingId(id);
                    void Promise.resolve(onRemove?.(id))
                      .catch(() => undefined)
                      .finally(() => setRemovingId(undefined));
                  }}
                />
              ) : null}
            </div>
          );
        })
      ) : (
        <div className="rounded-md border border-dashed border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
          未添加 {kind === 'skill' ? 'Skill' : kind === 'mcp' ? 'MCP' : 'CLI'}
        </div>
      )}
    </div>
  );
}

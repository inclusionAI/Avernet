import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { BotEditorCli, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { FileCheck, FileText, Loader2, Plus, Terminal, Trash2, User } from 'lucide-react';
import { useState } from 'react';

interface CapabilityMembersProps {
  kind: 'skill' | 'mcp' | 'cli';
  items: Array<BotEditorSkill | BotEditorMcp | BotEditorCli>;
  editable: boolean;
  identities?: Record<string, 'caller' | 'owner'>;
  identityEditable?: boolean;
  updatingIdentityId?: string;
  onIdentity?: (id: string, identity: 'caller' | 'owner') => Promise<void>;
  onAdd?: () => void;
  onRemove?: (id: string) => Promise<void>;
}

export function CapabilityMembers({
  kind,
  items,
  editable,
  identities = {},
  onIdentity,
  identityEditable = false,
  updatingIdentityId,
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
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={!editable || !identityEditable || updatingIdentityId === id}
                      aria-label={`修改${item.name}调用身份`}
                      leftIcon={
                        updatingIdentityId === id ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : identity === 'caller' ? (
                          <FileCheck className="size-3.5" />
                        ) : (
                          <User className="size-3.5" />
                        )
                      }
                    >
                      {identity === 'caller' ? 'Caller' : 'Owner'}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent align="end" className="w-72 space-y-2 p-2">
                    <p className="m-0 px-2 py-1 text-xs text-muted-foreground">
                      选择该 MCP 执行时使用的身份。修改仅作用于当前草稿。
                    </p>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto w-full items-start justify-start py-2 text-left"
                      leftIcon={<User className="mt-0.5 size-3.5" />}
                      disabled={identity === 'owner'}
                      onClick={() => void onIdentity?.(id, 'owner')}
                    >
                      <span>
                        <span className="block">Owner 模式</span>
                        <span className="block font-normal text-muted-foreground">使用 Bot 所有者身份调用</span>
                      </span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto w-full items-start justify-start py-2 text-left"
                      leftIcon={<FileCheck className="mt-0.5 size-3.5" />}
                      disabled={identity === 'caller'}
                      onClick={() => void onIdentity?.(id, 'caller')}
                    >
                      <span>
                        <span className="block">Caller 模式</span>
                        <span className="block font-normal text-muted-foreground">使用当前对话用户身份调用</span>
                      </span>
                    </Button>
                  </PopoverContent>
                </Popover>
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

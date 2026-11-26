import { Badge } from '@/components/ui/Badge';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { spawnName, type AgentRefDraft } from '@/domain/agentswarm/draft';

/**
 * One roster entry.
 *
 * The origin is shown, not implied: a `new` agent is written here and saved to
 * the definition library at submit, while a `library` one is a reference the
 * run resolves later. They submit the same way, so without the badge the two
 * would look identical and the user could not tell which is about to be created.
 */
export function AgentRefCard({
  agentRef,
  index,
  onChange,
  onRemove,
}: {
  agentRef: AgentRefDraft;
  index: number;
  onChange: (patch: Partial<AgentRefDraft>) => void;
  onRemove: () => void;
}) {
  const isNew = agentRef.origin === 'new';
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-divider px-3 py-2">
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">
            {agentRef.body.name || '（未命名定义）'}
          </span>
          <Badge tone={isNew ? 'primary' : 'neutral'}>{isNew ? '新建' : '来自定义库'}</Badge>
        </span>
        <span className="block truncate text-xs text-muted-foreground">
          {agentRef.body.role || '（无职责说明）'}
          {isNew ? ' · 提交时保存到定义库' : ''}
        </span>
      </span>
      <span className="flex w-44 shrink-0 flex-col gap-1">
        <label htmlFor={`agent-ref-alias-${agentRef.key}`} className="text-xs text-muted-foreground">
          本次运行的名称
        </label>
        <Input
          id={`agent-ref-alias-${agentRef.key}`}
          value={agentRef.alias}
          placeholder={spawnName(agentRef) || '（同定义名）'}
          onChange={(event) => onChange({ alias: event.target.value })}
        />
      </span>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-label={`移除第 ${index + 1} 个 Agent ${agentRef.body.name}`}
        onClick={onRemove}
      >
        移除
      </Button>
    </div>
  );
}
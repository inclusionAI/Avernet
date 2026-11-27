import { useMemo, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Checkbox } from '@/components/ui/Checkbox';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Segmented } from '@/components/ui/Segmented';
import { createLibraryAgentRef, type AgentRefDraft } from '@/domain/agentswarm/draft';
import { useDefinitions } from '@/hooks/useDefinitions';

/** PRD §3: the three pickers share one modal, chosen by a source tab. */
export type DefinitionSource = 'library' | 'space' | 'market';

const SOURCES: Array<{ value: DefinitionSource; label: string; enabled: boolean; note?: string }> = [
  { value: 'library', label: '我的 Agent 库', enabled: true },
  // Listed but disabled: the PRD requires the entry points to exist, and this
  // milestone does not wire the spaces or expert-market catalogues yet.
  { value: 'space', label: '空间 Bot', enabled: false, note: '本期暂未开放' },
  { value: 'market', label: '专家市场', enabled: false, note: '本期暂未开放' },
];

/**
 * Picks definitions from the stored library into the roster.
 *
 * Multi-select is the point: the roster is a pool, and several definitions are
 * usually added at once. The same definition may be picked more than once —
 * the caller assigns distinct aliases, which is what the gateway requires.
 */
export function DefinitionPicker({
  open,
  tenant,
  initialSource = 'library',
  onOpenChange,
  onAdd,
}: {
  open: boolean;
  tenant: string | undefined;
  /** Which source the modal opens on; the user can switch between them. */
  initialSource?: DefinitionSource;
  onOpenChange: (open: boolean) => void;
  onAdd: (refs: AgentRefDraft[]) => void;
}) {
  const [source, setSource] = useState<DefinitionSource>(initialSource);
  const [keyword, setKeyword] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const { definitions, total, loading, error } = useDefinitions(tenant, {
    keyword, page,
  });

  const byId = useMemo(
    () => new Map(definitions.map((definition) => [definition.definition_id, definition])),
    [definitions],
  );

  const toggle = (definitionId: string) =>
    setSelected((current) =>
      current.includes(definitionId)
        ? current.filter((id) => id !== definitionId)
        : [...current, definitionId],
    );

  const confirm = () => {
    const refs = selected
      .map((id) => byId.get(id))
      .filter((definition): definition is NonNullable<typeof definition> => Boolean(definition))
      .map((definition) => createLibraryAgentRef(definition));
    if (refs.length) onAdd(refs);
    setSelected([]);
    onOpenChange(false);
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      {/* Wide enough for a name, a role and its page: at `md` the rows wrapped
          onto three lines each and the list read as a column of fragments. */}
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>我的 Agent 库</ModalTitle>
        </ModalHeader>
        <div className="flex flex-col gap-3">
          <Segmented
            aria-label="定义来源"
            value={source}
            onChange={(next) => {
              setSource(next as DefinitionSource);
              setSelected([]);
              setKeyword('');
            }}
            options={SOURCES.map((item) => ({
              label: item.label,
              value: item.value,
              // Segmented disables a tab through this, not through a boolean:
              // passing only `label`/`value` left the unimplemented sources
              // clickable, and switching to one still showed the library.
              disabledReason: item.enabled ? undefined : item.note,
            }))}
          />

          {source !== 'library' ? (
            <p role="status" className="m-0 rounded-lg bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
              本期只接入「我的 Agent 库」，其他来源保留入口。
            </p>
          ) : null}

          {source === 'library' ? (
          <>
          <Input
            aria-label="搜索定义"
            placeholder="按名称搜索定义"
            value={keyword}
            onChange={(event) => {
              setKeyword(event.target.value);
              setPage(1);
            }}
          />
          {error ? (
            <p role="alert" className="m-0 text-xs text-destructive">
              {error}
            </p>
          ) : null}
          {/* Two columns, matching the library page's cards: a name and a role
              alone said nothing about what an agent would do, so the model and
              whether the YAML is custom decide the pick right here. */}
          {!loading && !definitions.length ? (
            <Empty title="定义库是空的" description="先在「我的 Agent 库」新建 Agent 定义，或直接空白创建 Agent。" />
          ) : (
            <ul className="m-0 grid max-h-96 list-none grid-cols-1 gap-2 overflow-y-auto p-0 sm:grid-cols-2">
              {definitions.map((definition) => {
                const checked = selected.includes(definition.definition_id);
                return (
                  <li key={definition.definition_id}>
                    <label
                      className={[
                        'flex h-full cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2.5 transition-colors',
                        // Selection is carried by the border and a tint, not by a
                        // ring: the ring added a second outline that fought the
                        // card's own border at this size.
                        checked
                          ? 'border-primary bg-primary/5'
                          : 'border-divider hover:border-border hover:bg-accent',
                      ].join(' ')}
                    >
                      <Checkbox
                        className="shrink-0"
                        checked={checked}
                        onCheckedChange={() => toggle(definition.definition_id)}
                        aria-label={`选择定义 ${definition.name}`}
                      />
                      <span className="flex min-w-0 flex-1 flex-col gap-1">
                        <span
                          className="truncate text-sm font-medium text-foreground"
                          title={definition.name}
                        >
                          {definition.name}
                        </span>
                        <span
                          className="line-clamp-2 text-xs leading-4 text-muted-foreground"
                          title={definition.role || undefined}
                        >
                          {definition.role.trim() || '未填写职责'}
                        </span>
                        <span className="flex flex-wrap items-center gap-1">
                          <Badge tone={definition.model ? 'primary' : 'outline'}>
                            {definition.model || '跟随默认'}
                          </Badge>
                          <Badge tone={definition.manifest_document.trim() ? 'purple' : 'neutral'}>
                            {definition.manifest_document.trim() ? '自定义 YAML' : '合成 SOUL.md'}
                          </Badge>
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
          {!loading && total > 0 ? (
            <Pagination
              current={page}
              pageSize={10}
              total={total}
              onChange={setPage}
              layout="between"
            />
          ) : null}
          </>
          ) : null}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button type="button" disabled={!selected.length} onClick={confirm}>
            添加 {selected.length ? `(${selected.length})` : ''}
          </Button>
        </div>
      </ModalContent>
    </Modal>
  );
}
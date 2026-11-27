import { useMemo, useState } from 'react';
import { Button } from '@/components/ui/Button';
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
      <ModalContent size="md">
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
            options={SOURCES.map((item) => ({ label: item.label, value: item.value }))}
          />

          {source !== 'library' ? (
            <p role="status" className="m-0 rounded-lg bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
              {SOURCES.find((item) => item.value === source)?.note}：本期只接入「我的 Agent 库」，其他来源保留入口。
            </p>
          ) : null}

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
          {!loading && !definitions.length ? (
            <Empty title="定义库是空的" description="先在「我的 Agent 库」新建 Agent 定义，或直接空白创建 Agent。" />
          ) : (
            <ul className="m-0 flex max-h-80 list-none flex-col gap-1 overflow-y-auto p-0">
              {definitions.map((definition) => {
                const checked = selected.includes(definition.definition_id);
                return (
                  <li key={definition.definition_id}>
                    <label
                      className={[
                        'flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2',
                        checked ? 'border-primary bg-primary/5' : 'border-divider hover:bg-accent',
                      ].join(' ')}
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => toggle(definition.definition_id)}
                        aria-label={`选择定义 ${definition.name}`}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{definition.name}</span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {definition.role || '（无职责说明）'}
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
import { Checkbox } from '@/components/ui/Checkbox';
import type {
  TodoExecutor,
  TodoExecutorOption,
  TodoOwner,
  TodoSourceDraft,
  TodoSourceWorkItem,
} from '@/domain/todos/models';
import { Loader2 } from 'lucide-react';

function firstMetadataValue(metadata: Record<string, string> | undefined, keys: string[]): string {
  if (!metadata) return '—';
  for (const key of keys) {
    const value = metadata[key]?.trim();
    if (value) return value;
  }
  return '—';
}

function handlerName(draft?: TodoSourceDraft): string {
  return firstMetadataValue(draft?.metadata, [
    'processor_name',
    'processor',
    'assignee_name',
    'assignee',
    'handler_name',
    'handler',
    'owner_name',
  ]);
}

function priorityName(draft?: TodoSourceDraft): string {
  return firstMetadataValue(draft?.metadata, ['priority_name', 'priority']);
}

function workItemCategory(category: TodoSourceWorkItem['category']): string {
  if (category === 'req') return '需求';
  if (category === 'bug') return '缺陷';
  return '任务';
}

interface TodoDimaWorkItemTableProps {
  items: TodoSourceWorkItem[];
  selectedIds: string[];
  drafts: Record<string, TodoSourceDraft>;
  draftLoadingIds: string[];
  loading: boolean;
  projectId: string;
  allSelected: boolean;
  someSelected: boolean;
  executor?: TodoExecutor;
  executorOptions: TodoExecutorOption[];
  owner?: TodoOwner;
  onToggleAll: (checked: boolean) => void;
  onToggleItem: (item: TodoSourceWorkItem, checked: boolean) => void;
}

export function TodoDimaWorkItemTable({
  items,
  selectedIds,
  drafts,
  draftLoadingIds,
  loading,
  projectId,
  allSelected,
  someSelected,
  executor,
  executorOptions,
  owner,
  onToggleAll,
  onToggleItem,
}: TodoDimaWorkItemTableProps) {
  const selectedExecutor = executorOptions.find((option) => option.type === executor?.type && option.id === executor.id);

  return (
    <section className="min-h-0 min-w-0 flex-1">
      <div className="h-full min-h-[300px] overflow-auto rounded-xl border border-border">
        <table className="w-full min-w-[860px] border-collapse text-left text-xs">
          <thead className="sticky top-0 z-10 bg-muted/90 text-muted-foreground backdrop-blur">
            <tr>
              <th className="w-12 px-3 py-3">
                <Checkbox
                  checked={allSelected}
                  indeterminate={!allSelected && someSelected}
                  disabled={items.length === 0}
                  onCheckedChange={onToggleAll}
                  aria-label="选择当前结果"
                />
              </th>
              <th className="px-3 py-3 font-medium">标题</th>
              <th className="w-24 px-3 py-3 font-medium">状态</th>
              <th className="w-28 px-3 py-3 font-medium">处理人</th>
              <th className="w-24 px-3 py-3 font-medium">优先级</th>
              <th className="w-32 px-3 py-3 font-medium">执行方</th>
              <th className="w-28 px-3 py-3 font-medium">负责人</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const draft = drafts[item.id];
              return (
                <tr key={item.id} className="border-t border-divider hover:bg-muted/20">
                  <td className="px-3 py-3 align-top">
                    <Checkbox
                      checked={selectedIds.includes(item.id)}
                      onCheckedChange={(checked) => onToggleItem(item, checked)}
                      aria-label={`选择 ${item.title}`}
                    />
                  </td>
                  <td className="px-3 py-3 align-top">
                    <p className="m-0 max-w-[360px] truncate font-medium text-foreground">{item.title}</p>
                    <p className="m-0 mt-1 text-[11px] text-muted-foreground">{workItemCategory(item.category)} · {item.id}</p>
                  </td>
                  <td className="px-3 py-3 align-top text-muted-foreground">{item.status || '—'}</td>
                  <td className="px-3 py-3 align-top text-muted-foreground">
                    {draftLoadingIds.includes(item.id) ? <Loader2 className="size-3.5 animate-spin" /> : handlerName(draft)}
                  </td>
                  <td className="px-3 py-3 align-top text-muted-foreground">{priorityName(draft)}</td>
                  <td className="px-3 py-3 align-top text-muted-foreground">{selectedExecutor?.name || '—'}</td>
                  <td className="px-3 py-3 align-top text-muted-foreground">{owner?.userName || '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> 加载 Dima 工作项中…
          </div>
        ) : null}
        {!loading && projectId && items.length === 0 ? (
          <div className="py-16 text-center text-sm text-muted-foreground">没有符合条件的 Dima 工作项</div>
        ) : null}
        {!loading && !projectId ? (
          <div className="py-16 text-center text-sm text-muted-foreground">请先选择 Dima 空间或项目</div>
        ) : null}
      </div>
    </section>
  );
}

import { Button } from '@/components/ui/Button';
import type {
  TodoExecutor,
  TodoExecutorOption,
  TodoOwner,
  TodoSourceDraft,
  TodoSourceWorkItem,
} from '@/domain/todos/models';
import { TodoDimaWorkItemTable } from './TodoDimaWorkItemTable';

interface TodoDimaImportSelectionProps {
  items: TodoSourceWorkItem[];
  selectedIds: string[];
  drafts: Record<string, TodoSourceDraft>;
  draftLoadingIds: string[];
  loading: boolean;
  projectId: string;
  projectCount: number;
  projectsLoading: boolean;
  error: string;
  maxImportCount: number;
  allSelected: boolean;
  someSelected: boolean;
  executor?: TodoExecutor;
  executorOptions: TodoExecutorOption[];
  owner?: TodoOwner;
  onReloadProjects: () => void;
  onToggleAll: (checked: boolean) => void;
  onToggleItem: (item: TodoSourceWorkItem, checked: boolean) => void;
}

export function TodoDimaImportSelection({
  items,
  selectedIds,
  drafts,
  draftLoadingIds,
  loading,
  projectId,
  projectCount,
  projectsLoading,
  error,
  maxImportCount,
  allSelected,
  someSelected,
  executor,
  executorOptions,
  owner,
  onReloadProjects,
  onToggleAll,
  onToggleItem,
}: TodoDimaImportSelectionProps) {
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          已选择 <strong className="text-foreground">{selectedIds.length}</strong> / {maxImportCount}
          {projectId ? ` · 当前结果 ${items.length} 条` : ''}
        </span>
        {projectCount === 0 && !projectsLoading && error ? (
          <Button variant="outline" size="sm" onClick={onReloadProjects}>
            重新加载项目
          </Button>
        ) : null}
      </div>
      {error ? (
        <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      ) : null}
      <TodoDimaWorkItemTable
        items={items}
        selectedIds={selectedIds}
        drafts={drafts}
        draftLoadingIds={draftLoadingIds}
        loading={loading}
        projectId={projectId}
        allSelected={allSelected}
        someSelected={someSelected}
        executor={executor}
        executorOptions={executorOptions}
        owner={owner}
        onToggleAll={onToggleAll}
        onToggleItem={onToggleItem}
      />
    </>
  );
}

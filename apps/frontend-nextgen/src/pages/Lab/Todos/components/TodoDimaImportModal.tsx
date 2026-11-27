import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { notifySuccess, notifyWarning } from '@/components/ui/notify';
import type {
  DimaImportResult,
  TodoExecutor,
  TodoExecutorOption,
  TodoOwner,
  TodoSourceDraft,
  TodoSourceProject,
  TodoSourceWorkItem,
} from '@/domain/todos/models';
import {
  importTodoDimaItems,
  loadTodoSourceDraft,
  loadTodoSourceProjects,
  loadTodoSourceWorkItems,
} from '@/services/todos/todoService';
import { safeReportError } from '@/utils/errorToast';
import { Loader2, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { TodoExecutorSelect } from './TodoExecutorSelect';

interface TodoDimaImportModalProps {
  open: boolean;
  spaceId?: number;
  executorOptions: TodoExecutorOption[];
  owners: TodoOwner[];
  onOpenChange: (open: boolean) => void;
  onImported: () => void | Promise<void>;
}

const MAX_IMPORT_COUNT = 20;

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

export function TodoDimaImportModal({
  open,
  spaceId,
  executorOptions,
  owners,
  onOpenChange,
  onImported,
}: TodoDimaImportModalProps) {
  const [projects, setProjects] = useState<TodoSourceProject[]>([]);
  const [projectId, setProjectId] = useState('');
  const [workItems, setWorkItems] = useState<TodoSourceWorkItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<Record<string, TodoSourceDraft>>({});
  const [query, setQuery] = useState('');
  const [executor, setExecutor] = useState<TodoExecutor>();
  const [ownerId, setOwnerId] = useState('');
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [workItemsLoading, setWorkItemsLoading] = useState(false);
  const [draftLoadingIds, setDraftLoadingIds] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [results, setResults] = useState<DimaImportResult[]>([]);
  const generation = useRef(0);
  const draftRequests = useRef<Record<string, Promise<TodoSourceDraft | undefined>>>({});

  const resetExecutorAndOwner = () => {
    const firstExecutor = executorOptions[0];
    setExecutor(
      firstExecutor
        ? {
            type: firstExecutor.type,
            id: firstExecutor.id,
            ...(firstExecutor.revisionId ? { revisionId: firstExecutor.revisionId } : {}),
          }
        : undefined,
    );
    setOwnerId(owners[0]?.userId ?? '');
  };

  const loadProjects = async () => {
    const request = ++generation.current;
    setProjectsLoading(true);
    setError('');
    try {
      const items = await loadTodoSourceProjects();
      if (request !== generation.current) return;
      setProjects(items);
    } catch (cause) {
      if (request !== generation.current) return;
      setProjects([]);
      setError('Dima 空间与项目加载失败，请重试。');
      safeReportError(cause, { title: 'Dima 项目加载失败' });
    } finally {
      if (request === generation.current) setProjectsLoading(false);
    }
  };

  useEffect(() => {
    generation.current += 1;
    if (!open) return;
    setProjects([]);
    setProjectId('');
    setWorkItems([]);
    setSelectedIds([]);
    setDrafts({});
    setQuery('');
    setResults([]);
    setError('');
    draftRequests.current = {};
    resetExecutorAndOwner();
    void loadProjects();
  }, [open]);

  useEffect(() => {
    if (!open || executor || !executorOptions[0]) return;
    const first = executorOptions[0];
    setExecutor({ type: first.type, id: first.id, ...(first.revisionId ? { revisionId: first.revisionId } : {}) });
  }, [executor, executorOptions, open]);

  useEffect(() => {
    if (!open || ownerId || !owners[0]) return;
    setOwnerId(owners[0].userId);
  }, [open, ownerId, owners]);

  const changeProject = async (nextProjectId: string) => {
    const request = ++generation.current;
    setProjectId(nextProjectId);
    setWorkItems([]);
    setSelectedIds([]);
    setDrafts({});
    draftRequests.current = {};
    setResults([]);
    setError('');
    setWorkItemsLoading(true);
    try {
      const items = await loadTodoSourceWorkItems(nextProjectId);
      if (request !== generation.current) return;
      setWorkItems(items);
    } catch (cause) {
      if (request !== generation.current) return;
      setError('Dima 工作项加载失败，请重试。');
      safeReportError(cause, { title: 'Dima 工作项加载失败' });
    } finally {
      if (request === generation.current) setWorkItemsLoading(false);
    }
  };

  const loadDraft = async (item: TodoSourceWorkItem): Promise<TodoSourceDraft | undefined> => {
    if (drafts[item.id]) return drafts[item.id];
    const pending = draftRequests.current[item.id];
    if (pending) return pending;
    const requestGeneration = generation.current;
    const request = (async () => {
      if (requestGeneration === generation.current) {
        setDraftLoadingIds((current) => (current.includes(item.id) ? current : [...current, item.id]));
      }
      try {
        const draft = await loadTodoSourceDraft(item.projectId, item.id);
        if (requestGeneration === generation.current) {
          setDrafts((current) => ({ ...current, [item.id]: draft }));
        }
        return draft;
      } catch (cause) {
        if (requestGeneration === generation.current) {
          safeReportError(cause, { title: `Dima 工作项 ${item.id} 详情加载失败` });
        }
        return undefined;
      } finally {
        if (draftRequests.current[item.id] === request) delete draftRequests.current[item.id];
        if (requestGeneration === generation.current) {
          setDraftLoadingIds((current) => current.filter((id) => id !== item.id));
        }
      }
    })();
    draftRequests.current[item.id] = request;
    return request;
  };

  const toggleItem = (item: TodoSourceWorkItem, checked: boolean) => {
    if (checked && !selectedIds.includes(item.id)) {
      if (selectedIds.length >= MAX_IMPORT_COUNT) {
        notifyWarning(`单次最多导入 ${MAX_IMPORT_COUNT} 个 Dima 工作项`);
        return;
      }
      setSelectedIds((current) => [...current, item.id]);
      void loadDraft(item);
      return;
    }
    if (!checked) setSelectedIds((current) => current.filter((id) => id !== item.id));
  };

  const visibleItems = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    if (!keyword) return workItems;
    return workItems.filter(
      (item) => item.title.toLocaleLowerCase().includes(keyword) || item.id.toLocaleLowerCase().includes(keyword),
    );
  }, [query, workItems]);

  const allVisibleSelected = visibleItems.length > 0 && visibleItems.every((item) => selectedIds.includes(item.id));
  const someVisibleSelected = visibleItems.some((item) => selectedIds.includes(item.id));
  const toggleVisibleItems = (checked: boolean) => {
    if (!checked) {
      const visibleIds = new Set(visibleItems.map((item) => item.id));
      setSelectedIds((current) => current.filter((id) => !visibleIds.has(id)));
      return;
    }
    const additions = visibleItems.filter((item) => !selectedIds.includes(item.id));
    const remaining = Math.max(0, MAX_IMPORT_COUNT - selectedIds.length);
    const accepted = additions.slice(0, remaining);
    if (accepted.length < additions.length) notifyWarning(`单次最多导入 ${MAX_IMPORT_COUNT} 个 Dima 工作项`);
    setSelectedIds((current) => [...current, ...accepted.map((item) => item.id)]);
    accepted.forEach((item) => void loadDraft(item));
  };

  const selectedOwner = owners.find((owner) => owner.userId === ownerId);
  const submit = async () => {
    if (!spaceId || !executor || !selectedOwner || selectedIds.length === 0) return;
    setSubmitting(true);
    setError('');
    setResults([]);
    try {
      const selectedItems = selectedIds
        .map((id) => workItems.find((item) => item.id === id))
        .filter((item): item is TodoSourceWorkItem => Boolean(item));
      const resolvedDrafts = await Promise.all(selectedItems.map((item) => loadDraft(item)));
      if (resolvedDrafts.some((draft) => !draft)) {
        setError('部分 Dima 工作项详情加载失败，请重试后再导入。');
        return;
      }
      const imported = await importTodoDimaItems({
        spaceId,
        executor,
        owner: selectedOwner,
        items: resolvedDrafts.map((draft) => ({
          title: draft!.title,
          description: draft!.description,
          source: {
            type: 'dima',
            containerId: draft!.projectId,
            itemId: draft!.workItemId,
            ...(draft!.sourceUrl ? { url: draft!.sourceUrl } : {}),
            ...(draft!.sourceUpdatedAt ? { updatedAt: draft!.sourceUpdatedAt } : {}),
            metadata: draft!.metadata,
          },
        })),
      });
      setResults(imported);
      const importedCount = imported.filter((item) => item.status === 'imported').length;
      const duplicateCount = imported.filter((item) => item.status === 'duplicate').length;
      const failedCount = imported.filter((item) => item.status === 'failed').length;
      if (importedCount > 0) {
        notifySuccess(`已导入 ${importedCount} 个 Dima 工作项`);
        await onImported();
      }
      if (duplicateCount > 0 || failedCount > 0) {
        notifyWarning(`重复 ${duplicateCount} 个，失败 ${failedCount} 个`);
      }
    } catch (cause) {
      setError('Dima 工作项导入失败，请重试。');
      safeReportError(cause, { title: 'Dima 导入失败' });
    } finally {
      setSubmitting(false);
    }
  };

  const importedCount = results.filter((item) => item.status === 'imported').length;
  const duplicateCount = results.filter((item) => item.status === 'duplicate').length;
  const failedCount = results.filter((item) => item.status === 'failed').length;

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="full" className="flex h-[80vh] max-w-[920px] flex-col" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle>从 Dima 导入</ModalTitle>
          <ModalDescription>
            支持搜索、按项目筛选、多选批量导入为待办。单次最多 {MAX_IMPORT_COUNT} 个。
          </ModalDescription>
        </ModalHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="grid gap-3 rounded-xl border border-border bg-muted/15 p-4 md:grid-cols-2 xl:grid-cols-[200px_minmax(180px,1fr)_220px_180px]">
            <label className="space-y-1.5 text-xs font-medium text-foreground">
              <span className="block">Dima 空间 / 项目</span>
              <Select value={projectId || undefined} onValueChange={(value) => void changeProject(value)}>
                <SelectTrigger aria-label="选择 Dima 空间或项目">
                  <SelectValue placeholder={projectsLoading ? '加载中…' : '请选择'} />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((project) => (
                    <SelectItem key={project.id} value={project.id}>
                      {project.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="space-y-1.5 text-xs font-medium text-foreground">
              <span className="block">搜索</span>
              <span className="relative block">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="标题 / 工作项 ID"
                  aria-label="搜索 Dima 工作项"
                  className="pl-9"
                />
              </span>
            </label>
            <label className="space-y-1.5 text-xs font-medium text-foreground">
              <span className="block">本批次执行方</span>
              <TodoExecutorSelect options={executorOptions} value={executor} onChange={setExecutor} />
            </label>
            <label className="space-y-1.5 text-xs font-medium text-foreground">
              <span className="block">本批次负责人</span>
              <Select value={ownerId || undefined} onValueChange={setOwnerId} disabled={owners.length === 0}>
                <SelectTrigger aria-label="选择导入待办负责人">
                  <SelectValue placeholder="请选择负责人" />
                </SelectTrigger>
                <SelectContent>
                  {owners.map((owner) => (
                    <SelectItem key={owner.userId} value={owner.userId}>
                      {owner.userName}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>
              已选择 <strong className="text-foreground">{selectedIds.length}</strong> / {MAX_IMPORT_COUNT}
              {projectId ? ` · 当前结果 ${visibleItems.length} 条` : ''}
            </span>
            {projects.length === 0 && !projectsLoading && error ? (
              <Button variant="outline" size="sm" onClick={() => void loadProjects()}>
                重新加载项目
              </Button>
            ) : null}
          </div>

          {error ? (
            <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          ) : null}

          <section className="min-h-0 min-w-0 flex-1">
            <div className="h-full min-h-[300px] overflow-auto rounded-xl border border-border">
              <table className="w-full min-w-[860px] border-collapse text-left text-xs">
                <thead className="sticky top-0 z-10 bg-muted/90 text-muted-foreground backdrop-blur">
                  <tr>
                    <th className="w-12 px-3 py-3">
                      <Checkbox
                        checked={allVisibleSelected}
                        indeterminate={!allVisibleSelected && someVisibleSelected}
                        disabled={visibleItems.length === 0}
                        onCheckedChange={toggleVisibleItems}
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
                  {visibleItems.map((item) => {
                    const checked = selectedIds.includes(item.id);
                    const draft = drafts[item.id];
                    const loadingDraft = draftLoadingIds.includes(item.id);
                    const selectedExecutor = executorOptions.find(
                      (option) => option.type === executor?.type && option.id === executor.id,
                    );
                    return (
                      <tr key={item.id} className="border-t border-divider hover:bg-muted/20">
                        <td className="px-3 py-3 align-top">
                          <Checkbox
                            checked={checked}
                            onCheckedChange={(next) => toggleItem(item, next)}
                            aria-label={`选择 ${item.title}`}
                          />
                        </td>
                        <td className="px-3 py-3 align-top">
                          <p className="m-0 max-w-[360px] truncate font-medium text-foreground">{item.title}</p>
                          <p className="m-0 mt-1 text-[11px] text-muted-foreground">
                            {workItemCategory(item.category)} · {item.id}
                          </p>
                        </td>
                        <td className="px-3 py-3 align-top text-muted-foreground">{item.status || '—'}</td>
                        <td className="px-3 py-3 align-top text-muted-foreground">
                          {loadingDraft ? <Loader2 className="size-3.5 animate-spin" /> : handlerName(draft)}
                        </td>
                        <td className="px-3 py-3 align-top text-muted-foreground">{priorityName(draft)}</td>
                        <td className="px-3 py-3 align-top text-muted-foreground">{selectedExecutor?.name || '—'}</td>
                        <td className="px-3 py-3 align-top text-muted-foreground">{selectedOwner?.userName || '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {workItemsLoading ? (
                <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> 加载 Dima 工作项中…
                </div>
              ) : null}
              {!workItemsLoading && projectId && visibleItems.length === 0 ? (
                <div className="py-16 text-center text-sm text-muted-foreground">没有符合条件的 Dima 工作项</div>
              ) : null}
              {!workItemsLoading && !projectId ? (
                <div className="py-16 text-center text-sm text-muted-foreground">请先选择 Dima 空间或项目</div>
              ) : null}
            </div>
          </section>
        </div>

        {results.length > 0 ? (
          <div className="flex flex-wrap gap-4 rounded-xl border border-border bg-muted/15 px-4 py-3 text-xs">
            <span>本次导入结果</span>
            <span className="text-success">成功 {importedCount}</span>
            <span className="text-warning">重复 {duplicateCount}</span>
            <span className={failedCount ? 'text-destructive' : 'text-muted-foreground'}>失败 {failedCount}</span>
          </div>
        ) : null}

        <ModalFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
          <Button
            loading={submitting}
            disabled={!spaceId || selectedIds.length === 0 || !executor || !selectedOwner}
            onClick={() => void submit()}
          >
            导入 {selectedIds.length ? `${selectedIds.length} 个` : ''}待办
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

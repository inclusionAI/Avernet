import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { notifySuccess, notifyWarning } from '@/components/ui/notify';
import type {
  DimaImportResult,
  TodoExecutor,
  TodoExecutorOption,
  TodoOwner,
  TodoSourceProject,
  TodoSourceWorkItem,
} from '@/domain/todos/models';
import {
  importTodoDimaItems,
  loadTodoSourceProjects,
  loadTodoSourceWorkItems,
} from '@/services/todos/todoService';
import { safeReportError } from '@/utils/errorToast';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTodoDimaDrafts } from './useTodoDimaDrafts';
import { TodoDimaImportFilters } from './TodoDimaImportFilters';
import { TodoDimaImportActions } from './TodoDimaImportActions';
import { TodoDimaImportSelection } from './TodoDimaImportSelection';

interface TodoDimaImportModalProps {
  open: boolean;
  spaceId?: number;
  executorOptions: TodoExecutorOption[];
  owners: TodoOwner[];
  onOpenChange: (open: boolean) => void;
  onImported: () => void | Promise<void>;
}

const MAX_IMPORT_COUNT = 20;

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
  const [query, setQuery] = useState('');
  const [executor, setExecutor] = useState<TodoExecutor>();
  const [ownerId, setOwnerId] = useState('');
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [workItemsLoading, setWorkItemsLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [results, setResults] = useState<DimaImportResult[]>([]);
  const generation = useRef(0);
  const { drafts, draftLoadingIds, loadDraft, resetDrafts } = useTodoDimaDrafts(generation);

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
    resetDrafts();
    setQuery('');
    setResults([]);
    setError('');
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
    resetDrafts();
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
          <TodoDimaImportFilters
            projects={projects}
            projectId={projectId}
            projectsLoading={projectsLoading}
            query={query}
            executorOptions={executorOptions}
            executor={executor}
            owners={owners}
            ownerId={ownerId}
            onProjectChange={(value) => void changeProject(value)}
            onQueryChange={setQuery}
            onExecutorChange={setExecutor}
            onOwnerChange={setOwnerId}
          />

          <TodoDimaImportSelection
            items={visibleItems}
            selectedIds={selectedIds}
            drafts={drafts}
            draftLoadingIds={draftLoadingIds}
            loading={workItemsLoading}
            projectId={projectId}
            projectCount={projects.length}
            projectsLoading={projectsLoading}
            error={error}
            maxImportCount={MAX_IMPORT_COUNT}
            allSelected={allVisibleSelected}
            someSelected={someVisibleSelected}
            executor={executor}
            executorOptions={executorOptions}
            owner={selectedOwner}
            onReloadProjects={() => void loadProjects()}
            onToggleAll={toggleVisibleItems}
            onToggleItem={toggleItem}
          />
        </div>

        <TodoDimaImportActions
          resultCount={results.length}
          importedCount={importedCount}
          duplicateCount={duplicateCount}
          failedCount={failedCount}
          submitting={submitting}
          spaceId={spaceId}
          selectedCount={selectedIds.length}
          executor={executor}
          owner={selectedOwner}
          onClose={() => onOpenChange(false)}
          onSubmit={() => void submit()}
        />
      </ModalContent>
    </Modal>
  );
}

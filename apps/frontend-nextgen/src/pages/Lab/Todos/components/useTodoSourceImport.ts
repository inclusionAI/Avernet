import type {
  TodoCreateSource,
  TodoSourceDraft,
  TodoSourceProject,
  TodoSourceType,
  TodoSourceWorkItem,
} from '@/domain/todos/models';
import { loadTodoSourceDraft, loadTodoSourceProjects, loadTodoSourceWorkItems } from '@/services/todos/todoService';
import type { Dispatch, SetStateAction } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';

type SourceErrorKind = 'projects' | 'workItems' | 'draft';

interface UseTodoSourceImportOptions {
  open: boolean;
  setTitle: Dispatch<SetStateAction<string>>;
  setDescription: Dispatch<SetStateAction<string>>;
}

export function useTodoSourceImport({ open, setTitle, setDescription }: UseTodoSourceImportOptions) {
  const [sourceType, setSourceType] = useState<TodoSourceType>('manual');
  const [projects, setProjects] = useState<TodoSourceProject[]>([]);
  const [projectId, setProjectId] = useState('');
  const [workItems, setWorkItems] = useState<TodoSourceWorkItem[]>([]);
  const [workItemId, setWorkItemId] = useState('');
  const [workItemQuery, setWorkItemQuery] = useState('');
  const [loadedDraft, setLoadedDraft] = useState<TodoSourceDraft | null>(null);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [workItemsLoading, setWorkItemsLoading] = useState(false);
  const [draftLoading, setDraftLoading] = useState(false);
  const [sourceError, setSourceError] = useState('');
  const [sourceErrorKind, setSourceErrorKind] = useState<SourceErrorKind | null>(null);
  const sourceRequestGeneration = useRef(0);

  useEffect(() => {
    sourceRequestGeneration.current += 1;
    setProjectsLoading(false);
    setWorkItemsLoading(false);
    setDraftLoading(false);
    if (!open) return;
    setSourceType('manual');
    setProjects([]);
    setProjectId('');
    setWorkItems([]);
    setWorkItemId('');
    setWorkItemQuery('');
    setLoadedDraft(null);
    setSourceError('');
    setSourceErrorKind(null);
  }, [open]);

  const fetchProjects = async () => {
    const generation = ++sourceRequestGeneration.current;
    setProjectsLoading(true);
    setSourceError('');
    setSourceErrorKind(null);
    try {
      const result = await loadTodoSourceProjects();
      if (generation !== sourceRequestGeneration.current) return;
      setProjects(result);
      setProjectId((current) => (result.some((item) => item.id === current) ? current : ''));
    } catch {
      if (generation !== sourceRequestGeneration.current) return;
      setProjects([]);
      setSourceError('Dima 项目加载失败，请重试。');
      setSourceErrorKind('projects');
    } finally {
      if (generation === sourceRequestGeneration.current) setProjectsLoading(false);
    }
  };

  const fetchWorkItems = async (selectedProjectId: string) => {
    const generation = ++sourceRequestGeneration.current;
    setWorkItemsLoading(true);
    setSourceError('');
    setSourceErrorKind(null);
    try {
      const result = await loadTodoSourceWorkItems(selectedProjectId);
      if (generation !== sourceRequestGeneration.current) return;
      setWorkItems(result);
    } catch {
      if (generation !== sourceRequestGeneration.current) return;
      setWorkItems([]);
      setSourceError('Dima 工作项加载失败，请重试。');
      setSourceErrorKind('workItems');
    } finally {
      if (generation === sourceRequestGeneration.current) setWorkItemsLoading(false);
    }
  };

  const changeSourceType = (next: TodoSourceType) => {
    sourceRequestGeneration.current += 1;
    setProjectsLoading(false);
    setWorkItemsLoading(false);
    setDraftLoading(false);
    setSourceType(next);
    setSourceError('');
    setSourceErrorKind(null);
    setProjectId('');
    setWorkItems([]);
    setWorkItemId('');
    setWorkItemQuery('');
    setLoadedDraft(null);
    setTitle('');
    setDescription('');
    if (next === 'dima') void fetchProjects();
  };

  const changeProject = (nextProjectId: string) => {
    sourceRequestGeneration.current += 1;
    setProjectId(nextProjectId);
    setWorkItemId('');
    setWorkItemQuery('');
    setWorkItems([]);
    setLoadedDraft(null);
    setTitle('');
    setDescription('');
    setDraftLoading(false);
    void fetchWorkItems(nextProjectId);
  };

  const changeWorkItem = async (nextWorkItemId: string) => {
    const generation = ++sourceRequestGeneration.current;
    setWorkItemId(nextWorkItemId);
    setLoadedDraft(null);
    setTitle('');
    setDescription('');
    setDraftLoading(true);
    setSourceError('');
    setSourceErrorKind(null);
    try {
      const draft = await loadTodoSourceDraft(projectId, nextWorkItemId);
      if (generation !== sourceRequestGeneration.current) return;
      setLoadedDraft(draft);
      setTitle(draft.title);
      setDescription(draft.description);
    } catch {
      if (generation !== sourceRequestGeneration.current) return;
      setLoadedDraft(null);
      setSourceError('Dima 工作项详情加载失败，请重试。');
      setSourceErrorKind('draft');
    } finally {
      if (generation === sourceRequestGeneration.current) setDraftLoading(false);
    }
  };

  const retrySourceRequest = () => {
    if (sourceErrorKind === 'draft' && projectId && workItemId) {
      void changeWorkItem(workItemId);
      return;
    }
    if (sourceErrorKind === 'workItems' && projectId) {
      void fetchWorkItems(projectId);
      return;
    }
    void fetchProjects();
  };

  const visibleWorkItems = useMemo(() => {
    const query = workItemQuery.trim().toLocaleLowerCase();
    if (!query) return workItems;
    return workItems.filter(
      (item) => item.title.toLocaleLowerCase().includes(query) || item.id.toLocaleLowerCase().includes(query),
    );
  }, [workItemQuery, workItems]);

  const selectedWorkItem = workItems.find((item) => item.id === workItemId);
  const createSource: TodoCreateSource | undefined = loadedDraft
    ? {
        type: 'dima',
        containerId: loadedDraft.projectId,
        itemId: loadedDraft.workItemId,
        ...(loadedDraft.sourceUrl ? { url: loadedDraft.sourceUrl } : {}),
        ...(loadedDraft.sourceUpdatedAt ? { updatedAt: loadedDraft.sourceUpdatedAt } : {}),
      }
    : undefined;

  return {
    sourceType,
    projects,
    projectId,
    workItems,
    workItemId,
    workItemQuery,
    loadedDraft,
    projectsLoading,
    workItemsLoading,
    draftLoading,
    sourceError,
    visibleWorkItems,
    selectedWorkItem,
    createSource,
    setWorkItemQuery,
    changeSourceType,
    changeProject,
    changeWorkItem,
    retrySourceRequest,
  };
}

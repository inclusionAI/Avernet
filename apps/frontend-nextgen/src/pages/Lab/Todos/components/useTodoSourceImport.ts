import type {
  TodoCreateSource,
  TodoSourceDraft,
  TodoSourceProject,
  TodoSourceType,
  TodoSourceWorkItem,
} from '@/domain/todos/models';
import { loadTodoSourceDraft, loadTodoSourceProjects, loadTodoSourceWorkItems } from '@/services/todos/todoService';
import type { Dispatch, SetStateAction } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

type SourceErrorKind = 'projects' | 'workItems' | 'draft';

interface UseTodoSourceImportOptions {
  open: boolean;
  sourceType: TodoSourceType;
  setTitle: Dispatch<SetStateAction<string>>;
  setDescription: Dispatch<SetStateAction<string>>;
}

export function useTodoSourceImport({ open, sourceType, setTitle, setDescription }: UseTodoSourceImportOptions) {
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

  const fetchProjects = useCallback(async () => {
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
      setSourceError('Dima 空间与项目加载失败，请重试。');
      setSourceErrorKind('projects');
    } finally {
      if (generation === sourceRequestGeneration.current) setProjectsLoading(false);
    }
  }, []);

  useEffect(() => {
    sourceRequestGeneration.current += 1;
    setProjectsLoading(false);
    setWorkItemsLoading(false);
    setDraftLoading(false);
    setProjects([]);
    setProjectId('');
    setWorkItems([]);
    setWorkItemId('');
    setWorkItemQuery('');
    setLoadedDraft(null);
    setSourceError('');
    setSourceErrorKind(null);
    if (open && sourceType === 'dima') void fetchProjects();
  }, [fetchProjects, open, sourceType]);

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
        metadata: loadedDraft.metadata,
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
    changeProject,
    changeWorkItem,
    retrySourceRequest,
  };
}

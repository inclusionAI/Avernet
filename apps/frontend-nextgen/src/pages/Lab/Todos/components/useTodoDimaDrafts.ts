import type { TodoSourceDraft, TodoSourceWorkItem } from '@/domain/todos/models';
import { loadTodoSourceDraft } from '@/services/todos/todoService';
import { safeReportError } from '@/utils/errorToast';
import { useRef, useState, type MutableRefObject } from 'react';

export function useTodoDimaDrafts(generation: MutableRefObject<number>) {
  const [drafts, setDrafts] = useState<Record<string, TodoSourceDraft>>({});
  const [draftLoadingIds, setDraftLoadingIds] = useState<string[]>([]);
  const requests = useRef<Record<string, Promise<TodoSourceDraft | undefined>>>({});

  const resetDrafts = () => {
    setDrafts({});
    setDraftLoadingIds([]);
    requests.current = {};
  };

  const loadDraft = async (item: TodoSourceWorkItem): Promise<TodoSourceDraft | undefined> => {
    if (drafts[item.id]) return drafts[item.id];
    const pending = requests.current[item.id];
    if (pending) return pending;
    const requestGeneration = generation.current;
    let request!: Promise<TodoSourceDraft | undefined>;
    request = (async () => {
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
        if (requests.current[item.id] === request) delete requests.current[item.id];
        if (requestGeneration === generation.current) {
          setDraftLoadingIds((current) => current.filter((id) => id !== item.id));
        }
      }
    })();
    requests.current[item.id] = request;
    return request;
  };

  return { drafts, draftLoadingIds, loadDraft, resetDrafts };
}

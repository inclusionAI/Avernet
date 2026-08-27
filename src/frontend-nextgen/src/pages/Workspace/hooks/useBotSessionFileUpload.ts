/** useBotSessionFileUpload — 单聊会话文件上传 Hook(暂存 → 串行上传 → ready)。
 *  上传成功后回写 store.readyFiles,并返回 ready 文件供 chip 引用。 */
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { botSessionFileService } from '@/services/workspace/botSessionFileService';
import { SESSION_FILE_MAX_BATCH } from '@/services/workspace/sessionFileUtils';
import { useBotSessionFileStore, type UploadTask } from '@/stores/botSessionFileStore';
import { useCallback, useRef } from 'react';
import { toast } from 'sonner';

function genLocalId(): string {
  return `bf_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export interface UseBotSessionFileUploadResult {
  tasks: UploadTask[];
  isUploading: boolean;
  stageFiles: (files: File[]) => string[];
  removeTask: (localId: string) => void;
  submit: (onFileReady?: (file: BotSessionFileView) => void) => Promise<void>;
  cancelAll: () => void;
}

export function useBotSessionFileUpload(
  botId: string | null,
  sessionId: string | null,
  userId: string | null,
  ownerId?: string,
): UseBotSessionFileUploadResult {
  const store = useBotSessionFileStore();
  const uploadingRef = useRef(false);

  const stageFiles = useCallback(
    (files: File[]): string[] => {
      const remaining = SESSION_FILE_MAX_BATCH - store.uploadTasks.length;
      if (remaining <= 0) {
        toast.warning(`单次最多上传 ${SESSION_FILE_MAX_BATCH} 个文件`);
        return [];
      }
      const validateErr = botSessionFileService.validateFiles(files, SESSION_FILE_MAX_BATCH);
      if (validateErr) {
        toast.warning(validateErr);
        return [];
      }
      const tasks: UploadTask[] = [];
      for (const file of files.slice(0, remaining)) {
        tasks.push({ localId: genLocalId(), name: file.name, size: file.size, phase: 'staged', progress: 0, file });
      }
      tasks.forEach((t) => store.addTask(t));
      return tasks.map((t) => t.localId);
    },
    [store],
  );

  const removeTask = useCallback(
    (localId: string) => {
      const task = store.uploadTasks.find((t) => t.localId === localId);
      task?.abortController?.abort();
      store.removeTask(localId);
    },
    [store],
  );

  const submit = useCallback(
    async (onFileReady?: (file: BotSessionFileView) => void) => {
      if (uploadingRef.current || !botId || !sessionId || !userId) return;
      uploadingRef.current = true;
      store.setIsUploading(true);
      try {
        const queue = [...store.uploadTasks.filter((t) => t.phase === 'staged')];
        for (const task of queue) {
          const abortController = new AbortController();
          store.updateTask(task.localId, { phase: 'uploading', progress: 0, abortController });
          const res = await botSessionFileService.uploadOne(botId, sessionId, userId, task.file, {
            ownerId,
            signal: abortController.signal,
            onProgress: (loaded, total) =>
              store.updateTask(task.localId, { progress: total > 0 ? Math.round((loaded / total) * 100) : 0 }),
          });
          if (res.ok) {
            store.updateTask(task.localId, { phase: 'ready', progress: 100, resourceId: res.data.resourceId });
            store.addReadyFile(res.data);
            onFileReady?.(res.data);
          } else {
            store.updateTask(task.localId, {
              phase: 'failed',
              error: res.error.friendlyMessage,
              abortController: undefined,
            });
            toast.error(`「${task.name}」${res.error.friendlyMessage}`);
          }
        }
      } finally {
        uploadingRef.current = false;
        store.setIsUploading(false);
      }
    },
    [botId, sessionId, userId, ownerId, store],
  );

  const cancelAll = useCallback(() => {
    store.uploadTasks.forEach((t) => t.abortController?.abort());
    store.clearTasks();
  }, [store]);

  return {
    tasks: store.uploadTasks,
    isUploading: store.isUploading,
    stageFiles,
    removeTask,
    submit,
    cancelAll,
  };
}

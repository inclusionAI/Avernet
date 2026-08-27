import type { SessionFileView } from '@/services/workspace/sessionFileService';

import { sessionFileService } from '@/services/workspace/sessionFileService';
import { uploadMultipartSessionFile } from '@/services/workspace/sessionFileUpload';
import {
  genLocalFileId,
  isAllowedFileExt,
  isIllegalFileName,
  resolveUploadContentType,
  SESSION_FILE_MAX_BATCH,
  SESSION_FILE_MULTIPART_THRESHOLD,
} from '@/services/workspace/sessionFileUtils';
import { useCallback, useRef, useState } from 'react';
import { toast } from 'sonner';

export type UploadPhase = 'staged' | 'preparing' | 'uploading' | 'completing' | 'ready' | 'failed';

export interface UploadTask {
  localId: string;
  name: string;
  size: number;
  mime: string;
  phase: UploadPhase;
  progress: number;
  error?: string;
  file?: File;
  fileId?: string;
}

export interface UseSessionFileUploadResult {
  queue: UploadTask[];
  isUploading: boolean;
  stageFiles: (files: File[]) => void;
  submitStaged: () => Promise<void>;
  cancelTask: (localId: string) => Promise<void>;
  retryTask: (localId: string) => Promise<void>;
  discardAll: () => Promise<void>;
  clearCompleted: () => void;
  hasPending: () => boolean;
}

export function useSessionFileUpload(
  sessionId: string | null | undefined,
  onUploaded: (file: SessionFileView) => void,
): UseSessionFileUploadResult {
  const [queue, setQueueState] = useState<UploadTask[]>([]);
  const queueRef = useRef<UploadTask[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const uploadingRef = useRef(false);
  const abortRef = useRef<Record<string, AbortController>>({});

  /** 同步更新 state 与 ref：编排逻辑经 ref 读取队列，必须即时落库，避免 stale 队列导致重复上传。 */
  const setQueue = useCallback((updater: UploadTask[] | ((q: UploadTask[]) => UploadTask[])) => {
    const next = typeof updater === 'function' ? updater(queueRef.current) : updater;
    queueRef.current = next;
    setQueueState(next);
  }, []);

  const patch = useCallback(
    (localId: string, diff: Partial<UploadTask>) => {
      setQueue((q) => q.map((t) => (t.localId === localId ? { ...t, ...diff } : t)));
    },
    [setQueue],
  );

  const stageFiles = useCallback(
    (files: File[]) => {
      setQueue((q) => {
        const remaining = SESSION_FILE_MAX_BATCH - q.length;
        if (remaining <= 0) {
          toast.warning(`单次最多上传 ${SESSION_FILE_MAX_BATCH} 个文件`);
          return q;
        }
        const accepted: UploadTask[] = [];
        let rejected = 0;
        for (const file of files) {
          if (accepted.length >= remaining) break;
          if (!isAllowedFileExt(file.name) || isIllegalFileName(file.name)) {
            rejected++;
            continue;
          }
          accepted.push({
            localId: genLocalFileId(),
            name: file.name,
            size: file.size,
            mime: file.type || 'application/octet-stream',
            phase: 'staged',
            progress: 0,
            file,
          });
        }
        if (rejected > 0) toast.warning(`${rejected} 个文件类型不支持或文件名非法，已忽略`);
        return [...q, ...accepted];
      });
    },
    [setQueue],
  );

  const uploadOne = useCallback(
    async (task: UploadTask): Promise<boolean> => {
      if (!sessionId || !task.file) return false;
      const { localId, file, size, name } = task;
      const controller = new AbortController();
      abortRef.current[localId] = controller;

      try {
        patch(localId, { phase: 'preparing', progress: 0 });
        const mime = await resolveUploadContentType(name, task.mime, file);
        const isMultipart = size >= SESSION_FILE_MULTIPART_THRESHOLD;
        const prepared = await sessionFileService.prepareUpload(sessionId, {
          file_name: name,
          size,
          mime_type: mime,
        });
        if (prepared.ok === false) {
          patch(localId, { phase: 'failed', error: prepared.error.friendlyMessage });
          return false;
        }
        const { fileId, uploadUrl, parts } = prepared.data;
        patch(localId, { fileId, phase: 'uploading', progress: 0 });

        const onProgress = (loaded: number, total: number) => {
          const pct = total > 0 ? Math.round((loaded / total) * 100) : 0;
          patch(localId, { progress: Math.min(99, pct) });
        };

        if (isMultipart && parts && parts.length > 1) {
          await uploadMultipartSessionFile(sessionId, fileId, file, size, parts, {
            mime,
            signal: controller.signal,
            onProgress: (percent) => patch(localId, { progress: percent }),
          });
        } else if (uploadUrl) {
          await sessionFileService.uploadBytes(uploadUrl, file, { mime, signal: controller.signal, onProgress });
        } else {
          await sessionFileService.uploadContent(sessionId, fileId, file, {
            mime,
            signal: controller.signal,
            onProgress,
          });
        }

        patch(localId, { phase: 'completing', progress: 99 });
        const completed = await sessionFileService.completeUpload(sessionId, fileId);
        if (completed.ok === false) {
          patch(localId, { phase: 'failed', error: completed.error.friendlyMessage });
          return false;
        }
        patch(localId, { phase: 'ready', progress: 100 });
        onUploaded(completed.data);
        return true;
      } catch (err) {
        if ((err as { name?: string })?.name === 'AbortError') return false;
        patch(localId, { phase: 'failed', error: '上传失败，请重试' });
        toast.error(`「${name}」上传失败，请重试`);
        return false;
      } finally {
        delete abortRef.current[localId];
      }
    },
    [sessionId, onUploaded, patch],
  );

  const submitStaged = useCallback(async () => {
    if (uploadingRef.current || !sessionId) return;
    uploadingRef.current = true;
    setIsUploading(true);
    try {
      // 串行提交：从 ref 实时取 staged，避免闭包内 stale 队列导致重复上传。
      let task = queueRef.current.find((t) => t.phase === 'staged');
      while (task) {
        await uploadOne(task);
        task = queueRef.current.find((t) => t.phase === 'staged');
      }
    } finally {
      uploadingRef.current = false;
      setIsUploading(false);
    }
  }, [sessionId, uploadOne]);

  const cancelTask = useCallback(
    async (localId: string) => {
      abortRef.current[localId]?.abort();
      const task = queueRef.current.find((t) => t.localId === localId);
      if (task?.fileId && sessionId && task.phase !== 'ready') {
        void sessionFileService.removeFile(sessionId, task.fileId);
      }
      setQueue((q) => q.filter((t) => t.localId !== localId));
    },
    [sessionId, setQueue],
  );

  const retryTask = useCallback(
    async (localId: string) => {
      const task = queueRef.current.find((t) => t.localId === localId);
      if (!task) return;
      patch(localId, { phase: 'staged', progress: 0, fileId: undefined, error: undefined });
      await uploadOne({ ...task, phase: 'staged', fileId: undefined });
    },
    [patch, uploadOne],
  );

  const discardAll = useCallback(async () => {
    const inflight = queueRef.current.filter((t) => t.phase !== 'ready');
    await Promise.all(inflight.map((t) => cancelTask(t.localId)));
    setQueue([]);
  }, [cancelTask, setQueue]);

  const clearCompleted = useCallback(() => {
    setQueue((q) => q.filter((t) => t.phase !== 'ready'));
  }, [setQueue]);

  const hasPending = useCallback(() => queueRef.current.some((t) => t.phase !== 'ready'), []);

  return {
    queue,
    isUploading,
    stageFiles,
    submitStaged,
    cancelTask,
    retryTask,
    discardAll,
    clearCompleted,
    hasPending,
  };
}

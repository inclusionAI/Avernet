import { create } from 'zustand';

export type BotSessionFileStatus = 'upload_url_issued' | 'device_syncing' | 'ready' | 'device_sync_failed' | 'deleted';

export interface BotSessionFileView {
  resourceId: string;
  displayName: string;
  status: BotSessionFileStatus;
  sizeBytes: number | null;
  errorCode: string | null;
}

/** 上传任务进程态(不等于后端 status)。 */
export type UploadPhase = 'staged' | 'uploading' | 'completing' | 'failed' | 'ready';

export interface UploadTask {
  localId: string;
  name: string;
  size: number;
  phase: UploadPhase;
  progress: number;
  error?: string;
  /** 上传成功后关联的 resource_id(用于 chip 引用)。 */
  resourceId?: string;
  file: File;
  abortController?: AbortController;
}

export interface BotSessionFileState {
  readyFiles: BotSessionFileView[];
  uploadTasks: UploadTask[];
  isUploading: boolean;
  isLoadingList: boolean;
  setReadyFiles: (files: BotSessionFileView[]) => void;
  addReadyFile: (file: BotSessionFileView) => void;
  removeReadyFile: (resourceId: string) => void;
  addTask: (task: UploadTask) => void;
  updateTask: (localId: string, patch: Partial<UploadTask>) => void;
  removeTask: (localId: string) => void;
  clearTasks: () => void;
  setIsUploading: (v: boolean) => void;
  setIsLoadingList: (v: boolean) => void;
  resetForSession: () => void;
}

const empty = {
  readyFiles: [] as BotSessionFileView[],
  uploadTasks: [] as UploadTask[],
  isUploading: false,
  isLoadingList: false,
};

export const useBotSessionFileStore = create<BotSessionFileState>((set) => ({
  ...empty,
  setReadyFiles: (readyFiles) => set({ readyFiles }),
  addReadyFile: (file) =>
    set((s) => ({ readyFiles: [file, ...s.readyFiles.filter((f) => f.resourceId !== file.resourceId)] })),
  removeReadyFile: (resourceId) =>
    set((s) => ({ readyFiles: s.readyFiles.filter((f) => f.resourceId !== resourceId) })),
  addTask: (task) => set((s) => ({ uploadTasks: [...s.uploadTasks, task] })),
  updateTask: (localId, patch) =>
    set((s) => ({ uploadTasks: s.uploadTasks.map((t) => (t.localId === localId ? { ...t, ...patch } : t)) })),
  removeTask: (localId) => set((s) => ({ uploadTasks: s.uploadTasks.filter((t) => t.localId !== localId) })),
  clearTasks: () => set({ uploadTasks: [] }),
  setIsUploading: (isUploading) => set({ isUploading }),
  setIsLoadingList: (isLoadingList) => set({ isLoadingList }),
  resetForSession: () => set({ ...empty }),
}));

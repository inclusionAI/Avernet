import type { ParticipantView } from '@/domain/collaboration';
import type { SessionFileView } from '@/services/workspace/sessionFileService';

import { sessionFileService } from '@/services/workspace/sessionFileService';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export interface UseSessionFilesResult {
  files: SessionFileView[];
  total: number;
  isLoading: boolean;
  refresh: () => Promise<void>;
  removeFile: (fileId: string) => Promise<boolean>;
  shareFile: (fileId: string) => Promise<string | null>;
  prependFile: (file: SessionFileView) => void;
  downloadFile: (file: SessionFileView) => Promise<void>;
  previewFile: (file: SessionFileView) => Promise<void>;
}

export function useSessionFiles(sessionId: string | null, participants?: ParticipantView[]): UseSessionFilesResult {
  const [files, setFiles] = useState<SessionFileView[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!sessionId) {
      setFiles([]);
      setTotal(0);
      return;
    }
    setIsLoading(true);
    try {
      const res = await sessionFileService.loadFiles(sessionId, participants, { limit: 100, offset: 0 });
      if (res.ok) {
        setFiles(res.data.items);
        setTotal(res.data.total);
      } else {
        toast.error(res.error.friendlyMessage);
      }
    } finally {
      setIsLoading(false);
    }
  }, [sessionId, participants]);

  useEffect(() => {
    void refresh();
    // participants 变化会导致每帧重建引用，仅依赖 sessionId 触发刷新，成员变化不影响文件列表。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const removeFile = useCallback(
    async (fileId: string) => {
      if (!sessionId) return false;
      const res = await sessionFileService.removeFile(sessionId, fileId);
      if (res.ok) {
        setFiles((fs) => fs.filter((f) => f.fileId !== fileId));
        setTotal((t) => Math.max(0, t - 1));
        return true;
      }
      toast.error(res.error.friendlyMessage);
      return false;
    },
    [sessionId],
  );

  const shareFile = useCallback(
    async (fileId: string) => {
      if (!sessionId) return null;
      const res = await sessionFileService.shareFile(sessionId, fileId);
      if (res.ok) return res.data;
      toast.error(res.error.friendlyMessage);
      return null;
    },
    [sessionId],
  );

  const prependFile = useCallback((file: SessionFileView) => {
    setFiles((fs) => [file, ...fs.filter((f) => f.fileId !== file.fileId)]);
    setTotal((t) => t + 1);
  }, []);

  const downloadFile = useCallback(async (file: SessionFileView) => {
    const anchor = document.createElement('a');
    anchor.href = sessionFileService.buildDownloadUrl(file.sessionId, file.fileId);
    anchor.download = file.name;
    anchor.target = '_blank';
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }, []);

  const previewFile = useCallback(async (file: SessionFileView) => {
    const res = await sessionFileService.fetchContentBlob(file.sessionId, file.fileId);
    if (!res.ok) {
      toast.error(res.error.friendlyMessage);
      return;
    }
    const url = URL.createObjectURL(res.data);
    window.open(url, '_blank');
  }, []);

  return { files, total, isLoading, refresh, removeFile, shareFile, prependFile, downloadFile, previewFile };
}

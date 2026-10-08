import type { ParticipantView } from '@/domain/collaboration';
import type { AuthenticatedUserName } from '@/domain/userIdentity';
import type { SessionFileView } from '@/services/workspace/sessionFileService';

import { sessionFileService } from '@/services/workspace/sessionFileService';
import { useCallback, useEffect, useRef, useState } from 'react';
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

export function useSessionFiles(
  sessionId: string | null,
  participants?: ParticipantView[],
  authenticatedUser?: AuthenticatedUserName | null,
): UseSessionFilesResult {
  const [files, setFiles] = useState<SessionFileView[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  // PR#413 评审跟进 P3（#257104461）：请求代次守卫——快速连续切会话时旧响应可能晚到，
  // 过期响应直接丢弃，loading 只由最新请求收尾，避免旧会话数据短暂覆盖新会话列表。
  const requestIdRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!sessionId) {
      // 评审 P4 跟进（#257104636）：早退同样消耗代次——作废所有在途请求（晚到响应一律被守卫
      // 丢弃），并同步收尾加载态（在途请求的 finally 被守卫拦截后不再回写状态）。
      requestIdRef.current++;
      setFiles([]);
      setTotal(0);
      setIsLoading(false);
      return;
    }
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    try {
      // status=ready：仅拉取已就绪文件，避免 pending 文件计入并造成列表数量与展示不一致。
      const res = await sessionFileService.loadFiles(
        sessionId,
        participants,
        {
          limit: 100,
          offset: 0,
          status: 'ready',
        },
        authenticatedUser,
      );
      if (requestIdRef.current !== requestId) return;
      if (res.ok) {
        // 文件接口仅返回上传者 actor_id，用 bots/query 批量反查展示名，未命中的回退 actor_id 兜底。
        const nameMap = await sessionFileService.resolveActorNames(res.data.items.map((f) => f.ownerActorId));
        if (requestIdRef.current !== requestId) return;
        setFiles(
          res.data.items.map((f) => (nameMap[f.ownerActorId] ? { ...f, ownerName: nameMap[f.ownerActorId] } : f)),
        );
        setTotal(res.data.total);
      } else {
        toast.error(res.error.friendlyMessage);
      }
    } finally {
      if (requestIdRef.current === requestId) setIsLoading(false);
    }
  }, [authenticatedUser, participants, sessionId]);

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

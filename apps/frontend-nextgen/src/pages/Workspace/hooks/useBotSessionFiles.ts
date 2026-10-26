/** useBotSessionFiles — 单聊会话文件列表/删除/下载/预览。 */
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { botSessionFileService, isLargeBotSessionFile } from '@/services/workspace/botSessionFileService';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';

export interface UseBotSessionFilesResult {
  readyFiles: BotSessionFileView[];
  isLoadingList: boolean;
  refresh: () => Promise<void>;
  deleteFile: (file: BotSessionFileView) => Promise<boolean>;
  downloadFile: (file: BotSessionFileView) => Promise<void>;
  previewFile: (file: BotSessionFileView) => Promise<void>;
  addReadyFile: (file: BotSessionFileView) => void;
}

export function useBotSessionFiles(
  botId: string | null,
  sessionId: string | null,
  userId: string | null,
  ownerId?: string,
  friendUserId?: string,
): UseBotSessionFilesResult {
  const store = useBotSessionFileStore();
  const { setIsLoadingList, setReadyFiles, resetForSession } = store;
  // PR#413 评审跟进 P3（#257104461）：请求代次守卫——快速连续切会话时旧响应可能晚到，
  // 过期响应直接丢弃，避免旧会话数据经全局 store 短暂覆盖新会话列表。
  const requestIdRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!botId || !sessionId || !userId) {
      // 评审 P4 跟进（#257104636）：早退同样消耗代次——作废所有在途请求（晚到响应一律被守卫
      // 丢弃），并同步收尾加载态（在途请求的收尾被守卫拦截后不再回写 store）。
      requestIdRef.current++;
      setIsLoadingList(false);
      return;
    }
    const requestId = ++requestIdRef.current;
    setIsLoadingList(true);
    const readyRes = await botSessionFileService.loadReady(botId, sessionId, userId, ownerId, friendUserId);
    if (requestIdRef.current !== requestId) return;
    setIsLoadingList(false);
    if (readyRes.ok) setReadyFiles(readyRes.data.items);
    else toast.error(readyRes.error.friendlyMessage);
  }, [botId, sessionId, userId, ownerId, friendUserId, setIsLoadingList, setReadyFiles]);

  useEffect(() => {
    // 懒加载(2026-10-21):进入会话不再默认调 file 接口(选会话≠有查看文件清单的诉求);
    // /file 唤起与资源管理面板打开时才拉取(每次唤起都刷新)。身份/会话切换仍清缓存并作废在途请求。
    resetForSession();
    return () => {
      requestIdRef.current++;
      setIsLoadingList(false);
    };
  }, [botId, sessionId, userId, ownerId, friendUserId, resetForSession, setIsLoadingList]);

  const deleteFile = useCallback(
    async (file: BotSessionFileView) => {
      if (!botId || !sessionId || !userId) return false;
      const res = await botSessionFileService.remove(botId, sessionId, file.resourceId, userId, ownerId);
      if (res.ok) {
        store.removeReadyFile(file.resourceId);
        toast.success('已删除');
        return true;
      }
      toast.error(res.error.friendlyMessage);
      return false;
    },
    [botId, sessionId, userId, ownerId, store],
  );

  const downloadFile = useCallback(
    async (file: BotSessionFileView) => {
      if (!botId || !sessionId || !userId) return;
      let url: string;
      try {
        url = isLargeBotSessionFile(file.sizeBytes)
          ? await botSessionFileService.resolveExternalDownloadUrl(botId, sessionId, file.resourceId, userId, ownerId)
          : await botSessionFileService.resolveContentUrl(
              botId,
              sessionId,
              file.resourceId,
              userId,
              ownerId,
              'attachment',
            );
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '下载失败');
        return;
      }
      if (isLargeBotSessionFile(file.sizeBytes)) {
        window.open(url, '_blank', 'noopener,noreferrer');
        return;
      }
      const a = document.createElement('a');
      a.href = url;
      a.download = file.displayName;
      document.body.appendChild(a);
      a.click();
      a.remove();
    },
    [botId, sessionId, userId, ownerId],
  );

  const previewFile = useCallback(
    async (file: BotSessionFileView) => {
      if (!botId || !sessionId || !userId) return;
      let url: string;
      try {
        url = await botSessionFileService.resolveContentUrl(
          botId,
          sessionId,
          file.resourceId,
          userId,
          ownerId,
          'inline',
        );
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '预览失败');
        return;
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    },
    [botId, sessionId, userId, ownerId],
  );

  return {
    readyFiles: store.readyFiles,
    isLoadingList: store.isLoadingList,
    refresh,
    deleteFile,
    downloadFile,
    previewFile,
    addReadyFile: store.addReadyFile,
  };
}

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
): UseBotSessionFilesResult {
  const store = useBotSessionFileStore();
  const lastSessionKeyRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    if (!botId || !sessionId || !userId) return;
    store.setIsLoadingList(true);
    const readyRes = await botSessionFileService.loadReady(botId, sessionId, userId, ownerId);
    store.setIsLoadingList(false);
    if (readyRes.ok) store.setReadyFiles(readyRes.data.items);
    else toast.error(readyRes.error.friendlyMessage);
  }, [botId, sessionId, userId, ownerId, store]);

  useEffect(() => {
    const nextKey = `${botId ?? ''}_${sessionId ?? ''}_${userId ?? ''}_${ownerId ?? ''}`;
    if (lastSessionKeyRef.current === nextKey) return;
    lastSessionKeyRef.current = nextKey;
    store.resetForSession();
  }, [botId, sessionId, userId, ownerId, store]);

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

import type { ServicePublication } from '@/domain/botEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export function useServicePublications(botId?: string, ownerId?: string) {
  const [items, setItems] = useState<ServicePublication[]>([]);
  const [loading, setLoading] = useState(Boolean(botId));
  const load = useCallback(async () => {
    if (!botId) {
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setItems(await botEditorService.listLifecycle(botId, ownerId));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '发布状态加载失败');
    } finally {
      setLoading(false);
    }
  }, [botId, ownerId]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!botId || !items.some((item) => item.status === 'deploying' || item.deployment?.status === 'running')) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [botId, items, load]);
  const act = useCallback(
    async (work: () => Promise<unknown>, message: string) => {
      try {
        await work();
        toast.success(message);
        await load();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '发布操作失败');
        throw error;
      }
    },
    [load],
  );
  return {
    items,
    loading,
    reload: load,
    advance: (stage: 'prestable' | 'online') =>
      act(
        () => botEditorService.advanceLifecycle(botId!, stage, ownerId),
        stage === 'prestable' ? '预发发布已提交' : '上线请求已提交',
      ),
    restart: (stage: 'prestable' | 'online') =>
      act(() => botEditorService.restartLifecycle(botId!, stage, ownerId), '重启发布已提交'),
    cancel: () => act(() => botEditorService.cancelStaging(botId!, ownerId), '预发发布已取消'),
    offline: () => act(() => botEditorService.offlineLifecycle(botId!, ownerId), '下线请求已提交'),
    retry: () => act(() => botEditorService.retryLifecycle(botId!, ownerId), '重试请求已提交'),
    upgrade: (publicationId: number) =>
      act(() => botEditorService.upgradeLifecycle(botId!, publicationId, ownerId), '升级草稿已创建'),
    deleteDraft: () => act(() => botEditorService.deleteLifecycleDraft(botId!, ownerId), '服务 Bot 草稿已删除'),
  };
}

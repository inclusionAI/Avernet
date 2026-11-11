import type { ServicePublication, ServicePublicationAction } from '@/domain/botEditor';
import type { BotDomain } from '@/domain/botWorkshop';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { serviceBotEvalService } from '@/services/botWorkshop/serviceBotEvalService';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export type PublicationMenuAction = Exclude<ServicePublicationAction, 'restart_publish' | 'delete'>;
export const publicationActionLabel: Record<PublicationMenuAction, string> = {
  publish_staging: '发布到预发',
  publish_online: '发布上线',
  cancel_staging: '取消预发',
  offline: '下线',
  retry: '重试',
  upgrade: '创建升级草稿',
};
export type PublicationMenuRunResult = 'success' | 'blocked' | 'failed';

export const publicationStatusLabel: Record<ServicePublication['status'], string> = {
  draft: '草稿',
  deploying: '部署中',
  prestable: '预发',
  staging: '预发',
  running: '线上',
  offline: '已下线',
};
export function isPublicationMenuAction(action: ServicePublicationAction): action is PublicationMenuAction {
  return action !== 'restart_publish' && action !== 'delete';
}

/** 库存行对应唯一发布记录；缺少标识时不借用其他版本的操作。 */
function rowPublication(bot: BotDomain, items: ServicePublication[]) {
  if (bot.cardId) {
    return items.find(
      (item) =>
        item.cardId === bot.cardId && (bot.publicationVersion === undefined || item.version === bot.publicationVersion),
    );
  }
  if (bot.publicationVersion === undefined) return undefined;
  const matching = items.filter((item) => item.version === bot.publicationVersion);
  return matching.length === 1 ? matching[0] : undefined;
}

/** 仅打开菜单时获取生命周期；确认前再次核对可用操作，避免使用过期状态。 */
export function useBotPublicationMenu(
  bot: BotDomain,
  open: boolean,
  onChanged?: () => Promise<void>,
) {
  const [items, setItems] = useState<ServicePublication[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [pending, setPending] = useState(false);
  const request = useRef(0);
  const submitting = useRef(false);
  const reload = useCallback(async () => {
    const id = ++request.current;
    setLoading(true);
    setError(false);
    try {
      const next = await botEditorService.listLifecycle(bot.id, bot.ownerId);
      if (id === request.current) {
        const current = rowPublication(bot, next);
        setItems(current ? [current] : []);
      }
    } catch {
      if (id === request.current) {
        setItems([]);
        setError(true);
      }
    } finally {
      if (id === request.current) setLoading(false);
    }
  }, [bot.id, bot.ownerId, bot.cardId, bot.publicationVersion]);
  useEffect(() => {
    if (open) void reload();
    else {
      setLoading(true);
      setItems([]);
    }
    return () => {
      ++request.current;
    };
  }, [open, reload]);
  const run = async (
    action: PublicationMenuAction,
    item: ServicePublication,
  ): Promise<PublicationMenuRunResult> => {
    if (submitting.current) return 'failed';
    submitting.current = true;
    setPending(true);
    try {
      if (bot.lock?.status === 'other') throw new Error('该 Bot 正被他人编辑，暂时无法执行此操作。');
      const latest = await botEditorService.listLifecycle(bot.id, bot.ownerId);
      const current = rowPublication(bot, latest);
      if (current?.publicationId !== item.publicationId || !current.availableActions.includes(action)) {
        throw new Error('发布状态已更新，请重新打开状态菜单后操作。');
      }
      if (action === 'publish_staging') {
        const gate = await serviceBotEvalService.precheck(bot.id, String(item.version), bot.ownerId);
        if (!gate.allowed) return 'blocked';
        Tracert.click(Points.botWorkshop.publishPre, { bot_id: bot.id });
      }
      if (action === 'publish_online') Tracert.click(Points.botWorkshop.publishOnline, { bot_id: bot.id });
      switch (action) {
        case 'publish_staging':
          await botEditorService.advanceLifecycle(bot.id, 'prestable', bot.ownerId);
          break;
        case 'publish_online':
          await botEditorService.advanceLifecycle(bot.id, 'online', bot.ownerId);
          break;
        case 'cancel_staging':
          await botEditorService.cancelStaging(bot.id, bot.ownerId);
          break;
        case 'offline':
          await botEditorService.offlineLifecycle(bot.id, bot.ownerId);
          break;
        case 'retry':
          await botEditorService.retryLifecycle(bot.id, bot.ownerId);
          break;
        case 'upgrade':
          await botEditorService.upgradeLifecycle(bot.id, item.publicationId, bot.ownerId);
          break;
      }
      toast.success(
        action === 'upgrade'
          ? '升级草稿已创建'
          : action === 'cancel_staging'
          ? '预发发布已取消'
          : `${publicationActionLabel[action]}请求已提交`,
      );
      // 操作已成功，列表刷新失败不能提示用户重复提交。
      try {
        await onChanged?.();
      } catch {
        toast.error('操作已提交，列表刷新失败，请刷新列表查看最新状态。');
      }
      return 'success';
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : '发布操作失败，请稍后重试');
      return 'failed';
    } finally {
      submitting.current = false;
      setPending(false);
    }
  };
  return { items, loading, error, pending, reload, run };
}

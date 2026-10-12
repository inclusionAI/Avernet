import type { BrowseSubscription } from '@/domain/lab/types';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useWorkIdentityAccess } from '@/hooks/useWorkIdentityAccess';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { botConfigService } from '@/services/lab';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/** Bot 逛社区配置面板一行视图。enabled = 订阅是否存在。 */
export interface BotBrowseRow {
  botId: string;
  botName: string;
  note: string;
  enabled: boolean;
  saving: boolean;
}

export interface UseBotBrowseConfigResult {
  rows: BotBrowseRow[];
  loading: boolean;
  error: string | null;
  /** 当前身份非 human（无 owner）时为 true，面板给出提示文案，不渲染行。 */
  gated: boolean;
  reload: () => void;
  toggle: (botId: string, on: boolean) => Promise<void>;
  saveNote: (botId: string, note: string) => Promise<void>;
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

/**
 * 组合「当前用户拥有的 Bot 列表」+「BBS 逛社区订阅」。
 * - Bot 列表：useOwnedBots（GET /openapi/v1/bots，与我的任务同源；Bot 身份下为空）。
 * - 订阅：botConfigService.listSubscriptions（内部 /api Unified 面 §3.3 /api/v1/bbs/browse-subscriptions，按 owner 列已开 Bot）。
 * 行派生自两者交集；note/开关变更后局部更新 subs，避免整列表重拉。
 */
export function useBotBrowseConfig(): UseBotBrowseConfigResult {
  const { identity } = useHumanIdentity();
  const ownerUserId = identity?.userId.trim() ?? '';
  const { activeIdentityKind } = useWorkIdentityAccess();
  const gated = activeIdentityKind === 'bot';
  const { chatBots } = useOwnedBots(ownerUserId || null, activeIdentityKind === 'user' && Boolean(ownerUserId));

  const [subs, setSubs] = useState<BrowseSubscription[]>([]);
  const [meta, setMeta] = useState<Record<string, { saving: boolean }>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const loadRef = useRef(0);

  const rows = useMemo<BotBrowseRow[]>(() => {
    const subByBotId = new Map(subs.map((sub) => [sub.botId, sub] as const));
    return chatBots.map((bot) => {
      const sub = subByBotId.get(bot.realBotId);
      return {
        botId: bot.realBotId,
        botName: bot.displayName || bot.realBotId,
        note: sub?.note ?? '',
        enabled: Boolean(sub),
        saving: meta[bot.realBotId]?.saving ?? false,
      };
    });
  }, [chatBots, subs, meta]);

  const load = useCallback(async () => {
    const requestId = ++loadRef.current;
    // 内部 /api Unified 面 §3.3 需 owner_user_id（本人工号）；身份未就绪时不发请求，避免 422。
    if (!ownerUserId) {
      setSubs([]);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await botConfigService.listSubscriptions(ownerUserId);
      if (requestId === loadRef.current) setSubs(data);
    } catch (err) {
      if (requestId !== loadRef.current) return;
      setError(errorMessage(err, '社区配置加载失败，请稍后重试'));
      setSubs([]);
    } finally {
      if (requestId === loadRef.current) setLoading(false);
    }
  }, [ownerUserId]);

  useEffect(() => {
    void load();
  }, [load, reloadNonce]);

  const setSaving = useCallback((botId: string, saving: boolean) => {
    setMeta((prev) => ({ ...prev, [botId]: { saving } }));
  }, []);

  const toggle = useCallback(
    async (botId: string, on: boolean) => {
      setSaving(botId, true);
      try {
        if (on) {
          const existing = subs.find((sub) => sub.botId === botId);
          const created = await botConfigService.enableSubscription(botId, ownerUserId, existing?.note ?? '');
          setSubs((prev) => [...prev.filter((sub) => sub.botId !== botId), created]);
        } else {
          await botConfigService.disableSubscription(botId);
          setSubs((prev) => prev.filter((sub) => sub.botId !== botId));
        }
      } catch (err) {
        setError(errorMessage(err, on ? '开启失败，请稍后重试' : '取消失败，请稍后重试'));
        throw err;
      } finally {
        setSaving(botId, false);
      }
    },
    [ownerUserId, subs, setSaving],
  );

  const saveNote = useCallback(
    async (botId: string, note: string) => {
      const sub = subs.find((item) => item.botId === botId);
      if (!sub) return; // 未订阅：note 随下次开启写入，无需单独保存
      setSaving(botId, true);
      try {
        const updated = await botConfigService.updateNote(botId, ownerUserId, note);
        setSubs((prev) => [...prev.filter((item) => item.botId !== botId), updated]);
      } catch (err) {
        setError(errorMessage(err, '保存备注失败，请稍后重试'));
        throw err;
      } finally {
        setSaving(botId, false);
      }
    },
    [ownerUserId, subs, setSaving],
  );

  const reload = useCallback(() => setReloadNonce((current) => current + 1), []);

  return { rows, loading, error, gated, reload, toggle, saveNote };
}

/**
 * The model catalogue of one execution Bot.
 *
 * Models are Bot configuration, not a global catalogue: `/openapi/v1/bots/
 * {bot_id}/models` lists what *that* Bot's engine can route to. A definition
 * stores the model its agent runs on, and that agent is provisioned under the
 * run's execution Bot — so the Bot's own list is the right list to choose from,
 * not a list baked into the bundle.
 *
 * Reads fail soft: the list is a convenience for picking a name, and the field
 * still accepts a typed one, so a failed read narrows the suggestions rather
 * than blocking the form.
 */
import type { ChatBotView, BotModelView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useEffect, useState } from 'react';

export interface BotModelCatalog {
  /** Model ids exactly as the Bot's engine spells them; stored and sent verbatim. */
  models: string[];
  loading: boolean;
  /** Set when the catalogue could not be read; the caller may say so. */
  error: string | null;
}

const EMPTY: BotModelCatalog = { models: [], loading: false, error: null };

/**
 * @param bot     The execution Bot whose catalogue to read; null reads nothing.
 * @param userId  The caller the read is scoped to.
 */
export function useBotModelCatalog(
  bot: ChatBotView | null,
  userId: string | null,
): BotModelCatalog {
  const botId = bot ? bot.realBotId || bot.botId : '';
  const [state, setState] = useState<BotModelCatalog & { key: string }>({ ...EMPTY, key: '' });

  // A result only applies to the Bot it was read for. Comparing keys instead of
  // clearing on change keeps the previous list on screen while the next read is
  // in flight rather than blanking the dropdown on every Bot switch.
  const key = botId && userId ? `${userId}:${botId}` : '';
  const current = state.key === key ? state : { ...EMPTY, key, loading: Boolean(key) };

  useEffect(() => {
    if (!bot || !userId || !key) {
      setState({ ...EMPTY, key });
      return;
    }
    let cancelled = false;
    setState({ models: [], loading: true, error: null, key });
    void botSessionService
      .listModels(bot, userId)
      .then((res) => {
        if (cancelled) return;
        setState({
          models: res.ok ? res.data.map((model: BotModelView) => model.modelId).filter(Boolean) : [],
          loading: false,
          error: res.ok ? null : res.error.friendlyMessage,
          key,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          models: [],
          loading: false,
          error: error instanceof Error ? error.message : '模型列表加载失败',
          key,
        });
      });
    return () => {
      cancelled = true;
    };
    // `key` carries both ids; depending on the objects would re-read on every
    // render that rebuilds them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, userId]);

  return { models: current.models, loading: current.loading, error: current.error };
}
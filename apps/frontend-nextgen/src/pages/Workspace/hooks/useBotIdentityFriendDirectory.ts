import {
  botFriendConversationService,
  type BotFriendActorType,
  type BotIdentityFriendView,
} from '@/services/workspace/botFriendConversationService';
import { useCallback, useEffect, useRef, useState } from 'react';

export interface UseBotIdentityFriendDirectoryResult {
  humanFriends: BotIdentityFriendView[];
  botFriends: BotIdentityFriendView[];
  humanLoading: boolean;
  botLoading: boolean;
  humanError: string | null;
  botError: string | null;
  settled: boolean;
  reloadHuman: () => void;
  reloadBot: () => void;
}

interface SectionState {
  items: BotIdentityFriendView[];
  loading: boolean;
  error: string | null;
}

const EMPTY_SECTION: SectionState = { items: [], loading: false, error: null };

export function useBotIdentityFriendDirectory(
  botIdentityId: string | null,
  enabled: boolean,
): UseBotIdentityFriendDirectoryResult {
  const [human, setHuman] = useState<SectionState>(EMPTY_SECTION);
  const [bot, setBot] = useState<SectionState>(EMPTY_SECTION);
  const [settled, setSettled] = useState(false);
  const generationRef = useRef(0);
  const identityRef = useRef(botIdentityId);
  const controllersRef = useRef(new Map<string, AbortController>());
  identityRef.current = botIdentityId;

  useEffect(() => {
    generationRef.current += 1;
    const generation = generationRef.current;
    controllersRef.current.forEach((controller) => controller.abort());
    controllersRef.current.clear();
    setHuman(EMPTY_SECTION);
    setBot(EMPTY_SECTION);
    setSettled(false);
    if (!enabled || !botIdentityId) return;

    const controller = new AbortController();
    controllersRef.current.set('directory', controller);
    setHuman({ items: [], loading: true, error: null });
    setBot({ items: [], loading: true, error: null });
    void botFriendConversationService
      .loadDirectory(botIdentityId, controller.signal)
      .then((result) => {
        if (generation !== generationRef.current || controller.signal.aborted) return;
        setHuman(
          result.human.ok
            ? { items: result.human.data.items, loading: false, error: null }
            : { items: [], loading: false, error: result.human.error.friendlyMessage },
        );
        setBot(
          result.bot.ok
            ? { items: result.bot.data.items, loading: false, error: null }
            : { items: [], loading: false, error: result.bot.error.friendlyMessage },
        );
        setSettled(true);
      })
      .finally(() => {
        if (controllersRef.current.get('directory') === controller) controllersRef.current.delete('directory');
      });

    return () => controller.abort();
  }, [botIdentityId, enabled]);

  useEffect(
    () => () => {
      controllersRef.current.forEach((controller) => controller.abort());
      controllersRef.current.clear();
    },
    [],
  );

  const reloadSection = useCallback(
    (targetType: BotFriendActorType) => {
      const identityId = identityRef.current;
      if (!enabled || !identityId) return;
      const generation = generationRef.current;
      const key = `section:${targetType}`;
      controllersRef.current.get(key)?.abort();
      const controller = new AbortController();
      controllersRef.current.set(key, controller);
      const setSection = targetType === 'human' ? setHuman : setBot;
      setSection((current) => ({ ...current, loading: true, error: null }));
      void botFriendConversationService
        .loadSection(identityId, targetType, controller.signal)
        .then((result) => {
          if (generation !== generationRef.current || controller.signal.aborted || identityRef.current !== identityId)
            return;
          setSection((current) =>
            result.ok
              ? { items: result.data.items, loading: false, error: null }
              : { ...current, loading: false, error: result.error.friendlyMessage },
          );
        })
        .finally(() => {
          if (controllersRef.current.get(key) === controller) controllersRef.current.delete(key);
        });
    },
    [enabled],
  );

  return {
    humanFriends: human.items,
    botFriends: bot.items,
    humanLoading: human.loading,
    botLoading: bot.loading,
    humanError: human.error,
    botError: bot.error,
    settled,
    reloadHuman: () => reloadSection('human'),
    reloadBot: () => reloadSection('bot'),
  };
}

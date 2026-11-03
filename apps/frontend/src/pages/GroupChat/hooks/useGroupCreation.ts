import { useEffect, useMemo, useRef, useState } from 'react';
import { useBotNetworkStore } from '@/stores/botNetworkStore';
import { useUserStore } from '@/stores/userStore';
import { useBotNetwork } from './useBotNetwork';
import {
  BCS_API_BASE, GROUP_CREATION_HOST, groupLink,
  readBotPage, groupOriginatorBots, readStoredDefinition,
} from '@/services/backend-api/GroupCreationController';
import type {
  BotPage, CollaborationDefinitionReference,
  GroupBot, StoredDefinition,
} from '@/services/backend-api/GroupCreationController';
import type { CreateGroupParams } from '../types';
import { GroupCreationSubmission, type GroupSubmissionState } from '../utils/groupCreationSubmission';

export function useGroupBotPage(originator: string | null, enabled: boolean, name: string) {
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; loading: boolean; error: string; page: BotPage }>({
    key: '', loading: false, error: '', page: { items: [], total: 0, offset: 0, limit: 20 },
  });
  const scope = `${originator}|${name}`;
  const key = `${scope}|${offset}`;
  const previous = useRef(scope);
  useEffect(() => {
    if (previous.current !== scope) { previous.current = scope; setOffset(0); if (offset) return; }
    if (!enabled || !originator) return;
    const controller = new AbortController();
    setState(s => ({ ...s, key, loading: true, error: '', page: { items: [], total: 0, offset, limit: 20 } }));
    const timer = window.setTimeout(async () => {
      try {
        const page = await readBotPage(originator, name, offset, controller.signal);
        if (!controller.signal.aborted) setState({ key, loading: false, error: '', page });
      } catch (e) {
        if (controller.signal.aborted) return;
        setState(s => ({ ...s, loading: false, error: (e as Error).message }));
      }
    }, name ? 250 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [key, enabled, attempt]);
  const page = state.key === key && enabled ? state.page : { items: [], total: 0, offset, limit: 20 };
  return { page, loading: enabled && (state.loading || state.key !== key), error: state.key === key ? state.error : '',
    previous: () => setOffset(v => Math.max(0, v - 20)), next: () => setOffset(v => v + 20), retry: () => setAttempt(v => v + 1) };
}

export function useGroupCreation({ selectOriginator, initialDraft, open, query }: {
  selectOriginator: boolean; initialDraft?: CollaborationDefinitionReference; open: boolean; query: string;
}) {
  const myBots = useBotNetworkStore(state => state.myBots);
  const loadingBots = useBotNetworkStore(state => state.isLoadingMyBots);
  const tabBotId = useBotNetworkStore(state => state.driverBot?.bot_uuid);
  const userId = useUserStore(state => state.userId);
  const nickName = useUserStore(state => state.nickName);
  const user = userId ? { user_id: userId, display_name: nickName || userId } : undefined;
  const { initUnifiedBotTabs } = useBotNetwork();
  const ownedBots = useMemo(() => groupOriginatorBots(myBots), [myBots]);
  const [originatorId, setOriginatorId] = useState('');
  const originator = ownedBots.find(bot => bot.bot_id === originatorId);
  const setOriginator = (bot: GroupBot | undefined) => setOriginatorId(bot?.bot_id || '');
  const [initialDefinition, setInitialDefinition] = useState<StoredDefinition>();
  const [sourceError, setSourceError] = useState('');
  const [definitionAttempt, setDefinitionAttempt] = useState(0);
  const [submission, setSubmission] = useState<GroupSubmissionState>({ phase: 'editing' });
  const operation = useRef<GroupCreationSubmission>();
  if (!operation.current) operation.current = new GroupCreationSubmission(setSubmission);
  const requestedBots = useRef(false);
  useEffect(() => {
    if (!selectOriginator || !open || loadingBots || myBots.length || requestedBots.current) return;
    // Non-BCN hosts may not have initialized the shared tabs yet.
    requestedBots.current = true;
    void initUnifiedBotTabs();
  }, [selectOriginator, open, loadingBots, myBots.length, initUnifiedBotTabs]);
  useEffect(() => {
    if (!selectOriginator || !open || originator) return;
    const preferred = ownedBots.find(bot => bot.bot_id === tabBotId)
      || (ownedBots.length === 1 ? ownedBots[0] : undefined);
    setOriginatorId(preferred?.bot_id || '');
  }, [selectOriginator, open, ownedBots, tabBotId, originator]);
  useEffect(() => {
    setInitialDefinition(undefined); setSourceError('');
    if (!initialDraft) return;
    const controller = new AbortController();
    readStoredDefinition(BCS_API_BASE, initialDraft, controller.signal).then(source => {
      if (!controller.signal.aborted) setInitialDefinition(source);
    }).catch(e => { if (!controller.signal.aborted) setSourceError((e as Error).message); });
    return () => controller.abort();
  }, [initialDraft?.draftId, initialDraft?.revision, definitionAttempt]);
  useEffect(() => () => operation.current?.dispose(), []);
  const mine = { items: ownedBots, loading: loadingBots, retry: () => { void initUnifiedBotTabs(); } };
  const candidates = useGroupBotPage(originator?.bot_id ?? null,
    selectOriginator && open && !!originator, query);
  const busy = submission.phase === 'preflight' || submission.phase === 'creating';
  const frozen = submission.phase !== 'editing';
  const submit = async (params: CreateGroupParams) => {
    if (!selectOriginator || !originator || loadingBots || frozen) return;
    const result = await operation.current!.submit({ params: { ...params, originator: originator.bot_id } });
    if (result) return groupLink(GROUP_CREATION_HOST, user, result);
  };
  return { user, originator, setOriginator, initialDefinition, error: sourceError,
    initialDefinitionPending: !!initialDraft && !initialDefinition, mine,
    candidates, submission, busy, frozen, submit, retry: () => setDefinitionAttempt(v => v + 1) };
}

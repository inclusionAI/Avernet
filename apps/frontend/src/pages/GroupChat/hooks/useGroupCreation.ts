import { useCallback, useEffect, useRef, useState } from 'react';
import {
  BCS_API_BASE, GROUP_CREATION_HOST, groupLink,
  readBotPage, readGroupUser, readStoredDefinition,
} from '@/services/backend-api/GroupCreationController';
import type {
  BotPage, CollaborationDefinitionReference,
  GroupBot, GroupUser, StoredDefinition,
} from '@/services/backend-api/GroupCreationController';
import type { CreateGroupParams } from '../types';
import { GroupCreationSubmission, type GroupSubmissionState } from '../utils/groupCreationSubmission';

export function useGroupBotPage(user: GroupUser | undefined, originator: string | null,
  enabled: boolean, name: string, identityChanged: () => void) {
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; loading: boolean; error: string; page: BotPage }>({
    key: '', loading: false, error: '', page: { items: [], total: 0, offset: 0, limit: 20 },
  });
  const scope = `${user?.user_id}|${originator}|${name}`;
  const key = `${scope}|${offset}`;
  const previous = useRef(scope);
  useEffect(() => {
    if (previous.current !== scope) { previous.current = scope; setOffset(0); if (offset) return; }
    if (!enabled || !user) return;
    const controller = new AbortController();
    setState(s => ({ ...s, key, loading: true, error: '', page: { items: [], total: 0, offset, limit: 20 } }));
    const timer = window.setTimeout(async () => {
      try {
        const page = await readBotPage(GROUP_CREATION_HOST, user, originator, name, offset, controller.signal);
        if (!controller.signal.aborted) setState({ key, loading: false, error: '', page });
      } catch (e) {
        if (controller.signal.aborted) return;
        if (['user_context_changed', 'unauthenticated'].includes((e as any).code)) identityChanged();
        setState(s => ({ ...s, loading: false, error: (e as Error).message }));
      }
    }, name ? 250 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [key, enabled, attempt, identityChanged]);
  const page = state.key === key && enabled ? state.page : { items: [], total: 0, offset, limit: 20 };
  return { page, loading: enabled && (state.loading || state.key !== key), error: state.key === key ? state.error : '',
    previous: () => setOffset(v => Math.max(0, v - 20)), next: () => setOffset(v => v + 20), retry: () => setAttempt(v => v + 1) };
}

export function useGroupCreation({ selectOriginator, initialDraft, open, query }: {
  selectOriginator: boolean; initialDraft?: CollaborationDefinitionReference; open: boolean; query: string;
}) {
  const [user, setUser] = useState<GroupUser>();
  const [originator, setOriginator] = useState<GroupBot>();
  const [initialDefinition, setInitialDefinition] = useState<StoredDefinition>();
  const [error, setError] = useState('');
  const [sourceError, setSourceError] = useState('');
  const [checking, setChecking] = useState(false);
  const [definitionAttempt, setDefinitionAttempt] = useState(0);
  const [submission, setSubmission] = useState<GroupSubmissionState>({ phase: 'editing' });
  const operation = useRef<GroupCreationSubmission>();
  if (!operation.current) operation.current = new GroupCreationSubmission(setSubmission);
  const identityRequest = useRef<AbortController>();
  const userId = useRef<string>();
  const identityChanged = useCallback(() => {
    identityRequest.current?.abort(); setUser(undefined); setOriginator(undefined);
    setChecking(false); setError('登录状态已变化，请重新确认登录');
  }, []);
  const refresh = useCallback(async () => {
    identityRequest.current?.abort();
    const controller = new AbortController(); identityRequest.current = controller;
    setChecking(true);
    try {
      const next = await readGroupUser(GROUP_CREATION_HOST, controller.signal);
      if (controller.signal.aborted) return;
      if (userId.current !== next.user_id) setOriginator(undefined);
      userId.current = next.user_id; setUser(next); setError('');
    } catch (e) {
      if (!controller.signal.aborted) { setUser(undefined); setOriginator(undefined); setError((e as Error).message); }
    } finally { if (!controller.signal.aborted) setChecking(false); }
  }, []);
  useEffect(() => {
    if (!selectOriginator || !open) return;
    void refresh();
    const check = () => { if (document.visibilityState !== 'hidden') void refresh(); };
    window.addEventListener('focus', check); document.addEventListener('visibilitychange', check);
    const timer = window.setInterval(check, 30_000);
    return () => { identityRequest.current?.abort(); window.clearInterval(timer);
      window.removeEventListener('focus', check); document.removeEventListener('visibilitychange', check); };
  }, [selectOriginator, open, refresh]);
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
  const mine = useGroupBotPage(user, null, selectOriginator && open, '', identityChanged);
  const candidates = useGroupBotPage(user, originator?.bot_id ?? null,
    selectOriginator && open && !!originator, query, identityChanged);
  useEffect(() => {
    if (!originator && !mine.loading && !mine.error && mine.page.total === 1 && mine.page.items.length === 1) {
      setOriginator(mine.page.items[0]);
    }
  }, [mine.page, mine.loading, mine.error, originator]);
  const busy = submission.phase === 'preflight' || submission.phase === 'creating';
  const frozen = submission.phase !== 'editing';
  const submit = async (params: CreateGroupParams) => {
    if (!selectOriginator || !user || !originator || checking || frozen) return;
    const result = await operation.current!.submit({ user, params: { ...params, originator: originator.bot_id } });
    if (result) return groupLink(GROUP_CREATION_HOST, user, result);
    await refresh();
  };
  return { user, originator, setOriginator, initialDefinition, checking, error: sourceError || error,
    initialDefinitionPending: !!initialDraft && !initialDefinition, mine,
    candidates, submission, busy, frozen, submit, retry: () => {
      if (sourceError) setDefinitionAttempt(v => v + 1);
      if (selectOriginator) void refresh();
    } };
}

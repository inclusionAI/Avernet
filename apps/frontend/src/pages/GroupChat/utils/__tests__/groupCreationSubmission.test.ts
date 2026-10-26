import { webcrypto } from 'crypto';
import { readFileSync } from 'fs';
import path from 'path';
import { GroupCreationSubmission } from '../groupCreationSubmission';
import { BCS_API_BASE, decodeOpenCreateGroupOptions, groupCreationBody, readStoredDefinition,
  readBotPage, groupLink, GROUP_CREATION_HOST } from '@/services/backend-api/GroupCreationController';
import type { CreateGroupParams } from '../../types';

const fixture = JSON.parse(readFileSync(path.resolve(__dirname, '../../../../../../bcs/tests/fixtures/collaboration-definition-draft/indentation-regression.json'), 'utf8'));
const validation = { warnings: [], errors: [], ...JSON.parse(readFileSync(path.resolve(__dirname, '../../../../../../bcs/assets/panel/test/fixtures/incident-loop-preview.json'), 'utf8')) };
const reference = { schemaVersion: 2 as const, draftId: '750c52c6-60e2-4249-8ee5-1fb8cb9402ab', revision: 1, readToken: 'A'.repeat(43) };
const revision = { ...fixture.revision, draft_id: reference.draftId };
const user = { user_id: 'alice', display_name: 'Alice' };
const params: CreateGroupParams = { driver_bot: 'alice-bot', originator: 'alice-bot', label: '稿件协作', context: '审稿并发表',
  group_strategy: 'state_machine', collaboration_definition_yaml: revision.definition_yaml,
  participants: ['alice-bot', 'writer', 'reviewer', 'publisher'].map(bot_uuid => ({ bot_uuid })),
  participant_bindings: { writer: { source: 'manual', bot_ids: ['writer'] }, reviewer: { source: 'manual', bot_ids: ['reviewer'] }, publisher: { source: 'manual', bot_ids: ['publisher'] } } };
let mode: string, calls: Array<{ url: string; options: any }>, resolvePost: (() => void) | undefined;
const ok = (data: any) => ({ ok: true, status: 200, json: async () => ({ code: 20000, data }) });
beforeEach(() => {
  mode = 'valid'; calls = []; resolvePost = undefined;
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
  Object.assign(globalThis, { window: { location: { href: 'http://local.test/bcn/chat', origin: 'http://local.test' }, setTimeout, clearTimeout } });
  globalThis.fetch = jest.fn(async (raw: any, options: any) => {
    const url = String(raw); calls.push({ url, options });
    if (url.includes('/drafts/')) {
      if (mode === 'revoked') return { ok: false, status: 404, json: async () => ({}) };
      if (mode === 'hash-mismatch') return ok({ ...revision, definition_yaml: revision.definition_yaml.replace('version', 'versiox') });
      return ok(mode === 'corrupt' ? { ...revision, size_bytes: 1 } : revision);
    }
    if (url.endsWith('/definitions/validate')) return ok(mode === 'invalid' ? { ...validation, valid: false }
      : mode === 'new-role' ? { ...validation, participants: [...validation.participants, { binding: 'editor' }] }
      : mode === 'fixed-actor' ? { ...validation, graph: { ...validation.graph, nodes: [{ assignee: { type: 'runtime_actor', actor: 'human_alice' } }] } } : validation);
    if (url.endsWith('/me') && mode === 'post-identity-error' && calls.some(call => call.url.endsWith('/groups'))) return { ok: false, status: 401, json: async () => ({ code: 40100, data: { error_code: 'unauthenticated' } }) };
    if (url.endsWith('/me')) return ok({ ...user, user_id: mode === 'account-change' || (mode === 'post-account-change' && calls.some(call => call.url.endsWith('/groups'))) ? 'bob' : user.user_id });
    if (url.includes('/candidates')) return ok({ items: [{ bot: { bot_id: 'friend', name: 'Friend', kind: 'bot', status: 'online', reachability: 'reachable' } }], total: 21, offset: 20, limit: 20 });
    if (mode === 'timeout') throw new Error('lost response');
    if (mode === 'malformed') return ok({ group_id: 'group' });
    if (mode === 'server-error') return { ok: false, status: 400, json: async () => ({ code: 40000, data: { error_code: 'invalid_request' } }) };
    if (mode === 'pending') return new Promise(resolve => { resolvePost = () => resolve(ok({ group_id: 'group', initial_session_id: 'session' })); });
    return ok({ group_id: 'group', initial_session_id: 'session' });
  }) as any;
});
const posts = () => calls.filter(c => c.url.endsWith('/groups'));
it('submits current form bytes and bindings exactly once without reloading a Draft', async () => {
  const operation = new GroupCreationSubmission(() => {}); mode = 'pending';
  const pending = operation.submit({ user, params }); expect(await operation.submit({ user, params })).toBeUndefined();
  for (let i = 0; i < 30 && !resolvePost; i++) await new Promise(resolve => { setTimeout(resolve, 5); });
  expect(posts()).toHaveLength(1); expect(calls.some(call => call.url.includes('/drafts/'))).toBe(false);
  expect(JSON.stringify(calls)).not.toContain(reference.readToken);
  expect(posts()[0].options.headers['X-BCS-Expected-User']).toBe('alice');
  const body = JSON.parse(posts()[0].options.body);
  expect(body.originator).toBe('alice-bot'); expect(body.driver_bot_uuid).toBe('alice-bot');
  expect(body.collaboration.definition.content_yaml).toBe(revision.definition_yaml);
  expect(Buffer.byteLength(body.collaboration.definition.content_yaml)).toBe(2174);
  expect(body.collaboration.participant_bindings).toHaveLength(3);
  resolvePost!(); const result = await pending;
  expect(result).toEqual({ group_id: 'group', initial_session_id: 'session', driver_bot_uuid: 'alice-bot' });
  expect(groupLink(GROUP_CREATION_HOST, user, result)).toBe('/bcn/chat/detail?id=group&session=session&bot_uuid=alice-bot');
  expect(calls[calls.length - 1].url).toMatch(/\/me$/);
  expect(operation.state.phase).toBe('success'); expect(await operation.submit({ user, params })).toBeUndefined(); expect(posts()).toHaveLength(1);
});
it('validates and submits edited text unchanged, independent of later form mutations', async () => {
  const edited = `${revision.definition_yaml}\n# 用户编辑：保留换行与缩进\r\n`;
  const editedParams = { ...params, collaboration_definition_yaml: edited };
  const operation = new GroupCreationSubmission(() => {});
  const pending = operation.submit({ user, params: editedParams }); editedParams.collaboration_definition_yaml = 'later mutation'; await pending;
  expect(JSON.parse(calls.find(c => c.url.endsWith('/definitions/validate'))!.options.body).definition_yaml).toBe(edited);
  expect(JSON.parse(posts()[0].options.body).collaboration.definition.content_yaml).toBe(edited);
  expect(calls.some(call => call.url.includes('/drafts/'))).toBe(false);
});
it.each(['invalid', 'new-role', 'account-change', 'fixed-actor'])('blocks POST after %s preflight failure', async failure => {
  mode = failure; const operation = new GroupCreationSubmission(() => {}); expect(await operation.submit({ user, params })).toBeUndefined();
  expect(posts()).toHaveLength(0); expect(operation.state.phase).toBe('editing'); expect(operation.state.error).toBeTruthy();
});
it.each(['timeout', 'malformed', 'server-error', 'post-identity-error', 'post-account-change'])('locks unknown result after %s without returning a navigation result', async failure => {
  mode = failure; const operation = new GroupCreationSubmission(() => {}); expect(await operation.submit({ user, params })).toBeUndefined();
  expect(operation.state.phase).toBe('unknown'); mode = 'valid'; expect(await operation.submit({ user, params })).toBeUndefined(); expect(posts()).toHaveLength(1);
});
it('does not return a navigation result after the modal is disposed during creation', async () => {
  const operation = new GroupCreationSubmission(() => {}); mode = 'pending';
  const pending = operation.submit({ user, params });
  for (let i = 0; i < 30 && !resolvePost; i++) await new Promise(resolve => { setTimeout(resolve, 5); });
  expect(posts()).toHaveLength(1); operation.dispose(); resolvePost!();
  expect(await pending).toBeUndefined(); expect(operation.state.phase).not.toBe('success');
});
it.each(['chat', 'manager_worker'] as const)('uses ordinary form parameters for %s without a Draft', async group_strategy => {
  const ordinary: CreateGroupParams = { driver_bot: 'friend', originator: 'alice-bot', participants: [{ bot_uuid: 'friend' }, { bot_uuid: 'alice-bot' }],
    label: '用户自己的协作群', group_strategy, routing_policy: { default_bot_final_delivery: 'inject_observers' } };
  const operation = new GroupCreationSubmission(() => {});
  const pending = operation.submit({ user, params: ordinary });
  ordinary.driver_bot = 'later-selection';
  const result = await pending;
  const body = JSON.parse(posts()[0].options.body);
  expect(body.collaboration.strategy).toBe(group_strategy); expect(body.driver_bot_uuid).toBe('friend');
  expect(body.participants.find((p: any) => p.actor_id === 'friend').role).toBe(group_strategy === 'chat' ? 'driver' : 'manager');
  expect(calls.some(call => /drafts|validate/.test(call.url))).toBe(false); expect(operation.state.phase).toBe('success');
  expect(result?.driver_bot_uuid).toBe('friend');
  expect(operation.state.result).toEqual(result);
  expect(groupLink(GROUP_CREATION_HOST, user, result)).toBe('/bcn/chat/detail?id=group&session=session&bot_uuid=friend');
});
it('reads initial source with its capability without any group write', async () => {
  const source = await readStoredDefinition(BCS_API_BASE, reference, new AbortController().signal);
  expect(source.definitionYaml).toBe(revision.definition_yaml); expect(calls[0].options.headers['X-BCS-Draft-Read-Token']).toBe(reference.readToken); expect(posts()).toHaveLength(0);
});
it.each(['revoked', 'corrupt', 'hash-mismatch'])('rejects %s initial content', async failure => {
  mode = failure; await expect(readStoredDefinition(BCS_API_BASE, reference, new AbortController().signal)).rejects.toThrow(); expect(posts()).toHaveLength(0);
});
it('queries candidates with selected originator and pagination', async () => {
  const page = await readBotPage(GROUP_CREATION_HOST, user, 'alice-bot', '审稿', 20, new AbortController().signal);
  expect(page.items[0].bot_id).toBe('friend'); expect(calls[0].url).toContain('/bots/alice-bot/candidates?offset=20&limit=20'); expect(calls[0].url).toContain('purpose=collaboration');
});
it('accepts empty open events and initial Drafts but rejects identity overrides', () => {
  expect(decodeOpenCreateGroupOptions()).toEqual({}); expect(decodeOpenCreateGroupOptions({})).toEqual({});
  expect(decodeOpenCreateGroupOptions({ draft: reference })).toEqual({ draft: reference });
  expect(() => decodeOpenCreateGroupOptions({ userId: 'alice' })).toThrow();
  expect(() => decodeOpenCreateGroupOptions({ draft: { ...reference, owner: 'alice' } })).toThrow();
});
it('preserves advanced fields and deduplicates participants', () => {
  const body = groupCreationBody({ ...params, opening_message: '{{bcs.run_id}}\n', participants: [...params.participants, params.participants[0]] }, validation);
  expect(body.opening_message).toBe('{{bcs.run_id}}\n'); expect(body.participants).toHaveLength(4);
});
it('navigates to the created group/session as its driver, keeping the fallback list in Human perspective', () => {
  expect(groupLink(GROUP_CREATION_HOST, user, { group_id: 'group', initial_session_id: 'group:session', driver_bot_uuid: 'bot-b:entity' })).toBe('/bcn/chat/detail?id=group&session=group%3Asession&bot_uuid=bot-b%3Aentity');
  expect(groupLink(GROUP_CREATION_HOST, user)).toBe('/bcn/chat/list?bot_uuid=human_alice');
});

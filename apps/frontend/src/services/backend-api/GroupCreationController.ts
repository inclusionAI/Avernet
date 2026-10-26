import type { CreateGroupParams } from '@/pages/GroupChat/types';
import type { CollaborationDefinitionValidationResponse } from './BcnController';

export type CollaborationDefinitionValidationResult = CollaborationDefinitionValidationResponse & {
  participants: NonNullable<CollaborationDefinitionValidationResponse['participants']>;
  warnings: NonNullable<CollaborationDefinitionValidationResponse['warnings']>;
};

export interface GroupCreationHost {
  userApiBaseUrl: string;
  groupUrlTemplate: string;
  groupListUrl: string;
}
export interface GroupUser { user_id: string; display_name: string }
export interface GroupBot {
  bot_id: string; name: string; kind: 'bot'; created_by?: string;
  status: string; reachability: string; summary?: string;
}
export interface BotPage { items: GroupBot[]; total: number; offset: number; limit: number }
/** Created group identifiers plus the submitted driver used for navigation. */
export interface GroupCreationResult { group_id: string; initial_session_id: string; driver_bot_uuid: string }
export class UserApiError extends Error {
  constructor(message: string, public code = '', public unknownOutcome = false) { super(message); }
}

// This frontend exposes native BCS through its existing same-origin proxy.
export const GROUP_CREATION_HOST: GroupCreationHost = {
  userApiBaseUrl: '/bcnproxy/collaboration/user',
  groupUrlTemplate: '/bcn/chat/detail?id={groupId}&session={sessionId}&bot_uuid={driverBotId}',
  groupListUrl: '/bcn/chat/list?bot_uuid={humanActorId}',
};
export const BCS_API_BASE = '/bcnproxy';

function sameOrigin(path: string): string {
  const url = new URL(path, window.location.href);
  if (!['http:', 'https:'].includes(url.protocol) || url.origin !== window.location.origin
    || url.username || url.password || url.hash) throw new UserApiError('用户操作仅支持可信同源入口');
  return `${url.pathname}${url.search}`;
}

export function groupLink(host: GroupCreationHost, user: GroupUser, result?: GroupCreationResult): string {
  if (!result) return sameOrigin(host.groupListUrl.replace(/\{humanActorId\}/g, encodeURIComponent(`human_${user.user_id}`)));
  const template = host.groupUrlTemplate;
  if (!template.includes('{groupId}') || !template.includes('{sessionId}')) {
    throw new UserApiError('宿主群导航缺少群或会话参数');
  }
  return sameOrigin(template.replace(/\{groupId\}/g, encodeURIComponent(result.group_id))
    .replace(/\{sessionId\}/g, encodeURIComponent(result.initial_session_id))
    .replace(/\{driverBotId\}/g, encodeURIComponent(result.driver_bot_uuid)));
}

async function request(host: GroupCreationHost, path: string, signal: AbortSignal, user?: GroupUser, body?: unknown): Promise<any> {
  const url = sameOrigin(`${host.userApiBaseUrl.replace(/\/+$/, '')}${path}`);
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timer = window.setTimeout(abort, body === undefined ? 15_000 : 60_000);
  try {
    const response = await fetch(url, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      cache: 'no-store', redirect: 'error', signal: controller.signal,
      headers: { ...(user ? { 'X-BCS-Expected-User': user.user_id } : {}),
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const envelope = await response.json();
    if (!response.ok || ![20000, 20100].includes(envelope?.code)) {
      const code = envelope?.data?.error_code ?? '';
      // Only boundary rejections prove the create handler never ran.
      const rejected = ['unauthenticated', 'user_context_changed', 'browser_context_required'].includes(code)
        && [400, 401, 403].includes(response.status);
      throw new UserApiError(code === 'user_context_changed' ? '登录用户已变化，请重新配置'
        : response.status === 401 ? '请先登录后再发起协作'
        : typeof envelope?.message === 'string' ? envelope.message : `用户服务请求失败（HTTP ${response.status}）`,
      code, body !== undefined && !rejected);
    }
    if (!envelope.data || typeof envelope.data !== 'object') throw new Error('Invalid response');
    return envelope.data;
  } catch (error) {
    if (error instanceof UserApiError) throw error;
    throw new UserApiError(controller.signal.aborted ? '请求已取消或超时' : '用户服务未接通或响应格式无效', '', body !== undefined);
  } finally {
    window.clearTimeout(timer);
    signal.removeEventListener('abort', abort);
  }
}

export async function readGroupUser(host: GroupCreationHost, signal: AbortSignal): Promise<GroupUser> {
  const data = await request(host, '/me', signal);
  if (typeof data.user_id !== 'string' || !data.user_id || typeof data.display_name !== 'string') {
    throw new UserApiError('当前入口没有可信 Human 身份');
  }
  return data;
}

export async function readBotPage(host: GroupCreationHost, user: GroupUser, originator: string | null,
  name: string, offset: number, signal: AbortSignal): Promise<BotPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: '20', name });
  if (originator) params.set('purpose', 'collaboration');
  else params.set('kind', 'bot');
  const path = originator ? `/bots/${encodeURIComponent(originator)}/candidates` : '/bots/mine';
  const page = await request(host, `${path}?${params}`, signal, user);
  if (!Array.isArray(page.items) || !Number.isSafeInteger(page.total) || page.total < 0
    || page.offset !== offset || page.limit !== 20) throw new UserApiError('Bot 列表响应格式无效');
  const items = page.items.map((item: any) => originator ? item.bot : item);
  if (items.some((bot: any) => !bot || bot.kind !== 'bot' || typeof bot.bot_id !== 'string' || !bot.bot_id
    || typeof bot.name !== 'string' || typeof bot.status !== 'string' || typeof bot.reachability !== 'string')) {
    throw new UserApiError('Bot 列表响应格式无效');
  }
  return { ...page, items };
}

export function groupDefinitionError(result: CollaborationDefinitionValidationResult): string {
  if (result.warnings.some(warning => warning.code === 'VALIDATION_ONLY_FEATURE')) return '当前服务只支持校验和预览此流程，暂不能创建协作群';
  if (!result.valid || !result.graph) return '当前 YAML 未通过流程校验，不能发起协作';
  if (result.graph.nodes.some(node => node.assignee?.type === 'runtime_actor')) {
    return '当前 YAML 包含固定执行身份，请改为角色槽位后重新校验。';
  }
  if (result.graph.nodes.some(node => {
    const assignee = node.assignee;
    return assignee?.type === 'bot_binding' && !result.participants.some(p => p.binding === assignee.binding);
  })) {
    return '流程引用了未声明的角色';
  }
  return '';
}

/** Adapt the same modal parameters to the authenticated Human Group API. */
export function groupCreationBody(params: CreateGroupParams, validation?: CollaborationDefinitionValidationResult) {
  const strategy = params.group_strategy || 'chat';
  if (!params.originator || !params.driver_bot) throw new UserApiError('请选择发起方和群主 Bot');
  let collaboration: { strategy: 'chat'; delivery_policy: { bot_final_delivery: string } }
    | { strategy: 'manager_worker' }
    | { strategy: 'state_machine'; definition: { content_yaml: string }; participant_bindings: { binding: string; actor_ids: string[] }[] };
  if (strategy === 'state_machine') {
    if (!validation) throw new UserApiError('请先校验当前 YAML');
    const error = groupDefinitionError(validation);
    if (error) throw new UserApiError(error);
    if (!params.context?.trim()) throw new UserApiError('请输入协作目标');
    collaboration = { strategy, definition: { content_yaml: params.collaboration_definition_yaml! },
      participant_bindings: validation.participants.map(p => {
        const ids = params.participant_bindings?.[p.binding]?.bot_ids;
        if (!ids || ids.length !== 1 || !ids[0]) throw new UserApiError(`请选择“${p.display_name || p.binding}”的 Bot`);
        return { binding: p.binding, actor_ids: [...ids] };
      }) };
  } else if (strategy === 'manager_worker') collaboration = { strategy };
  else collaboration = { strategy, delivery_policy: { bot_final_delivery: params.routing_policy?.default_bot_final_delivery || 'send_to_driver' } };
  const otherRole = strategy === 'manager_worker' ? 'worker' : 'consultant';
  const participants = new Map(params.participants.map(p => [p.bot_uuid,
    { actor_id: p.bot_uuid, role: p.role || otherRole }]));
  if (!participants.has(params.originator)) participants.set(params.originator, { actor_id: params.originator, role: otherRole });
  participants.set(params.driver_bot, { actor_id: params.driver_bot, role: strategy === 'manager_worker' ? 'manager' : 'driver' });
  return { group_kind: 'normal', originator: params.originator, driver_bot_uuid: params.driver_bot,
    name: params.label, ...(params.context ? { context: params.context } : {}),
    ...(params.opening_message ? { opening_message: params.opening_message } : {}),
    ...(params.event_subscriptions?.length ? { event_subscriptions: params.event_subscriptions } : {}),
    participants: [...participants.values()], collaboration };
}

export async function createGroup(host: GroupCreationHost, user: GroupUser, body: ReturnType<typeof groupCreationBody>, signal: AbortSignal): Promise<GroupCreationResult> {
  const data = await request(host, '/groups', signal, user, body);
  if (typeof data.group_id !== 'string' || !data.group_id || typeof data.initial_session_id !== 'string' || !data.initial_session_id) {
    throw new UserApiError('服务未返回完整的群和初始会话标识', '', true);
  }
  return { group_id: data.group_id, initial_session_id: data.initial_session_id, driver_bot_uuid: body.driver_bot_uuid };
}

export interface CollaborationDefinitionReference {
  schemaVersion: 2;
  draftId: string;
  revision: number;
  readToken: string;
}
export interface StoredDefinition {
  draftId: string;
  revision: number;
  definitionYaml: string;
  title?: string;
  contentHash: string;
  sizeBytes: number;
}

export class DraftReadError extends Error {}

function validUnicode(value: string): boolean {
  // TextEncoder replaces lone surrogates; reject them before byte/hash checks.
  return [...value].every((character) => {
    const point = character.codePointAt(0)!;
    return point < 0xd800 || point > 0xdfff;
  });
}

export function decodeCollaborationDefinitionReference(input: unknown): CollaborationDefinitionReference {
  const value = input as Record<string, unknown> | null;
  const fields = ['schemaVersion', 'draftId', 'revision', 'readToken'];
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== 4 || Object.keys(value).some((key) => !fields.includes(key))
    || value.schemaVersion !== 2
    || typeof value.draftId !== 'string'
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.draftId)
    || typeof value.revision !== 'number' || !Number.isSafeInteger(value.revision) || value.revision < 1
    || typeof value.readToken !== 'string' || !/^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/.test(value.readToken)) {
    throw new DraftReadError('协作版本引用无效');
  }
  return value as unknown as CollaborationDefinitionReference;
}

async function definitionRequest(url: string, signal: AbortSignal, options: RequestInit = {}): Promise<any> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal.aborted) controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  const timeout = window.setTimeout(abort, 15_000);
  try {
    const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      ...options, signal: controller.signal });
    if (response.status === 404 || response.status === 401 || response.status === 403) {
      throw new DraftReadError(options.method === 'POST' ? '协作定义校验服务不可用或无访问权限'
        : '引用不可用：版本不存在、分享已撤销或读取凭据不匹配');
    }
    if (!response.ok) throw new DraftReadError(`协作定义服务请求失败（HTTP ${response.status}）`);
    const result = await response.json();
    if (result && typeof result === 'object' && 'code' in result) {
      if (result.code !== 20000 || !result.data || typeof result.data !== 'object') {
        throw new DraftReadError('协作定义服务响应格式无效');
      }
      return result.data;
    }
    return result;
  } catch (error) {
    if (error instanceof DraftReadError) throw error;
    throw new DraftReadError(controller.signal.aborted ? '协作定义请求已取消或超时' : '协作定义服务网络错误或响应格式无效');
  } finally {
    window.clearTimeout(timeout);
    signal.removeEventListener('abort', abort);
  }
}

export async function readStoredDefinition(baseUrl: string, reference: CollaborationDefinitionReference, signal: AbortSignal): Promise<StoredDefinition> {
  const r = await definitionRequest(`${baseUrl}/collaboration/drafts/${reference.draftId}/revisions/${reference.revision}`, signal,
    { headers: { 'X-BCS-Draft-Read-Token': reference.readToken } });
  if (!r || r.draft_id !== reference.draftId || r.revision !== reference.revision
    || typeof r.definition_yaml !== 'string' || !validUnicode(r.definition_yaml) || !Number.isSafeInteger(r.size_bytes)
    || r.size_bytes < 0 || r.size_bytes > 65_536
    || typeof r.content_hash !== 'string' || !/^[0-9a-f]{64}$/.test(r.content_hash)
    || (r.title !== null && r.title !== undefined && (typeof r.title !== 'string' || !validUnicode(r.title) || !r.title.trim() || [...r.title].length > 120))) {
    throw new DraftReadError('原文完整性检查失败：版本响应格式不匹配');
  }
  const bytes = new TextEncoder().encode(r.definition_yaml);
  if (bytes.length !== r.size_bytes) throw new DraftReadError('原文完整性检查失败：字节数不匹配');
  let hash: string;
  try {
    hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
      .map((v) => v.toString(16).padStart(2, '0')).join('');
  } catch {
    throw new DraftReadError('当前浏览器无法核对原文 SHA-256');
  }
  if (hash !== r.content_hash) throw new DraftReadError('原文完整性检查失败：SHA-256 不匹配');
  return { draftId: r.draft_id, revision: r.revision, definitionYaml: r.definition_yaml,
    title: r.title ?? undefined, contentHash: hash, sizeBytes: bytes.length };
}

function decodeValidation(value: any): CollaborationDefinitionValidationResult {
  if (!value || typeof value.valid !== 'boolean' || !value.summary
    || !Array.isArray(value.summary.initial_nodes) || !Array.isArray(value.participants)
    || value.participants.some((p: any) => !p || typeof p.binding !== 'string' || !p.binding)
    || (value.valid && (!value.graph || !Array.isArray(value.graph.nodes) || !Array.isArray(value.graph.edges)))) {
    throw new DraftReadError('协作定义校验响应格式无效');
  }
  return { ...value, errors: value.errors ?? [], warnings: value.warnings ?? [] };
}

export async function validateGroupDefinition(definitionYaml: string, signal: AbortSignal): Promise<CollaborationDefinitionValidationResult> {
  return decodeValidation(await definitionRequest(`${BCS_API_BASE}/collaboration/definitions/validate`, signal,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ definition_yaml: definitionYaml }) }));
}

export interface OpenCreateGroupOptions { draft?: CollaborationDefinitionReference }

export function decodeOpenCreateGroupOptions(input: unknown = {}): OpenCreateGroupOptions {
  if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).some(key => key !== 'draft')) {
    throw new Error('建群入口参数无效');
  }
  if (!('draft' in input)) return {};
  return { draft: decodeCollaborationDefinitionReference(input.draft) };
}

export function toBindingBot(bot: GroupBot) {
  return { bot_uuid: bot.bot_id, bot_name: bot.name, summary: bot.summary,
    status: bot.status, is_online: bot.status === 'online', avatar_url: undefined,
    visibility: 'protected' as const, is_friend: null,
    dynamic_status: { status: bot.reachability === 'reachable' ? 'active' as const : 'offline' as const } };
}

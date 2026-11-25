import type { CreateGroupParams } from '@/pages/GroupChat/types';
import * as BcnController from './BcnController';
import type { CollaborationDefinitionValidationResponse } from './BcnController';
import { getActorList } from './ActorController';
import { computeIsOnline, type BotNetworkInfo, type BotVisibility } from '@/stores/botNetworkStore';

export type CollaborationDefinitionValidationResult = CollaborationDefinitionValidationResponse & {
  participants: NonNullable<CollaborationDefinitionValidationResponse['participants']>;
  warnings: NonNullable<CollaborationDefinitionValidationResponse['warnings']>;
};

export interface GroupCreationHost {
  groupUrlTemplate: string;
  groupListUrl: string;
}
export interface GroupUser { user_id: string; display_name: string }
export interface GroupBot {
  bot_id: string; name: string; kind: 'bot'; created_by?: string;
  status: string; reachability: string; summary?: string; avatar_url?: string;
  visibility?: BotVisibility; is_friend?: boolean | null;
}
export interface BotPage { items: GroupBot[]; total: number; offset: number; limit: number }
/** Created group identifiers plus the submitted driver used for navigation. */
export interface GroupCreationResult { group_id: string; initial_session_id: string; driver_bot_uuid: string }
export class UserApiError extends Error {
  constructor(message: string, public code = '', public unknownOutcome = false) { super(message); }
}

// Navigation stays in the host; API calls use the ordinary BCN request client.
export const GROUP_CREATION_HOST: GroupCreationHost = {
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

export function groupLink(host: GroupCreationHost, user: GroupUser | undefined, result?: GroupCreationResult): string {
  if (!result) return sameOrigin(host.groupListUrl.replace(/\{humanActorId\}/g, encodeURIComponent(user ? `human_${user.user_id}` : '')));
  const template = host.groupUrlTemplate;
  if (!template.includes('{groupId}') || !template.includes('{sessionId}')) {
    throw new UserApiError('宿主群导航缺少群或会话参数');
  }
  return sameOrigin(template.replace(/\{groupId\}/g, encodeURIComponent(result.group_id))
    .replace(/\{sessionId\}/g, encodeURIComponent(result.initial_session_id))
    .replace(/\{driverBotId\}/g, encodeURIComponent(result.driver_bot_uuid)));
}

/** Reuse the same owned, onboarded Bots and presentation data as the top tabs. */
export function groupOriginatorBots(bots: BotNetworkInfo[]): GroupBot[] {
  return bots.filter(bot => bot.actor_kind !== 'human' && !bot.bot_uuid.startsWith('human_')
    && bot.visibility !== 'offline').map(bot => ({
    bot_id: bot.bot_uuid, name: bot.bot_name, kind: 'bot', summary: bot.summary,
    status: bot.status || (bot.is_online ? 'online' : 'offline'),
    reachability: computeIsOnline(bot) ? 'reachable' : 'unreachable',
    avatar_url: bot.avatar_url, visibility: bot.visibility,
  }));
}

export async function readBotPage(originator: string, name: string, offset: number, signal: AbortSignal): Promise<BotPage> {
  const page = await getActorList({ current_bot_uuid: originator, cooperatable_only: true,
    page_no: offset / 20 + 1, page_size: 20, name }, { signal });
  if (!Array.isArray(page.bots) || !Number.isSafeInteger(page.total) || page.total < 0) {
    throw new UserApiError('Bot 列表响应格式无效');
  }
  const items: GroupBot[] = page.bots.map(bot => ({
    bot_id: bot.bot_uuid, name: bot.capabilities.name || bot.bot_uuid, kind: 'bot',
    summary: bot.capabilities.description, status: bot.status, visibility: bot.visibility,
    reachability: computeIsOnline(bot) ? 'reachable' : 'unreachable', is_friend: bot.is_friend,
  }));
  return { items, total: page.total, offset, limit: 20 };
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

/** Validate the current form, preserving the ordinary /groups wire contract. */
export function groupCreationBody(params: CreateGroupParams, validation?: CollaborationDefinitionValidationResult): CreateGroupParams {
  const strategy = params.group_strategy || 'chat';
  if (!params.originator || !params.driver_bot) throw new UserApiError('请选择发起方和群主 Bot');
  if (strategy === 'state_machine') {
    if (!validation) throw new UserApiError('请先校验当前 YAML');
    const error = groupDefinitionError(validation);
    if (error) throw new UserApiError(error);
    if (!params.context?.trim()) throw new UserApiError('请输入协作目标');
    for (const participant of validation.participants) {
      const ids = params.participant_bindings?.[participant.binding]?.bot_ids;
      if (!ids || ids.length !== 1 || !ids[0]) throw new UserApiError(`请选择“${participant.display_name || participant.binding}”的 Bot`);
    }
  }
  const participants = new Map(params.participants.map(p => [p.bot_uuid, p]));
  for (const bot_uuid of [params.originator, params.driver_bot]) {
    if (!participants.has(bot_uuid)) participants.set(bot_uuid, { bot_uuid });
  }
  return { ...params, group_kind: 'normal', group_strategy: strategy,
    participants: [...participants.values()], create_initial_session: true,
    ...(strategy === 'state_machine' ? { start_initial_run: true } : {}) };
}

export async function createGroup(body: CreateGroupParams, signal: AbortSignal): Promise<GroupCreationResult> {
  try {
    const data = await BcnController.createGroup(body, { signal, timeout: 60_000 });
    if (data?.error) throw new UserApiError(data.error, '', true);
    const session = data?.initial_session_id || data?.session_id;
    if (typeof data?.id !== 'string' || !data.id || typeof session !== 'string' || !session) {
      throw new UserApiError('服务未返回完整的群和初始会话标识', '', true);
    }
    return { group_id: data.id, initial_session_id: session, driver_bot_uuid: body.driver_bot };
  } catch (error) {
    if (error instanceof UserApiError) throw error;
    // A failed response can occur after the group has been persisted; never retry automatically.
    throw new UserApiError(error instanceof Error ? error.message : '建群请求失败', '', true);
  }
}

export interface CollaborationDefinitionReference {
  schemaVersion: 2;
  draftId: string;
  revision: number;
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
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || value.schemaVersion !== 2
    || typeof value.draftId !== 'string'
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.draftId)
    || typeof value.revision !== 'number' || !Number.isSafeInteger(value.revision) || value.revision < 1) {
    throw new DraftReadError('协作版本引用无效');
  }
  // Normalize historical cards too; extra fields are never forwarded.
  return { schemaVersion: 2, draftId: value.draftId, revision: value.revision };
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
        : '引用不可用：版本不存在或分享已撤销');
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
  const r = await definitionRequest(`${baseUrl}/collaboration/drafts/${reference.draftId}/revisions/${reference.revision}`, signal);
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
  return decodeValidation(await BcnController.validateCollaborationDefinitionYaml(
    { definition_yaml: definitionYaml }, { signal, timeout: 15_000 }));
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
    status: bot.status, is_online: bot.reachability === 'reachable', avatar_url: bot.avatar_url,
    visibility: bot.visibility || 'protected' as const, is_friend: bot.is_friend ?? null,
    dynamic_status: { status: bot.reachability === 'reachable' ? 'active' as const : 'offline' as const } };
}

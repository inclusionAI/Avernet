import { CollaborationDefinitionSnapshotError, decodeCollaborationDefinitionSnapshot } from './snapshot';
import { decodeCollaborationDefinitionValidationResult } from './validation';
import type { CollaborationDefinitionSnapshot, CollaborationDefinitionValidationResult } from './types';
import { collaborationApiUrl } from './api';

export interface CollaborationDefinitionReference {
  schemaVersion: 2;
  draftId: string;
  revision: number;
  readToken: string;
}
export type CollaborationDefinitionPayload = CollaborationDefinitionSnapshot | CollaborationDefinitionReference;

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
    throw new CollaborationDefinitionSnapshotError('invalid_field', '协作版本引用无效');
  }
  return value as unknown as CollaborationDefinitionReference;
}

export function decodeCollaborationDefinitionPayload(input: unknown): CollaborationDefinitionPayload {
  if (input && typeof input === 'object' && 'schemaVersion' in input && input.schemaVersion === 2) {
    return decodeCollaborationDefinitionReference(input);
  }
  return decodeCollaborationDefinitionSnapshot(input);
}

export function definitionVersionKey(payload: CollaborationDefinitionPayload): string {
  return payload.schemaVersion === 1 ? payload.snapshotId : `${payload.draftId}:${payload.revision}`;
}

async function request(baseUrl: string, reference: CollaborationDefinitionReference, validate: boolean, signal: AbortSignal): Promise<any> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal.aborted) controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  const timeout = window.setTimeout(abort, 15_000);
  try {
    const response = await fetch(collaborationApiUrl(baseUrl, `drafts/${reference.draftId}/revisions/${reference.revision}${validate ? '/validate' : ''}`), {
      method: validate ? 'POST' : 'GET',
      headers: { 'X-BCS-Draft-Read-Token': reference.readToken },
      credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      signal: controller.signal,
    });
    if (response.status === 404 || response.status === 401 || response.status === 403) {
      throw new DraftReadError('引用不可用：版本不存在、分享已撤销或读取凭据不匹配');
    }
    if (!response.ok) throw new DraftReadError(`版本服务请求失败（HTTP ${response.status}）`);
    return await response.json();
  } catch (error) {
    if (error instanceof DraftReadError) throw error;
    throw new DraftReadError(controller.signal.aborted ? '版本请求已取消或超时' : '版本服务网络错误或响应格式无效');
  } finally {
    window.clearTimeout(timeout);
    signal.removeEventListener('abort', abort);
  }
}

export async function readStoredDefinition(baseUrl: string, reference: CollaborationDefinitionReference, signal: AbortSignal): Promise<StoredDefinition> {
  const r = await request(baseUrl, reference, false, signal);
  if (!r || r.draft_id !== reference.draftId || r.revision !== reference.revision
    || typeof r.definition_yaml !== 'string' || !validUnicode(r.definition_yaml) || !Number.isSafeInteger(r.size_bytes)
    || r.size_bytes < 0 || r.size_bytes > 65_536
    || typeof r.content_hash !== 'string' || !/^[0-9a-f]{64}$/.test(r.content_hash)
    || (r.title != null && (typeof r.title !== 'string' || !validUnicode(r.title) || !r.title.trim() || [...r.title].length > 120))) {
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

export async function validateStoredDefinition(baseUrl: string, reference: CollaborationDefinitionReference, source: StoredDefinition, signal: AbortSignal): Promise<{
  result: CollaborationDefinitionValidationResult; checkedAt: number; validator: string;
}> {
  const r = await request(baseUrl, reference, true, signal);
  if (!r || r.draft_id !== source.draftId || r.revision !== source.revision || r.content_hash !== source.contentHash
    || typeof r.validator !== 'string' || !r.validator
    || typeof r.validated_at !== 'string' || !Number.isFinite(Date.parse(r.validated_at))) {
    throw new DraftReadError('固定版本校验响应无效或与原文不匹配');
  }
  return { result: decodeCollaborationDefinitionValidationResult(r.validation),
    checkedAt: Date.parse(r.validated_at), validator: r.validator };
}

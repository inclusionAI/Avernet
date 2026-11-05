import {
  decodeOpenCreateGroupOptions,
  type CollaborationDraft,
  type CollaborationDraftReference,
} from '@/domain/collaborationDraft';
import { readCollaborationDraft } from '@/services/backendApi/collaboration/collaborationDraftController';
import type { DomainResult } from './identityService';

function validUnicode(value: string): boolean {
  return [...value].every((character) => {
    const point = character.codePointAt(0)!;
    return point < 0xd800 || point > 0xdfff;
  });
}

export async function loadCollaborationDraft(
  reference: CollaborationDraftReference,
  signal?: AbortSignal,
): Promise<DomainResult<CollaborationDraft>> {
  try {
    const normalized = decodeOpenCreateGroupOptions({ draft: reference }).draft!;
    const { data } = await readCollaborationDraft(normalized, signal);
    if (
      !data ||
      data.draft_id !== normalized.draftId ||
      data.revision !== normalized.revision ||
      typeof data.definition_yaml !== 'string' ||
      !validUnicode(data.definition_yaml) ||
      !Number.isSafeInteger(data.size_bytes) ||
      data.size_bytes < 0 ||
      data.size_bytes > 65_536 ||
      typeof data.content_hash !== 'string' ||
      !/^[0-9a-f]{64}$/.test(data.content_hash) ||
      (data.title !== null &&
        data.title !== undefined &&
        (typeof data.title !== 'string' ||
          !validUnicode(data.title) ||
          !data.title.trim() ||
          [...data.title].length > 120))
    ) {
      throw new Error('原文完整性检查失败：版本响应格式不匹配');
    }
    const bytes = new TextEncoder().encode(data.definition_yaml);
    if (bytes.length !== data.size_bytes) throw new Error('原文完整性检查失败：字节数不匹配');
    let digest: ArrayBuffer;
    try {
      digest = await crypto.subtle.digest('SHA-256', bytes);
    } catch {
      throw new Error('当前浏览器无法核对原文 SHA-256');
    }
    const hash = [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('');
    if (hash !== data.content_hash) throw new Error('原文完整性检查失败：SHA-256 不匹配');
    return {
      ok: true,
      data: { reference: normalized, definitionYaml: data.definition_yaml, title: data.title ?? undefined },
    };
  } catch (error) {
    const status = (error as { status?: number }).status;
    const message =
      status === 404 ? '协作版本不存在或分享已撤销' : error instanceof Error ? error.message : '读取协作版本失败';
    return { ok: false, error: { code: 'DRAFT_READ_FAILED', friendlyMessage: message, canRetry: true } };
  }
}

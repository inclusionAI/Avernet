import type { ParticipantView } from '@/domain/collaboration';
import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import type { SessionFileDto, SessionFileStatus } from '@/services/backendApi/collaboration/sessionFileController';
import {
  buildSessionFileContentUrl,
  completeSessionFile,
  deleteSessionFile,
  getSessionFileContentBlob,
  listSessionFiles,
  prepareSessionFile,
  shareSessionFile,
  uploadBytes as uploadBytesApi,
  uploadSessionFileContent,
} from '@/services/backendApi/collaboration/sessionFileController';
import { resolveGroupGatewayOrigin } from './groupChatProviderHelpers';
import type { DomainError, DomainResult } from './identityService';
import { resolveOwnerDisplayName, type OwnerNameSource } from './sessionFileUtils';

export interface SessionFileView {
  fileId: string;
  sessionId: string;
  name: string;
  mimeType: string;
  size: number;
  status: SessionFileStatus;
  ownerActorId: string;
  ownerKind: 'human' | 'bot';
  ownerName: string;
  sha256: string | null;
  createdAt: number;
  updatedAt: number;
}

export interface PrepareUploadResult {
  fileId: string;
  uploadUrl?: string;
  uploadId?: string;
  parts?: Array<Record<string, unknown>>;
}

export interface SessionFileShareView {
  shareUrl: string;
  expiresAt: number;
}

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

function normalizeShareUrl(url: string): string {
  return url.replace(/\/openapi\/v1\//g, '/api/v1/');
}

function mapFile(dto: SessionFileDto, participants?: ParticipantView[]): SessionFileView {
  const sources: OwnerNameSource[] = (participants ?? []).map((p) => ({ actorId: p.actorId, name: p.name }));
  return {
    fileId: dto.file_id,
    sessionId: dto.session_id,
    name: dto.file_name,
    mimeType: dto.mime_type,
    size: dto.size,
    status: dto.status,
    ownerActorId: dto.owner.actor_id,
    ownerKind: dto.owner.actor_kind === 'human' ? 'human' : 'bot',
    ownerName: resolveOwnerDisplayName(dto.owner, sources),
    sha256: dto.sha256,
    createdAt: dto.created_at,
    updatedAt: dto.updated_at,
  };
}

export const sessionFileService = {
  /** 构造可被 <img>/<iframe> 直接消费的会话文件内容地址。部署态直连网关，本地保持同源代理。 */
  buildContentUrl(sessionId: string, fileId: string): string {
    const path = buildSessionFileContentUrl(sessionId, fileId, true);
    const origin = resolveGroupGatewayOrigin();
    return origin ? `${origin}${path}` : path;
  },

  /** 构造下载地址：顶层导航跟随网关 303/Oss 签名链，大文件不入 JS 内存。 */
  buildDownloadUrl(sessionId: string, fileId: string): string {
    const path = buildSessionFileContentUrl(sessionId, fileId, false);
    const origin = resolveGroupGatewayOrigin();
    return origin ? `${origin}${path}` : path;
  },

  /** 拉取文件内容字节（经网关鉴权，返回 Blob 供前端下载/预览）。 */
  async fetchContentBlob(sessionId: string, fileId: string): Promise<DomainResult<Blob>> {
    try {
      const blob = await getSessionFileContentBlob(sessionId, fileId);
      return { ok: true, data: blob };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 403 || status === 404) {
        return { ok: false, error: toDomainError('SESSION_FILE_DOWNLOAD_FORBIDDEN', '无权访问该文件。') };
      }
      return { ok: false, error: toDomainError('SESSION_FILE_DOWNLOAD_FAILED', '下载文件失败，请稍后重试。') };
    }
  },

  async loadFiles(
    sessionId: string,
    participants?: ParticipantView[],
    opts: { status?: SessionFileStatus; limit?: number; offset?: number } = {},
  ): Promise<DomainResult<{ items: SessionFileView[]; total: number }>> {
    try {
      const resp = await listSessionFiles(sessionId, opts);
      const data = resp.data ?? { items: [], total: 0 };
      return {
        ok: true,
        data: { items: (data.items ?? []).map((f) => mapFile(f, participants)), total: data.total ?? 0 },
      };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 403 || status === 404) {
        return { ok: false, error: toDomainError('SESSION_FILE_FORBIDDEN', '无权访问该会话文件，请刷新后重试。') };
      }
      return { ok: false, error: toDomainError('SESSION_FILE_LIST_FAILED', '加载会话文件失败，请稍后重试。') };
    }
  },

  /**
   * 批量解析 actor（上传者）的展示名。文件接口仅返回 owner.actor_id（如 human_327325 / bot:工号），
   * 需经 POST /openapi/v1/collaboration/bots/query 用 {bot_ids} 反查 name。返回 {actorId: name} 映射；
   * 后端返回的 bot 列表与请求 id 列表非一一对应，按 bot_id 匹配，未返回的 id 不出现在映射中。
   * 失败时返回空映射（调用方回退到 actor_id 兜底展示）。
   */
  async resolveActorNames(actorIds: string[]): Promise<Record<string, string>> {
    const unique = [...new Set(actorIds.filter((id) => id && id.trim()))];
    if (unique.length === 0) return {};
    try {
      const resp = await queryCollaborationBots({ bot_ids: unique });
      const items = resp.data?.items ?? [];
      const map: Record<string, string> = {};
      for (const item of items) {
        if (item.bot_id && item.name) map[item.bot_id] = item.name;
      }
      return map;
    } catch {
      return {};
    }
  },

  async prepareUpload(
    sessionId: string,
    body: { file_name: string; size: number; mime_type: string },
  ): Promise<DomainResult<PrepareUploadResult>> {
    try {
      const resp = await prepareSessionFile(sessionId, body);
      const d = resp.data;
      if (!d?.file_id) {
        return { ok: false, error: toDomainError('SESSION_FILE_PREPARE_FAILED', '准备上传失败，请稍后重试。') };
      }
      return {
        ok: true,
        data: { fileId: d.file_id, uploadUrl: d.upload_url, uploadId: d.upload_id, parts: d.parts },
      };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 413) return { ok: false, error: toDomainError('SESSION_FILE_TOO_LARGE', '文件超过大小上限。') };
      if (status === 400) return { ok: false, error: toDomainError('SESSION_FILE_INVALID', '文件名或参数非法。') };
      return { ok: false, error: toDomainError('SESSION_FILE_PREPARE_FAILED', '准备上传失败，请稍后重试。') };
    }
  },

  /** 上传单文件或单个分片字节到网关 content 路由。 */
  uploadContent(
    sessionId: string,
    fileId: string,
    blob: Blob,
    opts: {
      mime?: string;
      signal?: AbortSignal;
      onProgress?: (loaded: number, total: number) => void;
      part?: number;
    } = {},
  ): Promise<void> {
    return uploadSessionFileContent(sessionId, fileId, blob, opts);
  },

  /** 上传字节到任意目标地址（presigned 直传时用，去除站点凭证）。 */
  uploadBytes(
    url: string,
    blob: Blob,
    opts: { mime?: string; signal?: AbortSignal; onProgress?: (loaded: number, total: number) => void } = {},
  ): Promise<void> {
    return uploadBytesApi(url, blob, opts);
  },

  async completeUpload(sessionId: string, fileId: string): Promise<DomainResult<SessionFileView>> {
    try {
      const resp = await completeSessionFile(sessionId, fileId);
      return { ok: true, data: mapFile(resp.data!) };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 422) return { ok: false, error: toDomainError('SESSION_FILE_INCOMPLETE', '上传未完成，请重试。') };
      return { ok: false, error: toDomainError('SESSION_FILE_COMPLETE_FAILED', '文件组装失败，请重试。') };
    }
  },

  async removeFile(sessionId: string, fileId: string): Promise<DomainResult<null>> {
    try {
      await deleteSessionFile(sessionId, fileId);
      return { ok: true, data: null };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 403 || status === 404) {
        return { ok: false, error: toDomainError('SESSION_FILE_DELETE_FORBIDDEN', '无权删除该文件。') };
      }
      return { ok: false, error: toDomainError('SESSION_FILE_DELETE_FAILED', '删除文件失败，请稍后重试。') };
    }
  },

  async shareFile(sessionId: string, fileId: string): Promise<DomainResult<string>> {
    try {
      const resp = await shareSessionFile(sessionId, fileId);
      if (!resp.data?.share_url) {
        return { ok: false, error: toDomainError('SESSION_FILE_SHARE_FAILED', '生成分享链接失败。') };
      }
      return { ok: true, data: normalizeShareUrl(resp.data.share_url) };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 422)
        return { ok: false, error: toDomainError('SESSION_FILE_NOT_READY', '文件尚未就绪，无法分享。') };
      return { ok: false, error: toDomainError('SESSION_FILE_SHARE_FAILED', '生成分享链接失败，请稍后重试。') };
    }
  },

  /** 生成会话文件免鉴权分享链接，并带回过期时间（用于 chat.send 附件 wire 形态）。 */
  async shareFileWithExpiry(
    sessionId: string,
    fileId: string,
    ttlSeconds = 3600,
  ): Promise<DomainResult<SessionFileShareView>> {
    try {
      const resp = await shareSessionFile(sessionId, fileId, { ttl_seconds: ttlSeconds });
      if (!resp.data?.share_url) {
        return { ok: false, error: toDomainError('SESSION_FILE_SHARE_FAILED', '生成分享链接失败。') };
      }
      return {
        ok: true,
        data: { shareUrl: normalizeShareUrl(resp.data.share_url), expiresAt: (resp.data.expires_at ?? 0) * 1000 },
      };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 422)
        return { ok: false, error: toDomainError('SESSION_FILE_NOT_READY', '文件尚未就绪，无法分享。') };
      return { ok: false, error: toDomainError('SESSION_FILE_SHARE_FAILED', '生成分享链接失败，请稍后重试。') };
    }
  },
};

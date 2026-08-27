/** BotSessionFileService — 单聊会话文件业务编排
 *
 * 上传生命周期: createUploadIntents → 直传字节(SINGLE/MULTIPART)→ completeUpload →
 *   轮询 getMaterializeStatus 到 ready/failed。列表/删除/下载走 controller。
 * 复用 sessionFileUtils 白名单与格式化。
 */
import {
  buildBotSessionFileContentUrl,
  completeUpload,
  createUploadIntents,
  deleteFile,
  getContentBlob,
  getMaterializeStatus,
  listReady as listReadyApi,
  type BotRequestParams,
  type BotSessionFileDto,
  type UploadIntentDto,
} from '@/services/backendApi/bots/botSessionFileController';
import type { BotSessionFileView } from '@/stores/botSessionFileStore';
import { fetchExternalDownloadUrl } from './botSessionFileDownload';
import { directUpload, type UploadIntentView } from './botSessionFileUpload';
import { resolveUserId } from './botSessionService';
import { resolveGroupGatewayOrigin } from './groupChatProviderHelpers';
import type { DomainError, DomainResult } from './identityService';
import { isAllowedFileExt } from './sessionFileUtils';
export { isLargeBotSessionFile } from './botSessionFileDownload';
export type { UploadIntentView } from './botSessionFileUpload';
export type { BotSessionFileView };

export type { BotSessionFileStatus } from '@/services/backendApi/bots/botSessionFileController';

export interface BotSessionFileListResult {
  items: BotSessionFileView[];
  total: number;
}

const POLL_INTERVAL = 1500;
const POLL_DEADLINE = 3_600_000;

function toDomainError(err: unknown, fallback: string): DomainError {
  const msg = err instanceof Error ? err.message : fallback;
  const status = (err as { status?: number })?.status;
  return {
    code: status ? String(status) : 'bot_session_file_error',
    friendlyMessage: msg || fallback,
    canRetry: false,
  };
}

function mapView(dto: BotSessionFileDto): BotSessionFileView {
  return {
    resourceId: dto.resource_id,
    displayName: dto.display_name,
    status: dto.status,
    sizeBytes: dto.size_bytes,
    errorCode: dto.error_code,
  };
}

function mapIntent(dto: UploadIntentDto): UploadIntentView {
  return {
    resourceId: dto.resource_id,
    transferId: dto.transfer_id,
    uploadType: dto.upload_type,
    httpMethod: dto.http_method,
    uploadUrl: dto.upload_url,
    partSize: dto.part_size,
    partCount: dto.part_count,
    parts: dto.parts,
    expiresAt: dto.expires_at,
  };
}

function buildParams(userId: string, ownerId?: string, stage?: 'draft' | 'verify' | 'online'): BotRequestParams {
  return {
    user_id: resolveUserId(userId),
    ...(ownerId ? { owner_id: ownerId } : {}),
    ...(stage ? { stage } : {}),
  };
}

/** 计算客户端 SHA-256 hex(可选,用于防篡改;失败降级 undefined)。 */
async function sha256Hex(file: File): Promise<string | undefined> {
  try {
    const buf = await file.arrayBuffer();
    const digest = await crypto.subtle.digest('SHA-256', buf);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  } catch {
    return undefined;
  }
}

const sleep = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(() => resolve(), ms);
  });

export const botSessionFileService = {
  /** 校验文件:白名单 + 数量上限。 */
  validateFiles(files: File[], maxBatch: number): string | null {
    if (files.length > maxBatch) return `单次最多上传 ${maxBatch} 个文件`;
    const unsupported = files.find((f) => !isAllowedFileExt(f.name));
    if (unsupported) return `「${unsupported.name}」类型不支持`;
    return null;
  },

  /** 上传单个文件:申请凭证 → 直传字节 → complete → 轮询物化到 ready/failed。 */
  async uploadOne(
    botId: string,
    sessionId: string,
    userId: string,
    file: File,
    opts: {
      ownerId?: string;
      stage?: 'draft' | 'verify' | 'online';
      signal?: AbortSignal;
      onProgress?: (loaded: number, total: number) => void;
    },
  ): Promise<DomainResult<BotSessionFileView>> {
    const params = buildParams(userId, opts.ownerId, opts.stage);
    try {
      const hash = await sha256Hex(file);
      const intents = await createUploadIntents(botId, sessionId, params, {
        files: [{ filename: file.name, size_bytes: file.size, ...(hash ? { content_hash: hash } : {}) }],
      });
      const intent = mapIntent(intents.files[0]);

      await directUpload(intent, file, opts.signal ?? new AbortController().signal, opts.onProgress);

      const completed = await completeUpload(botId, sessionId, params, {
        resource_id: intent.resourceId,
        transfer_id: intent.transferId,
      });

      if (completed.status === 'ready') return { ok: true, data: mapView(completed) };
      if (completed.status === 'device_sync_failed') {
        return { ok: false, error: toDomainError({ status: 422 }, '文件物化失败') };
      }

      // 轮询物化状态到 ready / device_sync_failed
      const deadline = Date.now() + POLL_DEADLINE;
      while (Date.now() < deadline) {
        await sleep(POLL_INTERVAL);
        const status = await getMaterializeStatus(botId, sessionId, intent.resourceId, params);
        if (status.status === 'ready') return { ok: true, data: mapView(status) };
        if (status.status === 'device_sync_failed' || status.status === 'deleted') {
          return {
            ok: false,
            error: toDomainError(
              { status: 422 },
              status.error_code ? `物化失败: ${status.error_code}` : '文件物化失败',
            ),
          };
        }
      }
      return { ok: false, error: toDomainError({ status: 408 }, '文件物化超时') };
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return { ok: false, error: { code: 'aborted', friendlyMessage: '已取消', canRetry: false } };
      }
      return { ok: false, error: toDomainError(err, '上传失败') };
    }
  },

  async loadReady(
    botId: string,
    sessionId: string,
    userId: string,
    ownerId?: string,
  ): Promise<DomainResult<BotSessionFileListResult>> {
    try {
      const data = await listReadyApi(botId, sessionId, buildParams(userId, ownerId));
      const items = (data.files ?? []).map(mapView);
      return { ok: true, data: { items, total: items.length } };
    } catch (err) {
      return { ok: false, error: toDomainError(err, '加载文件列表失败') };
    }
  },

  /** 构造会话文件网关内容 URL。 */
  resolveContentUrl(
    botId: string,
    sessionId: string,
    resourceId: string,
    userId: string,
    ownerId?: string,
    disposition?: 'inline' | 'attachment',
  ): string {
    const path = buildBotSessionFileContentUrl(botId, sessionId, resourceId, buildParams(userId, ownerId), disposition);
    const origin = resolveGroupGatewayOrigin();
    return origin ? `${origin}${path}` : path;
  },

  /** 大文件先经 data-plane 轮询拿到 external_url，再交给浏览器直下。 */
  async resolveExternalDownloadUrl(
    botId: string,
    sessionId: string,
    resourceId: string,
    userId: string,
    ownerId?: string,
  ): Promise<string> {
    const contentUrl = buildBotSessionFileContentUrl(
      botId,
      sessionId,
      resourceId,
      buildParams(userId, ownerId),
      'attachment',
    );
    return fetchExternalDownloadUrl(contentUrl);
  },

  async remove(
    botId: string,
    sessionId: string,
    resourceId: string,
    userId: string,
    ownerId?: string,
  ): Promise<DomainResult<boolean>> {
    try {
      await deleteFile(botId, sessionId, resourceId, buildParams(userId, ownerId));
      return { ok: true, data: true };
    } catch (err) {
      return { ok: false, error: toDomainError(err, '删除失败') };
    }
  },

  async fetchBlob(
    botId: string,
    sessionId: string,
    resourceId: string,
    userId: string,
    ownerId?: string,
    disposition?: 'inline' | 'attachment',
  ): Promise<DomainResult<Blob>> {
    try {
      const blob = await getContentBlob(botId, sessionId, resourceId, buildParams(userId, ownerId), disposition);
      return { ok: true, data: blob };
    } catch (err) {
      return { ok: false, error: toDomainError(err, '下载失败') };
    }
  },
};

/** BotSessionFileController — 单聊会话文件 OpenAPI 控制器
 *
 * 后端契约: ~/Desktop/session-files-api.zh-CN.md (2026-08-19)
 * 公共前缀: /openapi/v1/bots/{bot_id}/sessions/{session_id}/files
 *
 * 生命周期: upload-intents → 二进制直传 upload_url → upload-complete →
 *   轮询 materialize-status / pending → ready 后可引用/下载;DELETE 删除。
 *
 * upload_url 由后端签发(可能 OSS 直传),原样使用,不带应用鉴权头;故用原生 fetch/XHR
 * 而非 backendRequest(后者会注入 Content-Type: application/json 与 cookie)。
 * 下载 /content 返回二进制流,单独处理。
 */
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

/** 文件状态机(文档 §5)。 */
export type BotSessionFileStatus = 'upload_url_issued' | 'device_syncing' | 'ready' | 'device_sync_failed' | 'deleted';

/** SessionFile 稳定字段(文档 §5)。 */
export interface BotSessionFileDto {
  resource_id: string;
  display_name: string;
  status: BotSessionFileStatus;
  size_bytes: number | null;
  content_hash: string | null;
  task_version: number | null;
  error_code: string | null;
}

/** upload-intents 请求体单文件描述(文档 §4.1)。 */
export interface UploadIntentFileInput {
  filename: string;
  size_bytes: number;
  content_hash?: string;
}

/** upload-intents 响应中的上传凭据(文档 §4.1)。 */
export interface UploadIntentDto extends BotSessionFileDto {
  transfer_id: string;
  upload_type: 'SINGLE' | 'MULTIPART' | string;
  http_method: string;
  upload_url: string | null;
  expires_at: string | null;
  upload_session_id: string | null;
  part_size: number | null;
  part_count: number | null;
  parts: Array<Record<string, unknown>> | null;
}

export interface UploadIntentsData {
  files: UploadIntentDto[];
}

export interface UploadCompleteBody {
  resource_id: string;
  transfer_id: string;
}

export interface DeletedData {
  deleted: boolean;
}

export interface BotSessionFileListData {
  files: BotSessionFileDto[];
}

export interface BotRequestParams {
  user_id: string;
  owner_id?: string;
  stage?: 'draft' | 'verify' | 'online';
}

const base = (botId: string, sessionId: string) =>
  `/openapi/v1/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/files`;

function unwrap<T>(resp: BackendApiEnvelope<T>): T {
  if (resp.data === null || resp.data === undefined) {
    throw new Error(resp.message ?? '接口返回为空');
  }
  return resp.data;
}

/** 1. 申请上传凭据(文档 §4.1)。一次最多 20 个资源。 */
export async function createUploadIntents(
  botId: string,
  sessionId: string,
  params: BotRequestParams,
  body: { files: UploadIntentFileInput[] },
) {
  const resp = await backendRequest<BackendApiEnvelope<UploadIntentsData>>(base(botId, sessionId) + '/upload-intents', {
    method: 'POST',
    params: params as unknown as Record<string, unknown>,
    data: body,
  });
  return unwrap(resp);
}

/** 2. 确认上传完成(文档 §4.2),触发物化。返回单个 SessionFile。 */
export async function completeUpload(
  botId: string,
  sessionId: string,
  params: BotRequestParams,
  body: UploadCompleteBody,
) {
  const resp = await backendRequest<BackendApiEnvelope<BotSessionFileDto>>(
    base(botId, sessionId) + '/upload-complete',
    {
      method: 'POST',
      params: params as unknown as Record<string, unknown>,
      data: body,
    },
  );
  return unwrap(resp);
}

/** 3. 查询单文件物化状态(文档 §4.3)。 */
export async function getMaterializeStatus(
  botId: string,
  sessionId: string,
  resourceId: string,
  params: BotRequestParams,
) {
  const resp = await backendRequest<BackendApiEnvelope<BotSessionFileDto>>(
    `${base(botId, sessionId)}/${encodeURIComponent(resourceId)}/materialize-status`,
    { method: 'GET', params: params as unknown as Record<string, unknown> },
  );
  return unwrap(resp);
}

/** 4. 查询 ready 文件(文档 §4.4)。 */
export async function listReady(botId: string, sessionId: string, params: BotRequestParams) {
  const resp = await backendRequest<BackendApiEnvelope<BotSessionFileListData>>(base(botId, sessionId), {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
  return unwrap(resp);
}

/** 5. 删除文件(文档 §4.6)。 */
export async function deleteFile(botId: string, sessionId: string, resourceId: string, params: BotRequestParams) {
  const resp = await backendRequest<BackendApiEnvelope<DeletedData>>(
    `${base(botId, sessionId)}/${encodeURIComponent(resourceId)}`,
    { method: 'DELETE', params: params as unknown as Record<string, unknown> },
  );
  return unwrap(resp);
}

/** 构造单聊文件内容 URL：仅用于浏览器原生加载（下载/预览），不被 fetch 读取。 */
export function buildBotSessionFileContentUrl(
  botId: string,
  sessionId: string,
  resourceId: string,
  params: BotRequestParams,
  disposition?: 'inline' | 'attachment',
) {
  const query = new URLSearchParams(params as unknown as Record<string, string>);
  if (disposition) query.set('disposition', disposition);
  return `${base(botId, sessionId)}/${encodeURIComponent(resourceId)}/content?${query.toString()}`;
}

/** 6. 下载/预览内容(文档 §4.5),返回二进制 Blob。disposition: inline|attachment。 */
export async function getContentBlob(
  botId: string,
  sessionId: string,
  resourceId: string,
  params: BotRequestParams,
  disposition?: 'inline' | 'attachment',
) {
  const url = buildBotSessionFileContentUrl(botId, sessionId, resourceId, params, disposition);
  const resp = await fetch(url, { method: 'GET', credentials: 'include' });
  if (!resp.ok) {
    let detail = '';
    try {
      const data = await resp.json();
      detail = data?.message ?? '';
    } catch {
      /* ignore */
    }
    throw Object.assign(new Error(detail || `下载失败: ${resp.status}`), { status: resp.status });
  }
  return resp.blob();
}

/** 二进制直传到 upload_url(单段 SINGLE)。原生 XHR,支持进度与取消,不带应用鉴权头。 */
export function uploadToUrl(
  uploadUrl: string,
  method: string,
  body: ArrayBuffer | Blob,
  opts: {
    signal?: AbortSignal;
    onProgress?: (loaded: number, total: number) => void;
    contentType?: string;
    headers?: Record<string, string>;
  } = {},
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const httpMethod = (method || 'PUT').toUpperCase();
    xhr.open(httpMethod, uploadUrl, true);
    if (opts.contentType) xhr.setRequestHeader('Content-Type', opts.contentType);
    if (opts.headers) {
      for (const [key, value] of Object.entries(opts.headers)) {
        // OSS 预签名不把 Content-Type 纳入签名；透传 back-end 返回的其它头。
        if (key.toLowerCase() === 'content-type') continue;
        try {
          xhr.setRequestHeader(key, value);
        } catch {
          /* 过滤非法 header 名 */
        }
      }
    }
    let aborted = false;
    if (opts.signal) {
      if (opts.signal.aborted) {
        reject(new DOMException('Aborted', 'AbortError'));
        return;
      }
      const onAbort = () => {
        aborted = true;
        xhr.abort();
      };
      opts.signal.addEventListener('abort', onAbort, { once: true });
      xhr.addEventListener('loadend', () => opts.signal?.removeEventListener('abort', onAbort), { once: true });
    }
    if (opts.onProgress && xhr.upload) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) opts.onProgress!(e.loaded, e.total);
      };
    }
    xhr.onloadend = () => {
      if (aborted) return;
      if (xhr.status >= 200 && xhr.status < 300) {
        // 补一次完成回调，避免部分环境下 upload.onprogress 不触发导致进度卡 0。
        const total = body instanceof Blob ? body.size : body.byteLength;
        opts.onProgress?.(total, total);
        resolve();
      } else {
        reject(Object.assign(new Error(`上传失败: ${xhr.status}`), { status: xhr.status }));
      }
    };
    xhr.onerror = () => {
      if (!aborted) reject(new Error('上传网络错误'));
    };
    xhr.onabort = () => {
      if (!aborted) reject(new DOMException('Aborted', 'AbortError'));
    };
    xhr.send(body);
  });
}

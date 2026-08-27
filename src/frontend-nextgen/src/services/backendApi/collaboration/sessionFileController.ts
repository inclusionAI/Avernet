import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

/** 文件所有者 actor_kind（网关用小写，区别于仓库内其它 PascalCase 形态）。 */
export type SessionFileActorKind = 'human' | 'bot';

/** 文件状态机。 */
export type SessionFileStatus = 'pending' | 'ready' | 'deleting' | 'failed';

export interface SessionFileOwnerDto {
  actor_kind: SessionFileActorKind;
  actor_id: string;
}

export interface SessionFileDto {
  file_id: string;
  session_id: string;
  file_name: string;
  mime_type: string;
  size: number;
  sha256: string | null;
  owner: SessionFileOwnerDto;
  storage_backend: string;
  status: SessionFileStatus;
  created_at: number;
  updated_at: number;
}

export interface ListSessionFilesData {
  items: SessionFileDto[];
  total: number;
}

/** prepare 返回：单文件给 upload_url；多分片给 upload_id + parts（有序分片 URL）。 */
export interface PrepareUploadData {
  file_id: string;
  /** 单文件直传地址（存在时直接 PUT 字节到该地址）。 */
  upload_url?: string;
  /** 多分片上传会话标识（存在时走多分片）。 */
  upload_id?: string;
  /** 有序分片目标（每项含上传地址，形态可能与后端 storage 相关）。 */
  parts?: Array<Record<string, unknown>>;
}

export interface ShareSessionFileData {
  share_url: string;
  share_token: string;
  expires_at: number;
}

const filesPath = (sessionId: string) => `/api/v1/collaboration/sessions/${encodeURIComponent(sessionId)}/files`;

const filePath = (sessionId: string, fileId: string) => `${filesPath(sessionId)}/${encodeURIComponent(fileId)}`;

/** 列出会话文件。 */
export async function listSessionFiles(
  sessionId: string,
  params: { status?: SessionFileStatus; prefix?: string; limit?: number; offset?: number } = {},
) {
  return backendRequest<BackendApiEnvelope<ListSessionFilesData>>(filesPath(sessionId), {
    method: 'GET',
    params: params as Record<string, unknown>,
  });
}

/** 查询单个文件元数据。 */
export async function getSessionFile(sessionId: string, fileId: string) {
  return backendRequest<BackendApiEnvelope<SessionFileDto>>(filePath(sessionId, fileId), { method: 'GET' });
}

/** 准备上传。 */
export async function prepareSessionFile(
  sessionId: string,
  body: { file_name: string; size: number; mime_type: string },
) {
  return backendRequest<BackendApiEnvelope<PrepareUploadData>>(filesPath(sessionId), {
    method: 'POST',
    data: body,
  });
}

/** 完成上传（后端组装并转 ready）。 */
export async function completeSessionFile(sessionId: string, fileId: string) {
  return backendRequest<BackendApiEnvelope<SessionFileDto>>(`${filePath(sessionId, fileId)}/complete`, {
    method: 'POST',
    data: {},
  });
}

/** 删除/取消会话文件。 */
export async function deleteSessionFile(sessionId: string, fileId: string) {
  return backendRequest<BackendApiEnvelope<unknown>>(filePath(sessionId, fileId), { method: 'DELETE' });
}

/** 生成分享链接（仅 ready 文件）。 */
export async function shareSessionFile(sessionId: string, fileId: string, body: { ttl_seconds?: number } = {}) {
  return backendRequest<BackendApiEnvelope<ShareSessionFileData>>(`${filePath(sessionId, fileId)}/share`, {
    method: 'POST',
    data: body,
  });
}

/** 构造下载/预览 URL：show=true 走内联预览，false（默认）走下载。 */
export function buildSessionFileContentUrl(sessionId: string, fileId: string, show = false): string {
  const query = show ? '?show=true' : '';
  return `${filePath(sessionId, fileId)}/content${query}`;
}

/**
 * 拉取文件内容字节（show=true 走网关流式返回，200 原始字节，不触发 302 预签名重定向）。
 * 复用浏览器会话凭证（credentials:include），与其它 backendRequest 同源鉴权一致，
 * 避免顶层窗口导航丢失鉴权导致 401。
 */
export async function getSessionFileContentBlob(sessionId: string, fileId: string): Promise<Blob> {
  const resp = await fetch(buildSessionFileContentUrl(sessionId, fileId, true), {
    method: 'GET',
    credentials: 'include',
  });
  if (!resp.ok) {
    throw Object.assign(new Error(`download failed: ${resp.status}`), { status: resp.status });
  }
  return resp.blob();
}

export interface UploadBytesOptions {
  mime?: string;
  signal?: AbortSignal;
  onProgress?: (loaded: number, total: number) => void;
}

/** 判断是否为跨站直传地址（presigned 目标需去除站点凭证）。 */
function isAbsoluteUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

/**
 * 以 PUT 上传二进制字节，支持进度与取消。
 * 直传地址（presigned / 绝对 URL）：不携带站点凭证；网关 content 路由（相对路径）：携带凭证鉴权。
 */
export function uploadBytes(url: string, body: Blob, opts: UploadBytesOptions = {}): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url, true);
    if (opts.mime) xhr.setRequestHeader('Content-Type', opts.mime);
    xhr.withCredentials = !isAbsoluteUrl(url);
    xhr.responseType = 'text';

    if (opts.onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) opts.onProgress?.(e.loaded, e.total);
      };
    }
    const onAbort = () => {
      const err = new Error('Aborted') as Error & { name: string };
      err.name = 'AbortError';
      reject(err);
    };
    if (opts.signal) {
      if (opts.signal.aborted) onAbort();
      opts.signal.addEventListener('abort', onAbort, { once: true });
    }
    xhr.onload = () => {
      opts.signal?.removeEventListener('abort', onAbort);
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(Object.assign(new Error(`upload failed: ${xhr.status}`), { status: xhr.status }));
    };
    xhr.onerror = () => {
      opts.signal?.removeEventListener('abort', onAbort);
      reject(new Error('upload network error'));
    };

    // 防浏览器按 Blob.type 自动注入，唯一控制 Content-Type 取值。
    xhr.send(new Blob([body], { type: '' }));
  });
}

/** 上传单文件或单个分片到网关 content 路由（相对地址，带会话凭证）。 */
export function uploadSessionFileContent(
  sessionId: string,
  fileId: string,
  body: Blob,
  opts: UploadBytesOptions & { part?: number } = {},
): Promise<void> {
  const query = opts.part !== undefined ? `?part=${opts.part}` : '';
  return uploadBytes(`${filePath(sessionId, fileId)}/content${query}`, body, opts);
}

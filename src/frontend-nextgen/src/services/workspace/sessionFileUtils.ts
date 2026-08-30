import type { SessionFileActorKind } from '@/services/backendApi/collaboration/sessionFileController';

/** 允许上传的扩展名白名单。 */
export const SESSION_FILE_ALLOWED_EXT = [
  'xlsx',
  'xls',
  'csv',
  'tsv',
  'txt',
  'json',
  'yaml',
  'yml',
  'md',
  'doc',
  'docx',
  'pdf',
  'ppt',
  'pptx',
  'zip',
  'tar.gz',
  'html',
  'js',
  'jsx',
  'ts',
  'tsx',
  'vue',
  'mjs',
  'cjs',
  'py',
  'java',
  'go',
  'rs',
  'c',
  'cpp',
  'h',
  'cs',
  'rb',
  'php',
  'sh',
  'bash',
  'zsh',
  'bat',
  'ps1',
  'sql',
  'xml',
  'log',
  'ini',
  'toml',
  'conf',
  'env',
  'jpeg',
  'jpg',
  'png',
  'gif',
  'svg',
] as const;

/** 单次最多上传文件数。 */
export const SESSION_FILE_MAX_BATCH = 20;

/** 单文件超过此值走分片上传（与网关 prepare multipart 目标一致）。 */
export const SESSION_FILE_MULTIPART_THRESHOLD = 8 * 1024 * 1024;

/** 文件大小格式化。 */
export function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  let size = bytes;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index++;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

/** 取扩展名（小写，支持 .tar.gz 多段后缀）。 */
export function getFileExt(name: string): string {
  if (!name) return '';
  const lower = name.toLowerCase();
  if (/\.tar\.gz$/.test(lower)) return 'tar.gz';
  const idx = lower.lastIndexOf('.');
  return idx >= 0 ? lower.slice(idx + 1) : '';
}

/** 是否在白名单内。 */
export function isAllowedFileExt(name: string): boolean {
  const ext = getFileExt(name);
  return !!ext && (SESSION_FILE_ALLOWED_EXT as readonly string[]).includes(ext);
}

/** 文件名是否非法（防路径穿越）。 */
export function isIllegalFileName(name: string): boolean {
  if (!name) return true;
  if (name.includes('/') || name.includes('\\') || name.includes('\0')) return true;
  const trimmed = name.trim();
  return trimmed === '.' || trimmed === '..';
}

/** 生成客户端本地唯一 id（上传队列用）。 */
export function genLocalFileId(): string {
  return `sf_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** 文本类扩展名按扩展名修正浏览器误标的 MIME（.ts→video/mp2t 等）。 */
const TEXT_EXT_MIME: Record<string, string> = {
  md: 'text/markdown',
  json: 'application/json',
  svg: 'image/svg+xml',
  csv: 'text/csv',
  tsv: 'text/tab-separated-values',
  html: 'text/html',
  xml: 'application/xml',
  yaml: 'text/yaml',
  yml: 'text/yaml',
  js: 'text/javascript',
  mjs: 'text/javascript',
  cjs: 'text/javascript',
  ts: 'text/typescript',
};

/** 文本类扩展名会补充 charset，二进制不参与。 */
const SESSION_FILE_BINARY_EXT = [
  'xlsx',
  'xls',
  'doc',
  'docx',
  'pdf',
  'ppt',
  'pptx',
  'zip',
  'tar.gz',
  'jpeg',
  'jpg',
  'png',
  'gif',
] as const;

/** 编码探测仅采样首 64KB，避免整读大文件。 */
export const ENCODING_SNIFF_BYTES = 64 * 1024;

/** 是否文本类文件（扩展名驱动，浏览器 file.type 对 .md/.ts 等不可靠）。 */
export function isTextLikeExt(name: string): boolean {
  if (!isAllowedFileExt(name)) return false;
  const ext = getFileExt(name);
  return !(SESSION_FILE_BINARY_EXT as readonly string[]).includes(ext);
}

/** 解析文本基础 MIME：可见 text/* 优先，其次扩展名映射，最后兜底 text/plain。 */
export function resolveTextBaseMime(name: string, browserMime: string): string {
  if (browserMime && browserMime.toLowerCase().startsWith('text/')) return browserMime;
  const ext = getFileExt(name);
  return TEXT_EXT_MIME[ext] || 'text/plain';
}

/** chardet 检出编码归一化为 HTTP charset 参数值。 */
export function mapChardetLabel(detected: string | null): string {
  if (!detected) return 'utf-8';
  if (detected === 'ASCII' || detected === 'UTF-8') return 'utf-8';
  return detected.toLowerCase();
}

async function readBlobBytes(blob: Blob): Promise<Uint8Array> {
  const arrayBuffer = typeof blob.arrayBuffer === 'function' ? blob.arrayBuffer() : null;
  if (arrayBuffer) return new Uint8Array(await arrayBuffer);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error ?? new Error('read file failed'));
    reader.readAsArrayBuffer(blob);
  });
}

/** 解析上传用 Content-Type：文本类按扩展名修正，其余沿用浏览器 MIME。 */
export function resolveUploadMime(name: string, browserMime: string): string {
  const ext = getFileExt(name);
  if (browserMime && browserMime.toLowerCase().startsWith('text/')) return browserMime;
  return TEXT_EXT_MIME[ext] || browserMime || 'application/octet-stream';
}

/**
 * 组装 prepare 阶段上传用 Content-Type；文本类检测真实编码并追加 charset，供后端保存元数据、
 * 后续预览按正确编码渲染。纯 ASCII 快路径不加载 chardet；含高位字节才动态探测。
 */
export async function resolveUploadContentType(name: string, browserMime: string, file: Blob): Promise<string> {
  if (!isTextLikeExt(name)) return browserMime;
  const base = resolveTextBaseMime(name, browserMime);
  const bytes = await readBlobBytes(file.slice(0, ENCODING_SNIFF_BYTES));
  if (bytes.every((byte) => byte < 0x80)) return `${base}; charset=utf-8`;
  const { detect } = await import('chardet');
  return `${base}; charset=${mapChardetLabel(detect(bytes))}`;
}

/** 从 prepare 返回的分片目标对象中提取上传地址。 */
export function extractPartUrl(part: Record<string, unknown> | undefined): string | undefined {
  if (!part) return undefined;
  const url = part.upload_url ?? part.url;
  return typeof url === 'string' ? url : undefined;
}

export interface OwnerNameSource {
  actorId?: string;
  id?: string;
  botUuid?: string;
  name?: string;
}

/**
 * 文件所有者展示名解析：优先按 actor_id 在会话成员中匹配，失败回退 actor_id；
 * 人类 actor_id 形如 `human_xxx` 时清理前缀。
 */
export function resolveOwnerDisplayName(
  owner: { actor_kind?: SessionFileActorKind; actor_id?: string } | null | undefined,
  participants: readonly OwnerNameSource[] | undefined,
): string {
  if (!owner) return '--';
  const rawId = owner.actor_id || '';
  if (rawId && participants?.length) {
    const matched = participants.find(
      (p) => (p.actorId && p.actorId === rawId) || (p.id && p.id === rawId) || (p.botUuid && p.botUuid === rawId),
    );
    if (matched?.name) return matched.name;
  }
  if (!rawId) return '--';
  if (owner.actor_kind === 'human' && rawId.startsWith('human_')) return rawId.slice(6);
  return rawId;
}

export type SessionFilePreviewKind = 'image' | 'pdf' | 'text' | 'other';

export const PREVIEW_IMAGE_EXT = ['png', 'jpg', 'jpeg', 'gif', 'svg'] as const;

export const PREVIEW_TEXT_EXT = [
  'txt',
  'md',
  'json',
  'csv',
  'tsv',
  'yaml',
  'yml',
  'log',
  'ini',
  'toml',
  'conf',
  'env',
  'xml',
  'html',
  'js',
  'jsx',
  'ts',
  'tsx',
  'vue',
  'mjs',
  'cjs',
  'py',
  'java',
  'go',
  'rs',
  'c',
  'cpp',
  'h',
  'cs',
  'rb',
  'php',
  'sh',
  'bash',
  'zsh',
  'sql',
  'bat',
  'ps1',
] as const;

/** 判断文件是否支持站内预览：图片 / PDF / 文本类支持，其余提示下载后查看。 */
export function getPreviewKind(name: string, mimeType?: string): SessionFilePreviewKind {
  const ext = getFileExt(name);
  const mime = (mimeType || '').toLowerCase();
  if ((PREVIEW_IMAGE_EXT as readonly string[]).includes(ext) || mime.startsWith('image/')) return 'image';
  if (ext === 'pdf' || mime === 'application/pdf') return 'pdf';
  if ((PREVIEW_TEXT_EXT as readonly string[]).includes(ext) || mime.startsWith('text/')) return 'text';
  return 'other';
}

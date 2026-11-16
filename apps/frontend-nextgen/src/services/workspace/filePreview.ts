export const SESSION_FILE_TEXT_PREVIEW_MAX_BYTES = 512 * 1024;
export type SessionFilePreviewKind = 'image' | 'video' | 'pdf' | 'html' | 'text' | 'other';

export const PREVIEW_IMAGE_EXT = ['png', 'jpg', 'jpeg', 'gif', 'svg'] as const;

/** 视频类（内嵌播放器；上传白名单不含视频，仅预览来自其他渠道的文件）。 */
export const PREVIEW_VIDEO_EXT = ['mp4', 'webm', 'mov'] as const;

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

export function getFileExt(name: string): string {
  if (!name) return '';
  const lower = name.toLowerCase();
  if (/\.tar\.gz$/.test(lower)) return 'tar.gz';
  const idx = lower.lastIndexOf('.');
  return idx >= 0 ? lower.slice(idx + 1) : '';
}

export function prettifyJsonText(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function getPreviewKind(name: string, mimeType?: string): SessionFilePreviewKind {
  const ext = getFileExt(name);
  const mime = (mimeType || '').toLowerCase();
  if ((PREVIEW_IMAGE_EXT as readonly string[]).includes(ext) || mime.startsWith('image/')) return 'image';
  if ((PREVIEW_VIDEO_EXT as readonly string[]).includes(ext) || mime.startsWith('video/')) return 'video';
  if (ext === 'pdf' || mime === 'application/pdf') return 'pdf';
  if (ext === 'html' || mime === 'text/html') return 'html';
  if ((PREVIEW_TEXT_EXT as readonly string[]).includes(ext) || mime.startsWith('text/')) return 'text';
  return 'other';
}

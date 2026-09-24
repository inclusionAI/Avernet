import { getFileExt } from '@/services/workspace/sessionFileUtils';
import type { LucideIcon } from 'lucide-react';
import { File, FileArchive, FileCode, FileImage, FileSpreadsheet, FileText, FileType, FileVideo } from 'lucide-react';

export type SessionFileIconKind =
  | 'image'
  | 'video'
  | 'pdf'
  | 'spreadsheet'
  | 'archive'
  | 'code'
  | 'doc'
  | 'office'
  | 'other';

/** 图片族（预览白名单 + 常见兜底）。 */
const IMAGE_ICON_EXT = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'] as const;
/** 视频族（内嵌播放器预览；与图片同为媒体色系）。 */
const VIDEO_ICON_EXT = ['mp4', 'webm', 'mov', 'avi', 'mkv'] as const;
/** 表格族（Excel/CSV）。 */
const SPREADSHEET_ICON_EXT = ['xlsx', 'xls', 'csv', 'tsv'] as const;
/** 压缩族（tar.gz 由 getFileExt 多段后缀支持）。 */
const ARCHIVE_ICON_EXT = ['zip', 'tar.gz', 'rar', '7z'] as const;
/** 代码与标记语言族（对齐上传白名单的开发类扩展名）。 */
const CODE_ICON_EXT = [
  'json',
  'yaml',
  'yml',
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
  'html',
  'htm',
  'css',
  'scss',
] as const;
/** 纯文本文档族。 */
const DOC_ICON_EXT = ['txt', 'md', 'markdown', 'log', 'rst', 'ini', 'toml', 'conf', 'env'] as const;
/** Office 套件（不可预览，中性色区分于蓝色可预览文本族）。 */
const OFFICE_ICON_EXT = ['doc', 'docx', 'ppt', 'pptx'] as const;

/** 文件名（+ 可选 MIME）→ 类型族别，纯静态判定，供列表行首图标与提示共用。 */
export function getSessionFileIconKind(name: string, mimeType?: string): SessionFileIconKind {
  if ((mimeType || '').toLowerCase().startsWith('image/')) return 'image';
  const ext = getFileExt(name);
  if (!ext) return 'other';
  if ((IMAGE_ICON_EXT as readonly string[]).includes(ext)) return 'image';
  if ((VIDEO_ICON_EXT as readonly string[]).includes(ext)) return 'video';
  if (ext === 'pdf') return 'pdf';
  if ((SPREADSHEET_ICON_EXT as readonly string[]).includes(ext)) return 'spreadsheet';
  if ((ARCHIVE_ICON_EXT as readonly string[]).includes(ext)) return 'archive';
  if ((CODE_ICON_EXT as readonly string[]).includes(ext)) return 'code';
  if ((DOC_ICON_EXT as readonly string[]).includes(ext)) return 'doc';
  if ((OFFICE_ICON_EXT as readonly string[]).includes(ext)) return 'office';
  return 'other';
}

const KIND_META: Record<SessionFileIconKind, { Icon: LucideIcon; colorClass: string }> = {
  image: { Icon: FileImage, colorClass: 'text-info' },
  video: { Icon: FileVideo, colorClass: 'text-info' },
  pdf: { Icon: FileType, colorClass: 'text-destructive' },
  spreadsheet: { Icon: FileSpreadsheet, colorClass: 'text-success' },
  archive: { Icon: FileArchive, colorClass: 'text-warning' },
  code: { Icon: FileCode, colorClass: 'text-primary' },
  doc: { Icon: FileText, colorClass: 'text-primary' },
  office: { Icon: FileText, colorClass: 'text-muted-foreground' },
  other: { Icon: File, colorClass: 'text-muted-foreground' },
};

/** 文件大类（列表过滤 Tab 用）：细分族聚合为 4 大类 + 其他。 */
export type SessionFileCategory = 'media' | 'document' | 'code' | 'sheet' | 'other';

export const SESSION_FILE_CATEGORY_LABELS: Record<SessionFileCategory, string> = {
  media: '媒体',
  document: '文档',
  code: '代码',
  sheet: '表格',
  other: '其他',
};

const KIND_TO_CATEGORY: Record<SessionFileIconKind, SessionFileCategory> = {
  image: 'media',
  video: 'media',
  pdf: 'document',
  doc: 'document',
  office: 'document',
  code: 'code',
  spreadsheet: 'sheet',
  archive: 'other',
  other: 'other',
};

/** 文件名（+ 可选 MIME）→ 大类（媒体/文档/代码/表格/其他），供列表过滤 Tab 共用。 */
export function getSessionFileCategory(name: string, mimeType?: string): SessionFileCategory {
  return KIND_TO_CATEGORY[getSessionFileIconKind(name, mimeType)];
}

interface SessionFileTypeIconProps {
  name: string;
  mimeType?: string;
  className?: string;
}

/** 会话文件列表行首的类型图标（形状=类型族，颜色=语义色，均走白名单 token）。 */
export function SessionFileTypeIcon({ name, mimeType, className = 'h-4 w-4' }: SessionFileTypeIconProps) {
  const { Icon, colorClass } = KIND_META[getSessionFileIconKind(name, mimeType)];
  return <Icon className={`${colorClass} ${className}`} aria-hidden />;
}

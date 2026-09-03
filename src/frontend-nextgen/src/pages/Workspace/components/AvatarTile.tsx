import { cn } from '@/utils/cn';
import { Bot } from 'lucide-react';
import type { ReactNode } from 'react';

interface AvatarTileProps {
  /** 头像地址(http 图片)或 emoji;为空时回退首字。 */
  src?: string;
  label: string;
  className?: string;
  /** 无图片时覆盖默认首字，用于区分群/Bot资源类型。 */
  fallbackContent?: ReactNode;
}

/** Bot/群列表头像:图片 > emoji > 名称首字 > 占位图标，使用轻量 32px 方形底板。 */
export function AvatarTile({ src, label, className, fallbackContent }: AvatarTileProps) {
  const isImage = !!src && /^(https?:)?\/\//.test(src);
  if (isImage) {
    return <img src={src} alt={label} className={cn('h-8 w-8 shrink-0 rounded-md object-cover', className)} />;
  }
  const fallback = src?.trim() || label.trim().slice(0, 1);
  const content = src?.trim() ? fallback : fallbackContent ?? fallback;
  return (
    <span
      aria-hidden="true"
      className={cn(
        'flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-xs font-medium text-primary',
        className,
      )}
    >
      {content || <Bot className="h-4 w-4" />}
    </span>
  );
}

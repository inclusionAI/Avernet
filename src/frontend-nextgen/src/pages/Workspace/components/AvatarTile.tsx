import { cn } from '@/utils/cn';
import { Bot } from 'lucide-react';

interface AvatarTileProps {
  /** 头像地址(http 图片)或 emoji;为空时回退首字。 */
  src?: string;
  label: string;
  className?: string;
}

/** Bot/群卡片圆角方形头像:图片 > emoji > 名称首字 > 占位图标,底板浅蓝。 */
export function AvatarTile({ src, label, className }: AvatarTileProps) {
  const isImage = !!src && /^(https?:)?\/\//.test(src);
  if (isImage) {
    return <img src={src} alt={label} className={cn('h-9 w-9 shrink-0 rounded-lg object-cover', className)} />;
  }
  const fallback = src?.trim() || label.trim().slice(0, 1);
  return (
    <span
      aria-hidden="true"
      className={cn(
        'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-sm font-semibold text-primary',
        className,
      )}
    >
      {fallback || <Bot className="h-4 w-4" />}
    </span>
  );
}

/**
 * BotAvatar — Bot 头像组件
 *
 * 优先显示自定义图片，无图片时用 botId（或 name）作 seed 生成 Bottts 头像。
 * user 类型：有 avatarUrl 时显示图片，否则显示 User 图标。
 *
 * 尺寸：xs=28px / sm=32px / md=40px / lg=56px
 * 类型：user / assistant / expert / group
 */

import { generateBotAvatar } from '@/components/BotAvatar/generateAvatar';
import { cn } from '@/utils/utils';
import { User, Users } from 'lucide-react';
import React from 'react';

export type BotAvatarType = 'user' | 'assistant' | 'expert' | 'group';
export type BotAvatarSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

export interface BotAvatarProps {
  type: BotAvatarType;
  size?: BotAvatarSize;
  className?: string;
  /** 自定义头像 URL，优先显示 */
  avatarUrl?: string | null;
  /** Bot 名称，seed 备选 */
  name?: string;
  /** Bot ID，作为 Bottts 生成 seed（优先于 name） */
  botId?: string;
}

const SIZE_CONFIG: Record<
  BotAvatarSize,
  { container: string; iconSize: number }
> = {
  xs: { container: 'w-7 h-7 rounded-lg', iconSize: 13 },
  sm: { container: 'w-8 h-8 rounded-lg', iconSize: 14 },
  md: { container: 'w-10 h-10 rounded-xl', iconSize: 18 },
  lg: { container: 'w-14 h-14 rounded-2xl', iconSize: 24 },
  xl: { container: 'w-20 h-20 rounded-2xl', iconSize: 32 },
};

const BotAvatar: React.FC<BotAvatarProps> = ({
  type,
  size = 'md',
  className,
  avatarUrl,
  name,
  botId,
}) => {
  const { container, iconSize } = SIZE_CONFIG[size];
  // 1. 自定义图片
  const imgSrc = avatarUrl || null;

  // 图片加载失败时降级
  const [imgError, setImgError] = React.useState(false);
  // avatarUrl 变化时重置错误状态
  React.useEffect(() => {
    setImgError(false);
  }, [avatarUrl]);

  // 2. 无图片时用 Bottts 生成（seed = botId > name > 'default'）
  const botttsDataUri = React.useMemo(() => {
    const seed = botId || name || 'default';
    return generateBotAvatar(seed);
  }, [botId, name]);

  // user 类型：有自定义图片且未加载失败时显示图片，否则显示 User 图标
  if (type === 'user') {
    if (imgSrc && !imgError) {
      return (
        <div
          className={cn(
            'flex items-center justify-center flex-shrink-0 overflow-hidden bg-slate-100',
            container,
            className,
          )}
        >
          <img
            src={imgSrc}
            alt="用户头像"
            className="w-full h-full object-cover"
            onError={() => setImgError(true)}
          />
        </div>
      );
    }
    return (
      <div
        className={cn(
          'flex items-center justify-center flex-shrink-0 bg-slate-100 text-slate-500',
          container,
          className,
        )}
      >
        <User size={iconSize} />
      </div>
    );
  }

  // group 类型：固定 Users 图标
  if (type === 'group') {
    return (
      <div
        className={cn(
          'flex items-center justify-center flex-shrink-0 bg-slate-100 text-slate-400',
          container,
          className,
        )}
      >
        <Users size={iconSize} />
      </div>
    );
  }

  const src = imgSrc && !imgError ? imgSrc : botttsDataUri;

  return (
    <div
      className={cn(
        'flex items-center justify-center flex-shrink-0 overflow-hidden bg-slate-100',
        container,
        className,
      )}
    >
      <img
        src={src}
        alt="Bot 头像"
        className="w-full h-full object-cover"
        onError={(e) => {
          // 图片加载失败时降级为 Bottts
          if (
            imgSrc &&
            !imgError &&
            (e.target as HTMLImageElement).src !== botttsDataUri
          ) {
            (e.target as HTMLImageElement).src = botttsDataUri;
          }
        }}
      />
    </div>
  );
};

export default BotAvatar;

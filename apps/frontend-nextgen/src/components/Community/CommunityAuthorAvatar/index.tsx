import { Avatar } from '@/components/ui/Avatar';
import type { CommunityAuthor } from '@/domain/community/types';
import { Bot, User } from 'lucide-react';
import type { CSSProperties } from 'react';

/**
 * 社区作者头像：有 avatarUrl 走真实图片；否则按作者类型回退语义图标
 * —— Bot 给 Bot 图标、Human 给 User 图标，避免用 bot_id/工号的首字母兜底
 * （社区后端目前不返回 display_name/avatar_url，作者名仅是 bot_id，
 * 取首字母会显示年份数字，观感差）。类型图标已能区分 Bot/Human。
 */
export function CommunityAuthorAvatar({ author, size = 40 }: { author: CommunityAuthor; size?: number }): JSX.Element {
  if (author.avatarUrl) return <Avatar name={author.displayName} src={author.avatarUrl} size={size} />;

  const Icon = author.type === 'bot' ? Bot : User;
  const iconSize = Math.round(size * 0.5);
  const style: CSSProperties = { width: size, height: size };
  return (
    <span
      role="img"
      aria-label={author.displayName}
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
      style={style}
    >
      <Icon aria-hidden width={iconSize} height={iconSize} />
    </span>
  );
}

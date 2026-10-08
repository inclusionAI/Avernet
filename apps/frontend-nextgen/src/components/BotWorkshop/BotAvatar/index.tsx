import { cn } from '@/utils/cn';
import { Bot } from 'lucide-react';
import React from 'react';
export interface BotAvatarProps {
  name?: string;
  avatarUrl?: string;
  className?: string;
}
const BotAvatar: React.FC<BotAvatarProps> = ({ name, avatarUrl, className }) => (
  <div
    aria-label={`${name ?? 'Bot'} 头像`}
    className={cn(
      'flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-primary-soft)] text-[var(--color-primary)]',
      className,
    )}
  >
    <>
      {avatarUrl ? (
        <img src={avatarUrl} alt="" className="size-full rounded-xl object-cover" />
      ) : (
        <Bot className="h-5 w-5" aria-hidden />
      )}
    </>
  </div>
);
export default BotAvatar;

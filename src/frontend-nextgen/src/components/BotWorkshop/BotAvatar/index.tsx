import { cn } from '@/utils/cn';
import { Bot } from 'lucide-react';
import React from 'react';
export interface BotAvatarProps {
  name?: string;
  className?: string;
}
const BotAvatar: React.FC<BotAvatarProps> = ({ name, className }) => (
  <div
    aria-label={`${name ?? 'Bot'} 头像`}
    className={cn(
      'flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-primary-soft)] text-[var(--color-primary)]',
      className,
    )}
  >
    <Bot className="h-5 w-5" aria-hidden />
  </div>
);
export default BotAvatar;

import { cn } from '@/utils/cn';
import type { SVGProps } from 'react';

/** 关注入口统一图标：爱心表示关注，双侧波纹表示持续接收动态。 */
export function CommunityFollowIcon({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('community-follow-icon', className)}
      {...props}
    >
      <path d="M4.4 4.2C1.2 8.1 1.2 15.9 4.4 19.8" />
      <path d="M7.4 7.1C5.5 9.7 5.5 14.3 7.4 16.9" />
      <path d="M19.6 4.2c3.2 3.9 3.2 11.7 0 15.6" />
      <path d="M16.6 7.1c1.9 2.6 1.9 7.2 0 9.8" />
      <path d="M12 16.3s-4-2.4-4-5.2a2.5 2.5 0 0 1 4-2 2.5 2.5 0 0 1 4 2c0 2.8-4 5.2-4 5.2Z" />
    </svg>
  );
}

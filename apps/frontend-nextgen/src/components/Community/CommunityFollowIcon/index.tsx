import { cn } from '@/utils/cn';
import { UserRoundCheck, type LucideProps } from 'lucide-react';

/** 社区关注入口统一使用「用户 + 对勾」图标。 */
export function CommunityFollowIcon({ className, ...props }: LucideProps) {
  return <UserRoundCheck className={cn('community-follow-icon', className)} {...props} />;
}

// 顶部栏右上角账号身份栏。消费 useHumanIdentity 解析当前登录用户真实花名/头像。
// Open Core：listMyBots human[0]；内部 overlay：staff_id + __TERN__.user。本组件零感知差异。
// 用户状态由其他业务区域表达，顶栏只展示头像和名称。Open Core（无 internal import）。
import { Button } from '@/components/ui';
import { Avatar } from '@/components/ui/Avatar';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { cn } from '@/utils/cn';
import { Loader2, User } from 'lucide-react';

/** 兼容壳层测试/集成注入；生产默认从 useHumanIdentity 读取。 */
export interface AccountUser {
  displayName: string;
  avatarUrl?: string;
}

/** 圆头像位（loading 旋转 / error 灰）。 */
function AvatarIcon({ spinning }: { spinning?: boolean }) {
  return (
    <span
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-white"
      style={{ background: 'linear-gradient(135deg, rgb(22,93,255), rgb(64,150,255))' }}
    >
      {spinning ? (
        <Loader2 className={cn('h-4 w-4 animate-spin')} aria-hidden />
      ) : (
        <User className="h-[15px] w-[15px]" aria-hidden />
      )}
    </span>
  );
}

function ReadyAccountBadge({ displayName, avatarUrl }: AccountUser) {
  return (
    <Button
      variant="ghost"
      className="h-auto justify-start gap-2.5 rounded-lg px-3 py-0 pl-1"
      leftIcon={<Avatar name={displayName} src={avatarUrl} size={32} />}
    >
      <span className="flex text-left" style={{ lineHeight: 1.2 }}>
        <span className="truncate max-w-[120px] text-[13px] font-semibold text-[rgb(29,33,41)]">{displayName}</span>
      </span>
    </Button>
  );
}

export function AccountBadge({ currentUser }: { currentUser?: AccountUser | null } = {}) {
  const { identity, status } = useHumanIdentity();

  // 显式传入时用于壳层集成和测试；未传入时走真实身份 Hook。
  if (currentUser !== undefined) {
    return (
      <ReadyAccountBadge displayName={currentUser?.displayName ?? '当前用户'} avatarUrl={currentUser?.avatarUrl} />
    );
  }

  // loading：渐变圆 + 旋转 + 「加载中…」
  if (status === 'loading') {
    return (
      <Button
        variant="ghost"
        className="h-auto justify-start gap-2.5 rounded-lg px-3 py-0 pl-1"
        leftIcon={<AvatarIcon spinning />}
      >
        <span className="flex text-left" style={{ lineHeight: 1.2 }}>
          <span className="text-[13px] font-semibold text-[rgb(29,33,41)]">加载中…</span>
        </span>
      </Button>
    );
  }

  // error / 无身份：灰名占位，不白屏
  if (status === 'error' || !identity) {
    return (
      <Button
        variant="ghost"
        className="h-auto justify-start gap-2.5 rounded-lg px-3 py-0 pl-1"
        leftIcon={<AvatarIcon />}
      >
        <span className="flex flex-col text-left" style={{ lineHeight: 1.2 }}>
          <span className="text-[13px] font-semibold text-[rgb(134,144,156)]">未登录</span>
        </span>
      </Button>
    );
  }

  return <ReadyAccountBadge displayName={identity.displayName} avatarUrl={identity.avatarUrl} />;
}

export default AccountBadge;

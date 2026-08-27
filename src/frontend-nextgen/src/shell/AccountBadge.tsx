// 顶部栏右上角账号身份栏。消费 useHumanIdentity 解析当前登录用户真实花名/头像/在线。
// Open Core：listMyBots human[0]；内部 overlay：staff_id + __TERN__.user。本组件零感知差异。
// 视觉对齐 PRD demo：圆形蓝色渐变头像 + User 人头肩图标 + 右下角在线绿点。Open Core（无 internal import）。
import { Button } from '@/components/ui';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { cn } from '@/utils/cn';
import { Loader2, User } from 'lucide-react';

/** 兼容壳层测试/集成注入；生产默认从 useHumanIdentity 读取。 */
export interface AccountUser {
  displayName: string;
  online?: boolean;
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

function ReadyAccountBadge({ displayName, avatarUrl, online }: AccountUser) {
  return (
    <Button
      variant="ghost"
      className="h-auto justify-start gap-2.5 rounded-lg px-3 py-0 pl-1"
      leftIcon={
        <span className="relative flex h-8 w-8 shrink-0">
          {avatarUrl ? (
            <img src={avatarUrl} alt={displayName} className="h-8 w-8 rounded-full object-cover" />
          ) : (
            <span
              className="flex h-8 w-8 items-center justify-center rounded-full text-white"
              style={{ background: 'linear-gradient(135deg, rgb(22,93,255), rgb(64,150,255))' }}
            >
              <User className="h-[15px] w-[15px]" aria-hidden />
            </span>
          )}
          {online && (
            <i
              className="absolute rounded-full border-2 border-white bg-[rgb(0,180,42)]"
              style={{ top: 22, right: -2, width: 10, height: 10 }}
              aria-hidden
            />
          )}
        </span>
      }
    >
      <span className="flex flex-col text-left" style={{ lineHeight: 1.2 }}>
        <span className="truncate max-w-[120px] text-[13px] font-semibold text-[rgb(29,33,41)]">{displayName}</span>
        <span className="text-[11px] text-[rgb(134,144,156)]">{online ? '在线' : '离线'}</span>
      </span>
    </Button>
  );
}

export function AccountBadge({ currentUser }: { currentUser?: AccountUser | null } = {}) {
  const { identity, status } = useHumanIdentity();

  // 显式传入时用于壳层集成和测试；未传入时走真实身份 Hook。
  if (currentUser !== undefined) {
    return (
      <ReadyAccountBadge
        displayName={currentUser?.displayName ?? '当前用户'}
        avatarUrl={currentUser?.avatarUrl}
        online={currentUser?.online ?? true}
      />
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
        <span className="flex flex-col text-left" style={{ lineHeight: 1.2 }}>
          <span className="text-[13px] font-semibold text-[rgb(29,33,41)]">加载中…</span>
          <span className="text-[11px] text-[rgb(134,144,156)]">在线</span>
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

  return (
    <ReadyAccountBadge displayName={identity.displayName} avatarUrl={identity.avatarUrl} online={identity.online} />
  );
}

export default AccountBadge;

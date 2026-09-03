// 顶部栏右上角账号身份栏。消费 useHumanIdentity 解析当前登录用户真实花名/头像。
// Open Core：listMyBots human[0]；内部 overlay：staff_id + __TERN__.user。本组件零感知差异。
// 用户状态由其他业务区域表达，顶栏只展示头像和名称。Open Core（无 internal import）。
// 退出登录：仅 Open Core（oauth-provider=阿里云部署）形态渲染；经 useAccountLogout 收口，
// 编排（POST /openapi/v1/auth/logout → 成功刷新；失败 toast）在 useExternalAuth 内。
import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { Avatar } from '@/components/ui/Avatar';
import { useAccountLogout } from '@/hooks/useAccountLogout';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { cn } from '@/utils/cn';
import { Loader2, LogOut, User } from 'lucide-react';
import { useState } from 'react';

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

/** 头像菜单行统一样式（对齐 HelpMenu ROW_CLASS；shell 内本地声明，避免跨文件耦合一行常量）。 */
const MENU_ROW_CLASS =
  'flex w-full items-center gap-2.5 rounded-md border-0 bg-transparent px-3 py-2 !text-sm !font-normal transition-colors hover:bg-muted';

/**
 * ready（已登录）态账号栏。canLogout 时头像 Button 作为 Popover trigger：
 * 菜单 = 身份头行 + 分隔线 + 「退出登录」（isLoggingOut 时 spinner + disabled）。
 */
function ReadyAccountBadge({
  user,
  canLogout,
  isLoggingOut,
  logout,
}: {
  user: AccountUser;
  canLogout: boolean;
  isLoggingOut: boolean;
  logout: () => Promise<void>;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  const badgeButton = (
    <Button
      variant="ghost"
      className="h-auto justify-start gap-2.5 rounded-lg px-3 py-0 pl-1"
      leftIcon={<Avatar name={user.displayName} src={user.avatarUrl} size={32} />}
    >
      <span className="flex text-left" style={{ lineHeight: 1.2 }}>
        <span className="truncate max-w-[120px] text-[13px] font-semibold text-foreground">{user.displayName}</span>
      </span>
    </Button>
  );

  // Open Core 默认 capability 未接退出编排时（仅单元测试/历史形态）纯展示，与内部 ace-gateway 一致。
  if (!canLogout) {
    return badgeButton;
  }

  return (
    <Popover open={menuOpen} onOpenChange={setMenuOpen}>
      <PopoverTrigger asChild>{badgeButton}</PopoverTrigger>
      <PopoverContent align="end" className="w-56 p-1.5">
        <div className="flex items-center gap-2.5 px-3 py-2">
          <Avatar name={user.displayName} src={user.avatarUrl} size={28} />
          <span className="truncate text-sm font-semibold">{user.displayName}</span>
        </div>
        <div className="my-1 h-px bg-border" />
        <button
          type="button"
          className={MENU_ROW_CLASS}
          disabled={isLoggingOut}
          onClick={() => {
            setMenuOpen(false);
            void logout();
          }}
        >
          {isLoggingOut ? (
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />
          ) : (
            <LogOut className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          )}
          <span className="flex-1 text-left">退出登录</span>
        </button>
      </PopoverContent>
    </Popover>
  );
}

export function AccountBadge({ currentUser }: { currentUser?: AccountUser | null } = {}) {
  const { identity, status } = useHumanIdentity();
  const { canLogout, isLoggingOut, logout } = useAccountLogout();

  // 显式传入时用于壳层集成和测试；未传入时走真实身份 Hook。两条路径都挂退出菜单
  // （AppShell 稳定后经 currentUser 注入，只挂一侧会出现菜单闪现/消失）。
  if (currentUser !== undefined) {
    return (
      <ReadyAccountBadge
        user={{ displayName: currentUser?.displayName ?? '当前用户', avatarUrl: currentUser?.avatarUrl }}
        canLogout={canLogout}
        isLoggingOut={isLoggingOut}
        logout={logout}
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

  return (
    <ReadyAccountBadge
      user={{ displayName: identity.displayName, avatarUrl: identity.avatarUrl }}
      canLogout={canLogout}
      isLoggingOut={isLoggingOut}
      logout={logout}
    />
  );
}

export default AccountBadge;

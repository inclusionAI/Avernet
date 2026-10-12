// 侧栏底部用户行账号身份栏（原顶栏右上角，refactor-global-nav-shell 迁入）。消费 useHumanIdentity 解析当前登录用户真实花名/头像。
// Open Core：listMyBots human[0]；内部 overlay：staff_id + __TERN__.user。本组件零感知差异。
// 用户状态由其他业务区域表达，侧栏只展示头像和名称。Open Core（无 internal import）。
// 「个人信息」弹窗（collab-permission-entry-migration AC-12~15）：点击头像/用户名统一打开；
// internal 含工号/部门与部门同步；Open Core 弹窗底部为唯一「退出登录」入口（原 Popover 退出菜单退役）。
// 退出编排（POST /openapi/v1/auth/logout → 成功刷新；失败 toast）经 useAccountLogout 收口在 useExternalAuth 内。
import { Button } from '@/components/ui';
import { Avatar } from '@/components/ui/Avatar';
import { useAccountLogout } from '@/hooks/useAccountLogout';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { PersonalInfoDialog } from '@/shell/PersonalInfoDialog';
import { cn } from '@/utils/cn';
import { Loader2, User } from 'lucide-react';
import { useState } from 'react';

/** 兼容壳层测试/集成注入；生产默认从 useHumanIdentity 读取。 */
export interface AccountUser {
  displayName: string;
  avatarUrl?: string;
}

/** 圆头像位（loading 旋转 / error 灰）。 */
function AvatarIcon({ spinning }: { spinning?: boolean }) {
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-primary to-brand text-primary-foreground">
      {spinning ? (
        <Loader2 className={cn('h-4 w-4 animate-spin')} aria-hidden />
      ) : (
        <User className="h-[15px] w-[15px]" aria-hidden />
      )}
    </span>
  );
}

/**
 * ready（已登录）态账号栏：点击头像/用户名打开「个人信息」弹窗（内外统一交互，不因可否退出改变点击行为）。
 */
function ReadyAccountBadge({
  user,
  canLogout,
  isLoggingOut,
  logout,
  collapsed = false,
}: {
  user: AccountUser;
  canLogout: boolean;
  isLoggingOut: boolean;
  logout: () => Promise<void>;
  /** 折叠态侧栏（w-14 icon 列）：仅渲染头像，弹窗交互保持。 */
  collapsed?: boolean;
}) {
  const [infoOpen, setInfoOpen] = useState(false);
  const openInfo = () => setInfoOpen(true);
  const badgeButton = collapsed ? (
    <Button
      variant="ghost"
      aria-label={user.displayName}
      className="h-10 w-10 justify-center rounded-full p-0"
      onClick={openInfo}
    >
      <Avatar name={user.displayName} src={user.avatarUrl} size={32} />
    </Button>
  ) : (
    <Button
      variant="ghost"
      className="h-auto justify-start gap-2.5 rounded-lg px-3 py-0 pl-1"
      leftIcon={<Avatar name={user.displayName} src={user.avatarUrl} size={32} />}
      onClick={openInfo}
    >
      <span className="flex text-left" style={{ lineHeight: 1.2 }}>
        <span className="truncate max-w-[120px] text-sm font-medium text-foreground">{user.displayName}</span>
      </span>
    </Button>
  );
  return (
    <>
      {badgeButton}
      {infoOpen ? (
        <PersonalInfoDialog
          user={user}
          canLogout={canLogout}
          isLoggingOut={isLoggingOut}
          onLogout={logout}
          onClose={() => setInfoOpen(false)}
        />
      ) : null}
    </>
  );
}

export function AccountBadge({
  currentUser,
  collapsed = false,
}: { currentUser?: AccountUser | null; collapsed?: boolean } = {}) {
  const { identity, status } = useHumanIdentity();
  const { canLogout, isLoggingOut, logout } = useAccountLogout();

  // 显式传入时用于壳层集成和测试；未传入时走真实身份 Hook。两条路径都提供弹窗与退出入口。
  if (currentUser !== undefined) {
    return (
      <ReadyAccountBadge
        user={{ displayName: currentUser?.displayName ?? '当前用户', avatarUrl: currentUser?.avatarUrl }}
        canLogout={canLogout}
        isLoggingOut={isLoggingOut}
        logout={logout}
        collapsed={collapsed}
      />
    );
  }

  // 加载/未登录折叠态：仅渐变圆 icon，不占宽度
  if (collapsed && (status === 'loading' || status === 'error' || !identity)) {
    return (
      <Button variant="ghost" aria-label="账号身份" className="h-10 w-10 justify-center rounded-full p-0">
        <AvatarIcon spinning={status === 'loading'} />
      </Button>
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
          <span className="text-sm font-medium text-foreground">加载中…</span>
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
          <span className="text-sm font-medium text-content-soft">未登录</span>
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
      collapsed={collapsed}
    />
  );
}

export default AccountBadge;

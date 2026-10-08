/**
 * 左下角账号身份「个人信息」弹窗（collab-permission-entry-migration AC-12~15）：
 * 内外统一交互（点击头像/用户名打开）；internal 展示工号/部门路径与「同步用户部门信息」
 * icon 按钮（IconButton + RefreshCw 现状形态，D13）；Open Core 无工号/部门行（认证身份零 org 请求），
 * 并在弹窗底部提供唯一「退出登录」入口（原 Popover 退出菜单随本迁移退役，D12 方案 2）。
 * 数据面复用协作权限域 currentUser 加载范围与部门刷新链路（不请求 Bot 列表）。
 */
import { getCapabilities } from '@/capabilities';
import { IdentityCard } from '@/components/CollaborationPrivacy/IdentityCard';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { useCollaborationPrivacy } from '@/hooks/useCollaborationPrivacy';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { LogOut, UserRound } from 'lucide-react';

export interface PersonalInfoUser {
  displayName: string;
  avatarUrl?: string;
}

interface PersonalInfoDialogProps {
  user: PersonalInfoUser;
  /** Open Core（oauth-provider）渲染退出按钮；internal（ace-gateway）无退出（useAccountLogout 口径）。 */
  canLogout: boolean;
  isLoggingOut: boolean;
  onLogout: () => Promise<void>;
  onClose: () => void;
}

export function PersonalInfoDialog({ user, canLogout, isLoggingOut, onLogout, onClose }: PersonalInfoDialogProps) {
  const privacy = useCollaborationPrivacy();
  const { identity: accountIdentity } = useHumanIdentity();
  const presentation = getCapabilities().getUserProfilePresentation().value;
  const overview = privacy.overview;
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <ModalContent size="md">
        <ModalHeader>
          <ModalTitle>个人信息</ModalTitle>
        </ModalHeader>
        {privacy.loading ? (
          <div aria-label="正在加载个人信息" className="py-2">
            <Skeleton.Card />
          </div>
        ) : overview ? (
          <IdentityCard
            identity={overview.currentUser}
            avatarUrl={accountIdentity?.avatarUrl ?? user.avatarUrl}
            authenticatedIdentity={
              presentation.preferAuthenticatedUserProfile ? accountIdentity ?? undefined : undefined
            }
            showDepartment={presentation.showDepartment}
            // Open Core 无工号/部门（AC-13）：工号行随部门展示口径同开同关。
            showEmployeeNumber={presentation.showDepartment}
            syncing={privacy.busyAction === 'syncDepartment'}
            onSync={() => void privacy.syncDepartment()}
          />
        ) : (
          <Empty
            title="个人信息加载失败"
            description={privacy.error ?? '用户信息暂不可用'}
            icon={<UserRound className="h-5 w-5" aria-hidden />}
            action={
              <Button variant="secondary" onClick={() => void privacy.load()}>
                重试
              </Button>
            }
          />
        )}
        {canLogout ? (
          <ModalFooter>
            <Button
              variant="outline"
              disabled={isLoggingOut}
              leftIcon={<LogOut className="h-4 w-4" aria-hidden />}
              onClick={() => void onLogout()}
            >
              退出登录
            </Button>
          </ModalFooter>
        ) : null}
      </ModalContent>
    </Modal>
  );
}

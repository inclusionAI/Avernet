import type { HumanIdentity } from '@/capabilities';
import { Avatar } from '@/components/ui/Avatar';
import { Card, CardContent } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/IconButton';
import type { CurrentUserIdentity } from '@/domain/collaborationPrivacy/types';
import { RefreshCw } from 'lucide-react';

interface IdentityCardProps {
  identity: CurrentUserIdentity;
  avatarUrl?: string;
  authenticatedIdentity?: HumanIdentity;
  showDepartment?: boolean;
  /** 是否展示工号行；Open Core「个人信息」弹窗按形态关闭（collab-permission-entry-migration AC-13），页面默认展示。 */
  showEmployeeNumber?: boolean;
  syncing: boolean;
  onSync: () => void;
}

export function IdentityCard({
  identity,
  avatarUrl,
  authenticatedIdentity,
  showDepartment = true,
  showEmployeeNumber = true,
  syncing,
  onSync,
}: IdentityCardProps) {
  const displayName =
    authenticatedIdentity?.displayName.trim() || authenticatedIdentity?.userId.trim() || identity.displayName;
  const employeeNumber = authenticatedIdentity?.userId.trim() || identity.employeeNumber;
  return (
    <Card>
      <CardContent>
        <div className="flex min-w-0 items-start gap-3">
          <Avatar name={displayName} src={avatarUrl} size={44} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <p className="m-0 text-base font-semibold text-foreground">{displayName}</p>
              {showEmployeeNumber ? <span className="text-xs text-muted-foreground">工号 {employeeNumber}</span> : null}
            </div>
            {showDepartment ? (
              <div className="mt-1 flex min-w-0 items-start gap-1">
                <p className="m-0 min-w-0 break-words text-xs leading-5 text-muted-foreground">
                  {identity.departmentPath.length > 0 ? identity.departmentPath.join(' / ') : '暂无部门信息'}
                </p>
                <IconButton
                  label="同步用户部门信息"
                  icon={syncing ? null : <RefreshCw className="h-3.5 w-3.5" aria-hidden />}
                  size="sm"
                  className="h-5 w-5 shrink-0 rounded-md p-0"
                  loading={syncing}
                  onClick={onSync}
                />
              </div>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

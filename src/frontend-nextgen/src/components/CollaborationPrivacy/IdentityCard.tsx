import { Avatar } from '@/components/ui/Avatar';
import { Card, CardContent } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/IconButton';
import type { CurrentUserIdentity } from '@/domain/collaborationPrivacy/types';
import { RefreshCw } from 'lucide-react';

interface IdentityCardProps {
  identity: CurrentUserIdentity;
  avatarUrl?: string;
  syncing: boolean;
  onSync: () => void;
}

export function IdentityCard({ identity, avatarUrl, syncing, onSync }: IdentityCardProps) {
  return (
    <Card>
      <CardContent>
        <div className="flex min-w-0 items-start gap-3">
          <Avatar name={identity.displayName} src={avatarUrl} size={44} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <p className="m-0 text-base font-semibold text-foreground">{identity.displayName}</p>
              <span className="text-xs text-muted-foreground">工号 {identity.employeeNumber}</span>
            </div>
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
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

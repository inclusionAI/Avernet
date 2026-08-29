import { Card, CardContent } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/IconButton';
import type { CurrentUserIdentity } from '@/domain/collaborationPrivacy/types';
import { RefreshCw, UserRound } from 'lucide-react';

interface IdentityCardProps {
  identity: CurrentUserIdentity;
  syncing: boolean;
  onSync: () => void;
}

export function IdentityCard({ identity, syncing, onSync }: IdentityCardProps) {
  return (
    <Card>
      <CardContent>
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[var(--color-primary-soft)] text-[var(--color-primary)]">
            <UserRound className="h-5 w-5" aria-hidden />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <p className="m-0 font-medium text-[var(--color-fg)]">{identity.displayName}</p>
              <span className="text-xs text-[var(--color-muted)]">工号 {identity.employeeNumber}</span>
            </div>
            <div className="mt-1 flex items-center gap-1">
              <p className="m-0 text-sm leading-6 text-[var(--color-muted)]">
                {identity.departmentPath.length > 0 ? identity.departmentPath.join(' / ') : '暂无部门信息'}
              </p>
              <IconButton
                label="同步用户部门信息"
                icon={syncing ? null : <RefreshCw className="h-3.5 w-3.5" aria-hidden />}
                size="sm"
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

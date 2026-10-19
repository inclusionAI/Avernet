import { IdentityAvatar } from '@/components/Workspace/IdentitySelector/IdentitySelectorParts';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';

/** 公开群固定使用登录用户，不读取或修改 Bot 身份选择状态。 */
export function GroupIdentityDisplay() {
  const { identity, status } = useHumanIdentity();
  const name = identity?.displayName || identity?.userId || (status === 'loading' ? '正在加载用户' : '当前用户');
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            role="group"
            aria-label={`当前协作身份：${name}`}
            aria-disabled="true"
            tabIndex={0}
            className="flex h-9 w-full max-w-[280px] shrink-0 cursor-not-allowed items-center gap-2 rounded-lg border border-input bg-muted px-2.5 text-xs text-muted-foreground"
          >
            <IdentityAvatar
              identity={{ id: identity?.userId || 'user', name, kind: 'user', avatar: name }}
              userAvatarUrl={identity?.avatarUrl}
              size="xs"
            />
            <span className="min-w-0 flex-1 truncate">{name}</span>
            <span className="shrink-0 rounded-md bg-primary/10 px-2 py-0.5 text-xs font-normal text-primary">用户</span>
          </div>
        </TooltipTrigger>
        <TooltipContent>公开协作群不支持切换bot身份</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

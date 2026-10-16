// v2 协作群侧栏收起竖轨（56px）：群头像竖排 + tooltip，点击展开侧栏并选中该群；
// 顶部「+」发起协作、底部展开按钮。形态对齐对话 ConversationCollapsedRail（头像列
// 无贯穿线），但语义取 spec 裁决（collaboration-sidebar）：群头像是「展开+直达」，
// 不做收起态挂机径，也不设溢出封顶（群列表数量级远小于会话）。
import { Button, IconButton, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { GroupView } from '@/domain/collaboration/types';
import { ChevronRight, Plus, Users } from 'lucide-react';
import { AvatarTile } from '../AvatarTile';

export interface GroupSidebarCollapsedRailProps {
  groups: GroupView[];
  selectedGroupId: string | null;
  /** 头像直达：选中该群并请求展开完整侧栏。 */
  onOpenGroup(groupId: string): void;
  /** 顶部「+」：发起协作（既有 CreateGroupModal 链路）；restricted 单群视图不传则不渲染。 */
  onCreateGroup?(): void;
  /** 底部入口：仅展开侧栏不改变选中。 */
  onExpand(): void;
}

export function GroupSidebarCollapsedRail({
  groups,
  selectedGroupId,
  onOpenGroup,
  onCreateGroup,
  onExpand,
}: GroupSidebarCollapsedRailProps) {
  return (
    <TooltipProvider delayDuration={300}>
      <nav aria-label="协作群（收起态）" className="app-scrollbar flex h-full w-full flex-col items-center gap-1 overflow-y-auto px-2 py-3">
        {onCreateGroup && (
          <Tooltip>
            <TooltipTrigger asChild>
              <IconButton
                label="发起协作"
                icon={<Plus className="h-4 w-4" />}
                variant="ghost"
                className="h-9 w-9 rounded-full text-primary hover:bg-primary/10 hover:text-primary"
                onClick={onCreateGroup}
              />
            </TooltipTrigger>
            <TooltipContent side="right">发起协作</TooltipContent>
          </Tooltip>
        )}
        {groups.map((group) => {
          const selected = selectedGroupId === group.groupId;
          return (
            <Tooltip key={group.groupId}>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  aria-label={`打开协作群 ${group.name}`}
                  aria-current={selected ? 'true' : undefined}
                  className="relative h-9 w-9 rounded-full hover:bg-muted"
                  onClick={() => onOpenGroup(group.groupId)}
                >
                  <AvatarTile
                    label={group.name}
                    className="h-7 w-7 rounded-full"
                    fallbackContent={<Users className="h-3.5 w-3.5" aria-hidden="true" />}
                  />
                  {selected && (
                    <span
                      aria-hidden="true"
                      className="absolute -left-1 top-1/2 h-3.5 w-1 -translate-y-1/2 rounded-full bg-primary"
                    />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">{group.name}</TooltipContent>
            </Tooltip>
          );
        })}
        {groups.length === 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                aria-label="展开侧栏，查看协作群"
                className="h-9 w-9 rounded-full border border-dashed border-border text-muted-foreground hover:bg-muted"
                onClick={onExpand}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">暂无协作群，展开查看</TooltipContent>
          </Tooltip>
        )}
        <Button
          variant="ghost"
          aria-label="展开协作群列表"
          className="mt-auto h-8 w-8 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={onExpand}
        >
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </nav>
    </TooltipProvider>
  );
}
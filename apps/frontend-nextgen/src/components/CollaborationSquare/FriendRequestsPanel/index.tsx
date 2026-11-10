import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import type { FriendRequestActor, SquareResource } from '@/domain/collaborationSquare/types';
import { FRIEND_CONNECTION_REQUESTS_PAGE_SIZE } from '@/services/collaborationSquare/friendConnectionRequestService';
import { useFriendConnectionRequests, type FriendConnectionRequestsView } from '@/hooks/useFriendConnectionRequests';
import { UsersRound, X } from 'lucide-react';
import { useState } from 'react';

export interface FriendRequestsPanelProps {
  resource: SquareResource;
  /** 当前所选操作角色；无角色时不渲染入口（Human 只看本人，Bot 管理权限由 BCS 复验）。 */
  actor: FriendRequestActor | null;
  /** 登录用户 userId，参与操作代次 scopeKey。 */
  userId?: string;
  /** 仅决定初始展开态（审批深链直达时使用）；之后开合仍由用户交互控制。 */
  defaultOpen?: boolean;
  /** 审批／撤销成功后刷新广场卡片关系（由页面层注入，发起前经代次守卫核验）。 */
  onRelationshipsChanged?: () => void;
}

type RequestWriteAction = 'accept' | 'reject' | 'cancel';

function RequestListFooter({ view }: { view: FriendConnectionRequestsView }) {
  const totalPages = Math.max(1, Math.ceil(view.total / FRIEND_CONNECTION_REQUESTS_PAGE_SIZE));
  if (totalPages <= 1) return null;
  return (
    <div className="mt-2 flex items-center justify-end gap-2">
      <Button
        variant="ghost"
        size="sm"
        disabled={view.page <= 1 || view.loading}
        onClick={() => view.gotoPage(view.page - 1)}
      >
        上一页
      </Button>
      <span className="text-xs text-muted-foreground">
        第 {view.page} / {totalPages} 页
      </span>
      <Button
        variant="ghost"
        size="sm"
        disabled={view.page >= totalPages || view.loading}
        onClick={() => view.gotoPage(view.page + 1)}
      >
        下一页
      </Button>
    </div>
  );
}

function RequestsSection({
  title,
  view,
  busyRequestIds,
  operation,
  onWrite,
}: {
  title: string;
  view: FriendConnectionRequestsView;
  busyRequestIds: string[];
  operation: 'review' | 'cancel';
  onWrite: (action: RequestWriteAction, requestId: string) => void;
}) {
  const acceptBusy = (requestId: string) => busyRequestIds.includes(`accept:${requestId}`);
  const rejectBusy = (requestId: string) => busyRequestIds.includes(`reject:${requestId}`);
  const cancelBusy = (requestId: string) => busyRequestIds.includes(`cancel:${requestId}`);
  return (
    <section aria-label={title} className="min-w-0">
      <h4 className="m-0 text-xs font-semibold tracking-wide text-muted-foreground">{title}</h4>
      {view.loading && <p className="m-0 mt-2 text-xs text-muted-foreground">正在加载申请...</p>}
      {view.error && !view.loading && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <p className="m-0 text-xs text-destructive">{view.error}</p>
          <Button variant="secondary" size="sm" onClick={view.reload}>
            重试
          </Button>
        </div>
      )}
      {!view.loading && !view.error && view.items.length === 0 && (
        <p className="m-0 mt-2 text-xs text-muted-foreground">暂无待处理申请</p>
      )}
      <ul className="m-0 mt-2 flex list-none flex-col gap-2 p-0">
        {view.items.map((item) => (
          <li
            key={item.requestId}
            className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-lg border border-divider bg-background px-3 py-2"
          >
            <div className="min-w-0">
              <p className="m-0 truncate text-sm text-foreground">
                {operation === 'review' ? `来自 ${item.fromActorId} 的好友申请` : `向 ${item.toActorId} 发出的好友申请`}
              </p>
              {item.message && <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">{item.message}</p>}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {operation === 'review' ? (
                <>
                  <Button
                    size="sm"
                    loading={acceptBusy(item.requestId)}
                    disabled={rejectBusy(item.requestId)}
                    onClick={() => onWrite('accept', item.requestId)}
                  >
                    通过
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={rejectBusy(item.requestId)}
                    disabled={acceptBusy(item.requestId)}
                    onClick={() => onWrite('reject', item.requestId)}
                  >
                    拒绝
                  </Button>
                </>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  loading={cancelBusy(item.requestId)}
                  onClick={() => onWrite('cancel', item.requestId)}
                >
                  撤销
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
      <RequestListFooter view={view} />
    </section>
  );
}

/**
 * 发现页（公开 Bot）所选角色的「好友申请」入口与申请处理面板
 * （bcs-standalone-friend-discovery Task 4）。组件不编排 Controller：
 * 列表读取与通过／拒绝／撤销由 {@link useFriendConnectionRequests} 完成，
 * 这里只呈现发出／收到两向分页列表及其操作按钮。
 */
export function FriendRequestsPanel({
  resource,
  actor,
  userId,
  defaultOpen = false,
  onRelationshipsChanged,
}: FriendRequestsPanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  const model = useFriendConnectionRequests({ resource, actor, userId, enabled: open, onRelationshipsChanged });
  if (!actor) return null;
  const handleWrite = (action: RequestWriteAction, requestId: string) => {
    if (action === 'accept') model.accept(requestId);
    else if (action === 'reject') model.reject(requestId);
    else model.cancel(requestId);
  };
  return (
    <div className="flex flex-col gap-2" data-guide-target="discover-friend-requests">
      <Button
        variant="secondary"
        size="sm"
        className="self-start"
        leftIcon={<UsersRound aria-hidden className="h-4 w-4" />}
        onClick={() => setOpen((previous) => !previous)}
      >
        好友申请
      </Button>
      {open && (
        <Card className="space-y-4 p-4">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <div className="min-w-0">
              <h3 className="m-0 text-sm font-semibold text-foreground">好友申请</h3>
              <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">
                当前角色：{actor.type === 'human' ? '用户' : 'Bot'} {actor.id}
              </p>
            </div>
            <Button variant="ghost" size="icon" aria-label="收起好友申请" onClick={() => setOpen(false)}>
              <X aria-hidden className="h-4 w-4" />
            </Button>
          </div>
          <RequestsSection
            title="收到的申请"
            view={model.received}
            busyRequestIds={model.busyRequestIds}
            operation="review"
            onWrite={handleWrite}
          />
          <RequestsSection
            title="发出的申请"
            view={model.sent}
            busyRequestIds={model.busyRequestIds}
            operation="cancel"
            onWrite={handleWrite}
          />
        </Card>
      )}
    </div>
  );
}

// 通知中心：130px 纵向视图导航 + 分类筛选 + 无边框消息列表，保留审批和已读业务链路。
// 视觉对齐 admin 视觉交互指南 §7.2/§7.4/§8。切视图/分类回第 1 页；详情用 typeLabel/statusLabel 本地化。
import notificationEmpty from '@/assets/notification-center/empty.png';
import { Button, Card, Pagination, Skeleton } from '@/components/ui';
import type { WorkOrder, WorkOrderCategory, WorkOrderView } from '@/domain/admin/models';
import { isSameWorkOrderItem, markNotificationReadInList } from '@/domain/admin/workOrderRead';
import { useNotifications } from '@/hooks/useNotifications';
import { useWorkOrders } from '@/hooks/useWorkOrders';
import { useWorkOrderStore } from '@/stores/workOrderStore';
import { extractFriendlyErrorMessage } from '@/utils/requestErrorHandler';
import { useNavigate } from '@umijs/max';
import { useState } from 'react';
import { toast } from 'sonner';
import { MiniSegmented } from '../Tabs';
import { WorkOrderCard } from '../WorkOrderCard';
import { WorkOrderDetailDrawer } from './WorkOrderDetailDrawer';

const VIEWS: { value: WorkOrderView; label: string }[] = [
  { value: 'pending_mine', label: '待我处理' },
  { value: 'initiated_mine', label: '我发起的' },
  { value: 'processed', label: '已处理' },
];

const CATEGORIES: { value: WorkOrderCategory; label: string }[] = [
  { value: 'APPROVAL', label: '审批类' },
  { value: 'NOTIFICATION', label: '通知类' },
];

const EMPTY_TEXT: Record<WorkOrderView, string> = {
  pending_mine: '暂无通知',
  initiated_mine: '暂无申请',
  processed: '暂无通知',
};

export function WorkOrderTabs() {
  const {
    view,
    category,
    pageNo,
    pageSize,
    items,
    total,
    loading,
    error,
    setView,
    setCategory,
    setPageNo,
    setPageSize,
    fetchList,
    approve,
    reject,
    getDetail,
    getNotificationDetail,
  } = useWorkOrders();
  const navigate = useNavigate();
  const { unreadCount, markAllRead, markOneRead, refreshUnread } = useNotifications();
  const [detail, setDetail] = useState<WorkOrder | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  // 全部已读 in-flight 态：按钮 loading 防抖，避免连点重复调用 markAllRead 接口。
  const [markingAll, setMarkingAll] = useState(false);
  const handleMarkAllRead = async () => {
    if (markingAll) return;
    setMarkingAll(true);
    try {
      await markAllRead();
      await fetchList();
    } finally {
      setMarkingAll(false);
    }
  };

  const openDetail = async (wo: WorkOrder) => {
    // BBS notices are actionable links: open the exact topic/post instead of the
    // generic JSON detail drawer. Keep navigation available if marking read fails.
    if (wo.bbsTarget?.topicId) {
      const { topicId, postId } = wo.bbsTarget;
      if (!wo.isRead) await markOneRead(wo.notificationId);
      const query = new URLSearchParams({ topicId });
      if (postId) query.set('postId', postId);
      void refreshUnread(true);
      navigate(`/lab/community?${query.toString()}`);
      return;
    }
    setDetail(wo);
    // 审批类：走工单详情（GET /openapi/v1/work-orders/{work_order_id}）补 apply_reason / review_remark 等
    // 行内看不到的字段；不动已读态（PENDING 审批读过仍要留在「待我处理」，由「标记为已读」显式触发）。
    if (wo.itemType === 'APPROVAL') {
      setDetailLoading(true);
      const r = await getDetail(wo.workOrderId);
      if (r.data) setDetail(r.data);
      if (r.error) toast.error(extractFriendlyErrorMessage(r.error));
      setDetailLoading(false);
      return;
    }
    // 通知类：拉通知详情（GET /openapi/v1/work-order-notifications/{notification_id}）。
    // 后端契约：查询通知详情时服务端自动将该通知标记为已读（后端接口文档 §7.7），
    // 故此处不再单独调用 markNotificationRead；详情成功后仅刷新角标 + 本地置 isRead。
    // 不 removeItem：查看不应让消息从当前列表消失（PRD openNotifDetail 仅 markAsRead，不删列表项）；
    // 已读项的归类由后端 query_type 在下次 fetchList 时自然完成（待我处理→已处理）。
    if (wo.itemType === 'NOTIFICATION' && wo.notificationId) {
      setDetailLoading(true);
      const r = await getNotificationDetail(wo.notificationId);
      if (r.data) {
        setDetail(r.data);
        void refreshUnread(true);
        setDetail((prev) => (prev ? { ...prev, isRead: true } : prev));
        // 同步列表项 isRead（待我处理视图内该条变 opacity-75 + 已查看，刷新后归到已处理）。
        // 通知类行的 workOrderId 恒为 0（List VO 契约），必须按 notificationId 定位本条，
        // 否则一次查看会把同列表所有通知一起点亮成已查看。
        useWorkOrderStore.getState().setList(markNotificationReadInList(items, wo.notificationId), total);
      }
      if (r.error) toast.error(extractFriendlyErrorMessage(r.error));
      setDetailLoading(false);
    }
  };

  // 下一条未读通知（当前视图内、非当前、未读的通知类）—Drawer「下一条未读」导航用。
  // 同一性走 isSameWorkOrderItem（通知类按 notificationId）：通知类 workOrderId 恒为 0 不能用来排除当前条。
  const nextUnread = detail
    ? items.find((wo) => wo.itemType === 'NOTIFICATION' && !wo.isRead && !isSameWorkOrderItem(wo, detail))
    : undefined;

  const openNextUnread = () => {
    if (nextUnread) {
      void openDetail(nextUnread);
    } else {
      toast.info('没有更多未读通知了');
    }
  };

  const totalCount = total ?? 0;

  return (
    <div className="flex min-h-full flex-col gap-5 sm:flex-row">
      <nav aria-label="通知视图" className="flex shrink-0 gap-0.5 sm:w-[130px] sm:flex-col">
        {VIEWS.map((option) => (
          <Button
            key={option.value}
            variant="ghost"
            aria-current={view === option.value ? 'page' : undefined}
            className={`h-8 justify-start rounded-md px-3 text-[13px] font-normal ${
              view === option.value ? 'bg-muted text-foreground' : 'text-muted-foreground'
            }`}
            onClick={() => {
              setView(option.value);
              setPageNo(1);
            }}
          >
            {option.label}
          </Button>
        ))}
      </nav>

      <section className="min-w-0 flex-1" aria-label="通知列表">
        <div className="mb-3 flex min-h-8 flex-wrap items-center justify-between gap-3">
          <MiniSegmented<WorkOrderCategory>
            value={category}
            onChange={(c) => {
              setCategory(c);
              setPageNo(1);
            }}
            options={CATEGORIES}
            className="h-8 w-[132px] shrink-0 [&>button]:flex-1"
          />
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            {view === 'pending_mine' ? (
              <>
                <span>
                  当前筛选共 {totalCount} 条消息，待处理 {unreadCount} 条
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-auto p-0 text-xs font-normal hover:bg-transparent"
                  disabled={unreadCount === 0 || markingAll}
                  loading={markingAll}
                  onClick={() => void handleMarkAllRead()}
                >
                  标记为全部已读
                </Button>
              </>
            ) : (
              <span>共 {totalCount} 条记录</span>
            )}
          </div>
        </div>

        {/* 列表 */}
        {/* 骨架行结构对齐真实 WorkOrderCard(无 Skeleton.ListItem 自带 p-3,loading↔loaded 不左跳) */}
        {loading ? (
          <div>
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 border-b border-divider py-4 last:border-b-0">
                <div className="flex-1 space-y-2">
                  <Skeleton.Line className="w-2/5" />
                  <Skeleton.Line className="w-4/5" />
                </div>
                <Skeleton.Block className="h-8 w-16 shrink-0 rounded-md" />
              </div>
            ))}
          </div>
        ) : error ? (
          <Card className="m-5 mt-0 flex items-center justify-between gap-3 border-destructive/30 bg-destructive/5 p-4 shadow-none">
            <div className="min-w-0 text-sm text-destructive">{extractFriendlyErrorMessage(error)}</div>
            <Button size="sm" variant="primary" onClick={() => void fetchList()}>
              重试
            </Button>
          </Card>
        ) : items.length === 0 ? (
          <div className="flex min-h-[440px] items-center justify-center sm:min-h-[calc(100dvh-180px)]" role="status">
            <div className="flex flex-col items-center gap-6">
              <img src={notificationEmpty} width={150} height={107} alt="" aria-hidden="true" />
              <p className="m-0 text-sm text-muted-foreground">{EMPTY_TEXT[view]}</p>
            </div>
          </div>
        ) : (
          <>
            <div>
              {items.map((wo) => (
                <WorkOrderCard
                  key={`${wo.itemId}-${wo.workOrderId}`}
                  workOrder={wo}
                  onApprove={(id) => approve(id)}
                  onReject={(id, remark) => void reject(id, remark)}
                  onView={openDetail}
                />
              ))}
            </div>
            {/* 分页：接入统一 Pagination 组件（计数 + 上/下一页图标按钮，右对齐） */}
            <div className="flex items-center justify-end py-4">
              <Pagination
                current={pageNo}
                pageSize={pageSize}
                total={totalCount}
                onChange={setPageNo}
                onPageSizeChange={setPageSize}
              />
            </div>
          </>
        )}
      </section>

      <WorkOrderDetailDrawer
        detail={detail}
        open={!!detail}
        loading={detailLoading}
        nextUnread={nextUnread}
        onClose={() => setDetail(null)}
        onNextUnread={openNextUnread}
      />
    </div>
  );
}

export default WorkOrderTabs;

// 工单中心：card 视图 tab + 分类 Segmented + 全部已读 + 列表（骨架/空态/错误）+ 分页 + 工单/通知详情 Drawer。
// 视觉对齐 admin 视觉交互指南 §7.2/§7.4/§8。切视图/分类回第 1 页；详情用 typeLabel/statusLabel 本地化。
import { Button, Card, Empty, Pagination, Skeleton } from '@/components/ui';
import type { WorkOrder, WorkOrderCategory, WorkOrderView } from '@/domain/admin/models';
import { useNotifications } from '@/hooks/useNotifications';
import { useWorkOrders } from '@/hooks/useWorkOrders';
import { useWorkOrderStore } from '@/stores/workOrderStore';
import { extractFriendlyErrorMessage } from '@/utils/requestErrorHandler';
import { CheckCircle } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { CardTabs, MiniSegmented } from '../Tabs';
import { WorkOrderCard } from '../WorkOrderCard';
import { WorkOrderDetailDrawer } from './WorkOrderDetailDrawer';

const VIEWS: { value: WorkOrderView; label: string }[] = [
  { value: 'pending_mine', label: '待我处理' },
  { value: 'initiated_mine', label: '我发起的' },
  { value: 'processed', label: '已处理' },
];

const CATEGORIES: { value: WorkOrderCategory; label: string }[] = [
  { value: 'ALL', label: '全部' },
  { value: 'APPROVAL', label: '审批类' },
  { value: 'NOTIFICATION', label: '通知类' },
];

const EMPTY_TEXT: Record<WorkOrderView, string> = {
  pending_mine: '暂无待处理工单',
  initiated_mine: '暂无申请',
  processed: '暂无已处理记录',
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
  const { unreadCount, markAllRead, refreshUnread } = useNotifications();
  const [detail, setDetail] = useState<WorkOrder | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const openDetail = async (wo: WorkOrder) => {
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
        // 同步列表项 isRead（待我处理视图内该条变 opacity-75 + 已查看，刷新后归到已处理）
        useWorkOrderStore.getState().setList(
          items.map((it) => (it.workOrderId === wo.workOrderId ? { ...it, isRead: true } : it)),
          total,
        );
      }
      if (r.error) toast.error(extractFriendlyErrorMessage(r.error));
      setDetailLoading(false);
    }
  };

  // 下一条未读通知（当前视图内、非当前、未读的通知类）—Drawer「下一条未读」导航用。
  const nextUnread = detail
    ? items.find((wo) => wo.itemType === 'NOTIFICATION' && !wo.isRead && wo.workOrderId !== detail.workOrderId)
    : undefined;

  const openNextUnread = () => {
    if (nextUnread) {
      void openDetail(nextUnread);
    } else {
      toast.info('没有更多未读通知了');
    }
  };

  const totalCount = total ?? 0;

  const viewOptions = VIEWS.map((vw) => ({
    value: vw.value,
    label:
      vw.value === 'pending_mine' && unreadCount > 0 ? (
        <span className="inline-flex items-center gap-1.5">
          {vw.label}
          <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1 text-xs font-normal text-primary-foreground">
            {unreadCount}
          </span>
        </span>
      ) : (
        vw.label
      ),
  }));

  return (
    <div className="mx-auto max-w-[1200px]">
      {/* card tab 头 */}
      <CardTabs<WorkOrderView>
        value={view}
        options={viewOptions}
        onChange={(v) => {
          setView(v);
          setPageNo(1);
        }}
        className="mb-3"
      />

      {/* 内容面板：独立卡片，与上方 card tab 留 12px 间距，左右边缘与 tab 行对齐 */}
      <Card className="overflow-hidden bg-card shadow-sm">
        {/* 分类筛选 + 全部已读 */}
        <div className="flex items-center justify-between gap-3 px-5 py-4">
          <MiniSegmented<WorkOrderCategory>
            value={category}
            onChange={(c) => {
              setCategory(c);
              setPageNo(1);
            }}
            options={CATEGORIES}
          />
          {view === 'pending_mine' ? (
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground">共 {unreadCount} 条未读通知</span>
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<CheckCircle size={14} />}
                disabled={unreadCount === 0}
                onClick={() => void markAllRead()}
              >
                全部标记为已读
              </Button>
            </div>
          ) : view === 'initiated_mine' ? (
            <span className="text-xs text-muted-foreground">共 {totalCount} 条申请记录</span>
          ) : view === 'processed' ? (
            <span className="text-xs text-muted-foreground">共 {totalCount} 条已处理记录</span>
          ) : null}
        </div>

        {/* 列表 */}
        {/* 骨架行结构对齐真实 WorkOrderCard(无 Skeleton.ListItem 自带 p-3,loading↔loaded 不左跳) */}
        {loading ? (
          <div className="px-5 pb-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 border-b border-border py-4 last:border-b-0">
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
          <div className="p-5 pt-0">
            <Empty
              title={EMPTY_TEXT[view]}
              description="当前视图没有工单记录"
              action={
                category !== 'ALL' ? (
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => {
                      setCategory('ALL');
                      setPageNo(1);
                    }}
                  >
                    清除筛选
                  </Button>
                ) : undefined
              }
            />
          </div>
        ) : (
          <>
            <div className="px-5 pb-2">
              {items.map((wo) => (
                <WorkOrderCard
                  key={`${wo.itemId}-${wo.workOrderId}`}
                  workOrder={wo}
                  onApprove={(id) => void approve(id)}
                  onReject={(id, remark) => void reject(id, remark)}
                  onView={openDetail}
                />
              ))}
            </div>
            {/* 分页：接入统一 Pagination 组件（计数 + 上/下一页图标按钮，右对齐） */}
            <div className="flex items-center justify-end border-t border-border px-5 py-4">
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
      </Card>

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

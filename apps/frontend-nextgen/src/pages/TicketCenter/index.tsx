// 通知中心独立页（split-admin-space-ticket-pages）：原 /admin 单页「通知中心」Tab 拆为独立路由 /ticket-center，
// 页面体原样复用 AdminWorkOrdersView，不占导航位——入口见 NotificationBell 弹层「查看全部」。
// ?category= 深链在挂载时写入 workOrderStore 分类筛选（旧 /admin?tab=work-orders&category= 的 category
// 参数此前从未被消费，本 change 让分类深链真正生效）；非法/缺省值不动 store 默认 ALL。
import type { WorkOrderCategory } from '@/domain/admin/models';
import { useWorkOrders } from '@/hooks/useWorkOrders';
import { useSearchParams } from '@umijs/max';
import { useEffect } from 'react';
import { AdminWorkOrdersView } from '../Admin/WorkOrders';

const VALID_CATEGORIES: readonly WorkOrderCategory[] = ['APPROVAL', 'NOTIFICATION'];

function TicketCenterPage() {
  const [searchParams] = useSearchParams();
  const { setCategory } = useWorkOrders();

  useEffect(() => {
    // 深链消费仅在挂载时执行一次；页内分类切换以 store 为准，不回写 URL（保持旧行为）。
    const raw = searchParams.get('category');
    if (raw && (VALID_CATEGORIES as readonly string[]).includes(raw)) setCategory(raw as WorkOrderCategory);
  }, [searchParams, setCategory]);

  return (
    <div className="flex h-full flex-col">
      {/* 内容区容器与原 /admin 单页一致：白底继承 AppShell，灰底内边距滚动区 */}
      <div className="min-h-0 flex-1 overflow-y-auto p-6 pb-10">
        <AdminWorkOrdersView />
      </div>
    </div>
  );
}

export default TicketCenterPage;

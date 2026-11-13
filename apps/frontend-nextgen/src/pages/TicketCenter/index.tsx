// 通知中心独立页（split-admin-space-ticket-pages）：原 /admin 单页「通知中心」Tab 拆为独立路由 /ticket-center，
// 页面体原样复用 AdminWorkOrdersView，不占导航位——入口见 NotificationBell 弹层「查看全部」。
// ?category= 深链在挂载时写入 workOrderStore 分类筛选（旧 /admin?tab=work-orders&category= 的 category
// 参数此前从未被消费，本 change 让分类深链真正生效）；非法/缺省值不动 store 默认审批类。
import { Button } from '@/components/ui';
import type { WorkOrderCategory } from '@/domain/admin/models';
import { useWorkOrders } from '@/hooks/useWorkOrders';
import { useNavigate, useSearchParams } from '@umijs/max';
import { ArrowLeft } from 'lucide-react';
import { useEffect } from 'react';
import { AdminWorkOrdersView } from '../Admin/WorkOrders';

const VALID_CATEGORIES: readonly WorkOrderCategory[] = ['APPROVAL', 'NOTIFICATION'];

function TicketCenterPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { setCategory } = useWorkOrders();

  useEffect(() => {
    // 深链消费仅在挂载时执行一次；页内分类切换以 store 为准，不回写 URL（保持旧行为）。
    const raw = searchParams.get('category');
    if (raw && (VALID_CATEGORIES as readonly string[]).includes(raw)) setCategory(raw as WorkOrderCategory);
  }, [searchParams, setCategory]);

  return (
    <div className="flex h-full flex-col bg-background">
      <header className="flex shrink-0 items-center gap-2.5 px-5 py-5">
        <Button variant="ghost" size="icon" className="h-6 w-6" aria-label="返回" onClick={() => navigate(-1)}>
          <ArrowLeft className="h-5 w-5" aria-hidden />
        </Button>
        <h1 className="m-0 text-2xl font-semibold leading-[34px]">通知中心</h1>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        <AdminWorkOrdersView />
      </div>
    </div>
  );
}

export default TicketCenterPage;

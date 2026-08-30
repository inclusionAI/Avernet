// 工单中心视图。容器宽度由 WorkOrderTabs 自带 max-w-[1200px]，此处不再重复包一层（§5）。
import { WorkOrderTabs } from '@/components/Admin/WorkOrderTabs';

export function AdminWorkOrdersView() {
  return <WorkOrderTabs />;
}

export default AdminWorkOrdersView;

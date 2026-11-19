import type { WorkOrder } from '@/domain/admin/models';
import { workOrderCategoryLabel } from '@/domain/admin/workOrderMeta';
import { Tag } from './Tag';

interface WorkOrderCategoryTagProps {
  workOrder: Pick<WorkOrder, 'itemType' | 'notificationCategory'>;
  className?: string;
}

/** 列表和详情共用中性分类标签；不传事件色，所有视图保持一致。 */
export function WorkOrderCategoryTag({ workOrder, className }: WorkOrderCategoryTagProps) {
  return <Tag className={className}>{workOrderCategoryLabel(workOrder)}</Tag>;
}

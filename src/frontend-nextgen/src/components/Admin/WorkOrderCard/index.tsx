// 工单行：审批类（可审批=同意 primary / 驳回 destructive+弹窗收集理由；已处理=显示已通过/已驳回状态文字）
// / 通知类（未读显示查看，已读直接显示已查看；查看进入 Drawer 详情）。
// 通知类未读提供查看入口；审批类通过操作按钮或状态文字表达当前状态，详情仍可通过整行点击进入。
// 视觉对齐 admin 视觉交互指南 §7.3：无外框列表行，底分割线 border-border，hover bg-muted/50，次行 content+内联时间。
// 类型 Tag 颜色取自 domain 单一映射（wo.typeTone/typeLabel），禁止硬编码。
// 驳回走 Modal+Textarea 收集 review_remark（后端 reject 要求非空，否则 422）。
import { Button, Card, Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle, Textarea } from '@/components/ui';
import type { WorkOrder } from '@/domain/admin/models';
import { workOrderService } from '@/services/admin/workOrderService';
import { cn } from '@/utils/cn';
import { formatAbsoluteTime } from '@/utils/format';
import { Eye } from 'lucide-react';
import { useState } from 'react';
import { Tag } from '../Tag';

export interface WorkOrderCardProps {
  workOrder: WorkOrder;
  onApprove?: (workOrderId: number | string) => void;
  onReject?: (workOrderId: number | string, remark: string) => void | Promise<void>;
  onView?: (workOrder: WorkOrder) => void;
  canAct?: boolean; // 工单中心容器决定是否可操作（缺 identity 时只读）
}

export function WorkOrderCard({ workOrder: wo, onApprove, onReject, onView, canAct = true }: WorkOrderCardProps) {
  const isApproval = wo.itemType === 'APPROVAL';
  const availability = workOrderService.canApprove(wo);
  const actionable = availability.ok; // PENDING 且有审批权限；已处理/无权限时为否
  const [rejectOpen, setRejectOpen] = useState(false);
  const [remark, setRemark] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submitReject = async () => {
    const text = remark.trim();
    if (!text) return;
    setSubmitting(true);
    try {
      await onReject?.(wo.workOrderId, text);
      setRejectOpen(false);
      setRemark('');
    } finally {
      setSubmitting(false);
    }
  };

  // 已处理审批的状态文字（§2.3：APPROVED=success / REJECTED=destructive / 其他回退 statusLabel）
  const statusText =
    wo.status === 'APPROVED' ? (
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-success">
        <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden />
        已通过
      </span>
    ) : wo.status === 'REJECTED' ? (
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-destructive">
        <span className="h-1.5 w-1.5 rounded-full bg-destructive" aria-hidden />
        已驳回
      </span>
    ) : (
      <span className="text-xs text-muted-foreground">{wo.statusLabel}</span>
    );

  return (
    <>
      <Card
        className={cn(
          'flex cursor-pointer items-center gap-5 rounded-none border-x-0 border-t-0 border-border bg-transparent py-4 shadow-none transition-colors last:border-b-0 hover:bg-muted/50',
          !isApproval && wo.isRead && 'opacity-75',
        )}
        onClick={() => onView?.(wo)}
      >
        <Tag tone={wo.typeTone} className="shrink-0">
          {wo.typeLabel}
        </Tag>
        <div className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-foreground">{wo.title}</span>
          {wo.content ? <div className="mt-1 truncate text-xs text-muted-foreground">{wo.content}</div> : null}
          {wo.gmtModified ? (
            <div className="mt-1 text-xs text-muted-foreground">{formatAbsoluteTime(wo.gmtModified)}</div>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {isApproval ? (
            actionable ? (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!canAct}
                  onClick={(e) => {
                    e.stopPropagation();
                    onApprove?.(wo.workOrderId);
                  }}
                >
                  同意
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={!canAct}
                  onClick={(e) => {
                    e.stopPropagation();
                    setRejectOpen(true);
                  }}
                >
                  驳回
                </Button>
              </div>
            ) : (
              statusText
            )
          ) : wo.isRead ? (
            <Tag>已查看</Tag>
          ) : (
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                leftIcon={<Eye size={14} />}
                disabled={!canAct}
                onClick={(e) => {
                  e.stopPropagation();
                  onView?.(wo);
                }}
              >
                查看
              </Button>
            </div>
          )}
        </div>
      </Card>

      {/* 驳回弹窗：收集 review_remark（后端 reject 必填非空 max512） */}
      <Modal open={rejectOpen} onOpenChange={(o) => !submitting && setRejectOpen(o)}>
        <ModalContent size="md" className="max-w-[480px]">
          <ModalHeader>
            <ModalTitle>驳回工单</ModalTitle>
          </ModalHeader>
          <div className="space-y-2 py-2">
            <div className="flex items-center justify-between">
              <label className="text-xs text-muted-foreground">驳回理由（必填）</label>
              <span className="text-xs text-muted-foreground tabular-nums">{remark.length}/512</span>
            </div>
            <Textarea
              placeholder="请填写驳回理由，将告知申请人"
              value={remark}
              onChange={(e) => setRemark(e.target.value)}
              rows={3}
              maxLength={512}
              autoFocus
            />
            <p className="m-0 truncate text-xs text-muted-foreground">工单：{wo.title}</p>
          </div>
          <ModalFooter>
            <Button variant="ghost" onClick={() => setRejectOpen(false)} disabled={submitting}>
              取消
            </Button>
            <Button variant="destructive" onClick={() => void submitReject()} disabled={!remark.trim() || submitting}>
              {submitting ? '处理中…' : '确认驳回'}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </>
  );
}

export default WorkOrderCard;

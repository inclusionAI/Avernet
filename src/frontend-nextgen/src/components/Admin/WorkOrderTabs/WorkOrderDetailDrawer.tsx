// 工单详情抽屉：头部 Tag + 已读 Tag + 时间 + 标题；正文 content；
// 详情 contentRaw（结构化对象）走 JsonBlock（着色 + 复制 + 展开/收起，key=itemId 重置折叠态），
// 纯文本 content 原样展示；审批类附加 申请人/申请理由/审批人/审批意见/审批时间；通知类底部已读提示。
// Footer 关闭 + 下一条未读(仅通知)。
import { Button, Drawer, DrawerContent, DrawerHeader, DrawerTitle, Skeleton } from '@/components/ui';
import type { WorkOrder } from '@/domain/admin/models';
import { formatAbsoluteTime } from '@/utils/format';
import { Tag } from '../Tag';
import { JsonBlock } from './JsonBlock';

export interface WorkOrderDetailDrawerProps {
  detail: WorkOrder | null;
  open: boolean;
  loading: boolean;
  nextUnread?: WorkOrder;
  onClose: () => void;
  onNextUnread: () => void;
}

export function WorkOrderDetailDrawer({
  detail,
  open,
  loading,
  nextUnread,
  onClose,
  onNextUnread,
}: WorkOrderDetailDrawerProps) {
  return (
    <Drawer open={open} onOpenChange={(o) => !o && onClose()}>
      <DrawerContent side="right" size="md" className="w-[480px]">
        <DrawerHeader>
          {detail && (
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Tag tone={detail.typeTone}>{detail.typeLabel}</Tag>
                {detail.isRead && <Tag>已查看</Tag>}
              </div>
              <span className="text-xs text-muted-foreground">
                {detail.gmtModified ? formatAbsoluteTime(detail.gmtModified) : ''}
              </span>
            </div>
          )}
          <DrawerTitle className="text-lg font-semibold text-foreground">{detail?.title ?? '工单详情'}</DrawerTitle>
        </DrawerHeader>
        {loading ? (
          <Skeleton.Line />
        ) : (
          <>
            {detail?.contentRaw ? (
              <JsonBlock key={detail.itemId} raw={detail.contentRaw} />
            ) : detail?.content ? (
              <div className="rounded-lg bg-muted/60 p-4 text-xs leading-relaxed text-foreground">{detail.content}</div>
            ) : (
              <div className="rounded-lg bg-muted/60 p-4 text-xs text-muted-foreground">（暂无内容）</div>
            )}
            {detail?.itemType === 'APPROVAL' ? (
              <dl className="mt-4 grid grid-cols-[80px_1fr] gap-x-3 gap-y-2 text-xs">
                {detail.applicantName || detail.applicantUserId ? (
                  <>
                    <dt className="text-muted-foreground">申请人</dt>
                    <dd className="m-0 text-foreground">{detail.applicantName ?? detail.applicantUserId}</dd>
                  </>
                ) : null}
                {detail.applyReason ? (
                  <>
                    <dt className="text-muted-foreground">申请理由</dt>
                    <dd className="m-0 text-foreground">{detail.applyReason}</dd>
                  </>
                ) : null}
                {detail.reviewerUserName || detail.reviewerUserId ? (
                  <>
                    <dt className="text-muted-foreground">审批人</dt>
                    <dd className="m-0 text-foreground">{detail.reviewerUserName ?? detail.reviewerUserId}</dd>
                  </>
                ) : null}
                {detail.reviewRemark ? (
                  <>
                    <dt className="text-muted-foreground">审批意见</dt>
                    <dd className="m-0 text-foreground">{detail.reviewRemark}</dd>
                  </>
                ) : null}
                {detail.reviewedAt ? (
                  <>
                    <dt className="text-muted-foreground">审批时间</dt>
                    <dd className="m-0 text-foreground">{formatAbsoluteTime(detail.reviewedAt)}</dd>
                  </>
                ) : null}
              </dl>
            ) : (
              <p className="mt-4 text-xs text-muted-foreground">
                {detail?.isRead ? '此通知已标记为已读' : '此通知尚未阅读，标记已读后将移动到「已处理」'}
              </p>
            )}
          </>
        )}
        <div className="mt-6 flex items-center justify-end gap-2 border-t border-border pt-4">
          <Button size="sm" variant="ghost" onClick={onClose}>
            关闭
          </Button>
          {nextUnread && detail?.itemType !== 'APPROVAL' && (
            <Button size="sm" variant="secondary" onClick={onNextUnread}>
              下一条未读
            </Button>
          )}
        </div>
      </DrawerContent>
    </Drawer>
  );
}

export default WorkOrderDetailDrawer;

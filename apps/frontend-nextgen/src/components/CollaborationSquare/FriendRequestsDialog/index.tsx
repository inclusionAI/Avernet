import type { HumanIdentity } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Pagination } from '@/components/ui/Pagination';
import { Segmented } from '@/components/ui/Segmented';
import type { FriendRequestDirection } from '@/domain/collaborationSquare/friendRequests';
import type { FriendRequestActor } from '@/domain/collaborationSquare/types';
import { useFriendConnectionRequests } from '@/hooks/useFriendConnectionRequests';
import { useState } from 'react';
import { FriendRequestRow } from './FriendRequestRow';

interface FriendRequestsDialogProps {
  actor: FriendRequestActor;
  actorName: string;
  human: HumanIdentity;
  onClose: () => void;
  onChanged: () => void;
}
export function FriendRequestsDialog({ actor, actorName, human, onClose, onChanged }: FriendRequestsDialogProps) {
  const [direction, setDirection] = useState<FriendRequestDirection>('received');
  const requests = useFriendConnectionRequests(actor, onChanged);
  const page = requests.data?.[direction];
  return (
    <Modal open onOpenChange={(open) => !open && onClose()}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>好友申请</ModalTitle>
          <ModalDescription>当前身份：{actorName} · 仅查询待处理申请，本次已处理结果保留至刷新。</ModalDescription>
        </ModalHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Segmented value={direction} onChange={setDirection} aria-label="好友申请方向" options={[
            { value: 'received', label: '收到的申请' }, { value: 'sent', label: '发出的申请' },
          ]} />
          <Button variant="secondary" size="sm" disabled={requests.loading || requests.busyIds.length > 0}
            onClick={() => void requests.refresh()}>刷新</Button>
        </div>
        {requests.loading ? <p role="status" className="py-8 text-center text-sm text-muted-foreground">正在加载好友申请…</p> : null}
        {requests.error ? <p role="alert" className="text-sm text-destructive">{requests.error}</p> : null}
        {requests.data?.detailsWarning ? <p role="status" className="text-sm text-muted-foreground">{requests.data.detailsWarning}</p> : null}
        {page && !requests.loading ? (
          <section aria-label={direction === 'received' ? '收到的申请列表' : '发出的申请列表'} className="space-y-4">
            {page.items.length ? (
              <ul className="space-y-3">
                {page.items.map((item) => <FriendRequestRow key={item.requestId} item={item} direction={direction}
                  actor={actor} human={human} busy={requests.busyIds.includes(item.requestId)}
                  error={requests.actionErrors[item.requestId]} onDecide={requests.decide} />)}
              </ul>
            ) : <Empty compact title={direction === 'received' ? '暂无收到的待处理申请' : '暂无发出的待处理申请'} />}
            <fieldset disabled={requests.busyIds.length > 0} aria-label="好友申请分页" className="m-0 border-0 p-0">
              <Pagination current={page.page} pageSize={page.pageSize} total={page.total}
                onChange={(next) => requests.goToPage(direction, next)} />
            </fieldset>
          </section>
        ) : null}
      </ModalContent>
    </Modal>
  );
}

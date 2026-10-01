import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import type { PublicBotProfile } from '@/domain/collaborationSquare/types';
import { Copy } from 'lucide-react';

interface BotProfileModalProps {
  open: boolean;
  profile: PublicBotProfile | null;
  loading: boolean;
  onClose: () => void;
  onCopyId: (id: string) => void;
}

export function BotProfileModal({ open, profile, loading, onClose, onCopyId }: BotProfileModalProps) {
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent>
        <ModalHeader>
          <ModalTitle>{profile?.name ?? 'Bot 公开画像'}</ModalTitle>
          <ModalDescription>展示该 Bot 已公开的画像与能力。</ModalDescription>
        </ModalHeader>
        {loading && (
          <div aria-label="正在加载 Bot 画像" className="space-y-3">
            <Skeleton.Line />
            <Skeleton.Line className="w-2/3" />
            <Skeleton.Card />
          </div>
        )}
        {!loading && profile && (
          <div className="space-y-5 text-sm">
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Bot UUID</p>
              <div className="flex flex-wrap items-center gap-2">
                <code className="break-all text-foreground">{profile.id}</code>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => onCopyId(profile.id)}
                  leftIcon={<Copy aria-hidden className="h-4 w-4" />}
                >
                  复制
                </Button>
              </div>
            </div>
            <dl className="grid gap-3 sm:grid-cols-2">
              <div>
                <dt className="text-xs text-muted-foreground">Owner用户</dt>
                <dd className="m-0 mt-1 text-foreground">{profile.ownerName}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">引擎类型</dt>
                <dd className="m-0 mt-1 text-foreground">{profile.engine ?? '未公开'}</dd>
              </div>
            </dl>
            <div>
              <p className="mb-1 text-xs text-muted-foreground">公开描述</p>
              <p className="m-0 leading-6 text-foreground">{profile.description || '暂无公开描述'}</p>
            </div>
          </div>
        )}
      </ModalContent>
    </Modal>
  );
}

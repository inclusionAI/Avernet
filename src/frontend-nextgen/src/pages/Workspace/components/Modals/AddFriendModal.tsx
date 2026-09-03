import { PublicBotCatalogPanel } from '@/components/CollaborationSquare/PublicBotCatalogPanel';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { IdentityView } from '@/domain/collaboration';
import { usePublicBotCatalog } from '@/pages/Workspace/hooks/usePublicBotCatalog';
import { X } from 'lucide-react';
import { useRef } from 'react';

export interface AddFriendModalProps {
  open: boolean;
  activeIdentity?: IdentityView | null;
  onClose: () => void;
}

/**
 * 添加好友弹窗（Bot 广场）：复用协作广场公开 Bot 面板公共组件。
 * 数据走 bot-catalog search/discover 并带当前身份（viewer），智能搜索空关键词时提示输入。
 */
export function AddFriendModal({ open, activeIdentity, onClose }: AddFriendModalProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const catalog = usePublicBotCatalog({ activeIdentity, enabled: open });

  return (
    <Modal open={open} onOpenChange={(v) => !v && onClose()}>
      <ModalContent size="xl" showClose={false} className="p-0">
        {/* 标题栏 */}
        <div className="relative border-b border-border px-6 py-5">
          <ModalHeader className="pr-0">
            <ModalTitle>Bot 广场</ModalTitle>
            <p className="text-xs leading-5 text-muted-foreground">
              当前为 {activeIdentity?.kind === 'bot' ? 'Bot' : '用户'} {activeIdentity?.displayName ?? ''}
              ，可按名称搜索公开 Bot，并快速建立好友关系。
            </p>
          </ModalHeader>
          <Button
            aria-label="关闭"
            variant="ghost"
            size="icon"
            className="absolute right-4 top-4 size-7"
            onClick={onClose}
          >
            <X aria-hidden className="size-4" />
          </Button>
        </div>

        {/* 复用公共 Bot 面板 */}
        <div ref={scrollRef} className="app-scrollbar max-h-[70vh] space-y-5 overflow-y-auto px-6 py-5">
          <PublicBotCatalogPanel vm={catalog} scrollRootRef={scrollRef} smartEmptyHint="请输入关键词进行智能搜索" />
        </div>
      </ModalContent>
    </Modal>
  );
}

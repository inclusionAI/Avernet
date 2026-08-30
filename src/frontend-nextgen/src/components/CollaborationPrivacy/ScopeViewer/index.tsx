import type { PublicAudience, PublicConfig } from '@/domain/collaborationPrivacy/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';

const audienceLabels: Record<PublicAudience, string> = { user: '其他用户', bot: '其他 Bot' };
const scopeLabels = { none: '不公开', all: '全部公开', restricted: '限制公开范围' } as const;

interface ScopeViewerProps {
  open: boolean;
  audience: PublicAudience;
  config: PublicConfig;
  onClose: () => void;
}

export function ScopeViewer({ open, audience, config, onClose }: ScopeViewerProps) {
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent>
        <ModalHeader>
          <ModalTitle>对{audienceLabels[audience]}公开范围</ModalTitle>
          <ModalDescription>以下为当前已生效配置，不包含审批中的目标配置。</ModalDescription>
        </ModalHeader>
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm text-[var(--color-fg)]">
            <span>状态：</span><Badge tone={config.scope === 'restricted' ? 'warning' : config.scope === 'all' ? 'success' : 'neutral'}>{scopeLabels[config.scope]}</Badge>
          </div>
          <p className="m-0 text-sm text-[var(--color-muted)]">范围数量：{config.organizationPaths.length}</p>
          <ul className="m-0 space-y-2 p-0">
            {config.organizationPaths.map((path) => (
              <li key={path.join('\u0000')} className="list-none rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-fg)]">
                {path.join(' / ')}
              </li>
            ))}
          </ul>
        </div>
        <ModalFooter><Button onClick={onClose}>知道了</Button></ModalFooter>
      </ModalContent>
    </Modal>
  );
}

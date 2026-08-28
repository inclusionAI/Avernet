import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { friendApprovalConfigsEqual } from '@/domain/collaborationPrivacy/policies';
import type {
  FriendApprovalConfig,
  FriendApprovalMode,
  OrganizationPath,
  OrganizationSearchEntry,
} from '@/domain/collaborationPrivacy/types';
import { useEffect, useState } from 'react';
import { OrganizationScopeSearch } from '../OrganizationScopeSearch';

const modes: Array<{ value: FriendApprovalMode; label: string; description: string }> = [
  { value: 'none', label: '无需审批', description: '符合公开范围的新申请直接建立好友关系' },
  { value: 'all', label: '全部审批', description: '所有新好友申请都需要确认' },
  { value: 'partial_exempt', label: '部分组织免审批', description: '指定组织范围直接通过，其余申请需审批' },
];

interface FriendApprovalEditorProps {
  open: boolean;
  initialConfig: FriendApprovalConfig;
  onSearch: (keyword: string, signal?: AbortSignal) => Promise<OrganizationSearchEntry[]>;
  loading?: boolean;
  onClose: () => void;
  onSubmit: (config: FriendApprovalConfig) => void;
}

export function FriendApprovalEditor({
  open,
  initialConfig,
  onSearch,
  loading,
  onClose,
  onSubmit,
}: FriendApprovalEditorProps) {
  const [mode, setMode] = useState(initialConfig.mode);
  const [selected, setSelected] = useState<OrganizationPath[]>(initialConfig.exemptOrganizationPaths);
  useEffect(() => {
    if (open) {
      setMode(initialConfig.mode);
      setSelected(initialConfig.exemptOrganizationPaths);
    }
  }, [open, initialConfig]);
  const invalid = mode === 'partial_exempt' && selected.length === 0;
  const unchanged = friendApprovalConfigsEqual(initialConfig, { mode, exemptOrganizationPaths: selected });
  return (
    <Modal open={open} onOpenChange={(next) => !next && !loading && onClose()}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>好友申请审批策略</ModalTitle>
          <ModalDescription>该设置只影响新的好友申请，不改变已有好友关系。</ModalDescription>
        </ModalHeader>
        <div className="space-y-5">
          <div className="space-y-2">
            {modes.map((option) => (
              <Button
                key={option.value}
                variant={mode === option.value ? 'primary' : 'secondary'}
                className="h-auto w-full flex-col items-start px-3 py-3 text-left"
                aria-pressed={mode === option.value}
                onClick={() => setMode(option.value)}
              >
                <span>{option.label}</span>
                <span className={mode === option.value ? 'text-xs text-white/80' : 'text-xs text-[var(--color-muted)]'}>
                  {option.description}
                </span>
              </Button>
            ))}
          </div>
          {mode === 'partial_exempt' && (
            <section aria-labelledby="friend-exempt-organizations">
              <h3 id="friend-exempt-organizations" className="mb-2 text-sm font-medium text-[var(--color-fg)]">
                免审批组织范围
              </h3>
              <OrganizationScopeSearch value={selected} onChange={setSelected} onSearch={onSearch} />
              {invalid && (
                <p className="mt-2 text-xs text-[var(--color-error)]">部分组织免审批时，请至少选择一个组织范围</p>
              )}
            </section>
          )}
          {unchanged && !invalid && <p className="text-xs text-[var(--color-muted)]">配置未发生变化，无需保存</p>}
        </div>
        <ModalFooter>
          <Button variant="secondary" disabled={loading} onClick={onClose}>
            取消
          </Button>
          <Button
            loading={loading}
            disabled={invalid || unchanged}
            onClick={() => onSubmit({ mode, exemptOrganizationPaths: selected })}
          >
            保存策略
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

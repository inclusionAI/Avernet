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
import { ChoiceGroup } from '../ChoiceGroup';
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
  const [selectedEntries, setSelectedEntries] = useState<OrganizationSearchEntry[]>(
    initialConfig.exemptOrganizationEntries ?? [],
  );
  useEffect(() => {
    if (open) {
      setMode(initialConfig.mode);
      setSelected(initialConfig.exemptOrganizationPaths);
      setSelectedEntries(initialConfig.exemptOrganizationEntries ?? []);
    }
  }, [open, initialConfig]);
  const selectedKeys = new Set(selected.map((path) => path.join('\u0000')));
  const activeEntries = selectedEntries.filter((entry) => selectedKeys.has(entry.path.join('\u0000')));
  const exemptDepartmentNos =
    mode === 'partial_exempt' ? [...new Set(activeEntries.map((entry) => entry.deptNo).filter(Boolean))] : [];
  const invalid = mode === 'partial_exempt' && selected.length === 0 && exemptDepartmentNos.length === 0;
  const unchanged = friendApprovalConfigsEqual(initialConfig, {
    mode,
    exemptOrganizationPaths: selected,
    exemptDepartmentNos,
  });
  return (
    <Modal open={open} onOpenChange={(next) => !next && !loading && onClose()}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>好友申请审批策略</ModalTitle>
          <ModalDescription>该设置只影响新的好友申请，不改变已有好友关系。</ModalDescription>
        </ModalHeader>
        <div className="space-y-5">
          <ChoiceGroup value={mode} options={modes} ariaLabel="好友审批策略" onChange={setMode} />
          {mode === 'partial_exempt' && (
            <section aria-labelledby="friend-exempt-organizations">
              <h3 id="friend-exempt-organizations" className="mb-2 text-sm font-medium text-foreground">
                免审批组织范围
              </h3>
              <OrganizationScopeSearch
                value={selected}
                onChange={setSelected}
                onSearch={onSearch}
                selectedEntries={selectedEntries}
                onEntriesChange={setSelectedEntries}
              />
              {invalid && <p className="mt-2 text-xs text-destructive">部分组织免审批时，请至少选择一个组织范围</p>}
            </section>
          )}
          {unchanged && !invalid && <p className="text-xs text-muted-foreground">配置未发生变化，无需保存</p>}
        </div>
        <ModalFooter>
          <Button variant="secondary" disabled={loading} onClick={onClose}>
            取消
          </Button>
          <Button
            loading={loading}
            disabled={invalid || unchanged}
            onClick={() =>
              onSubmit({
                mode,
                exemptOrganizationPaths: selected,
                exemptDepartmentNos,
                exemptOrganizationEntries: activeEntries,
              })
            }
          >
            保存策略
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

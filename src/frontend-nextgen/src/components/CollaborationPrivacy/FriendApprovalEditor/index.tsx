import { getCapabilities } from '@/capabilities';
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
  { value: 'none', label: '无需审批', description: '新好友申请无需用户审批，直接通过' },
  { value: 'all', label: '全部审批', description: '新好友申请都需要用户审批后方可通过' },
  { value: 'partial_exempt', label: '部分组织免审批', description: '限定组织的用户申请可直接通过；其余组织的用户申请仍需当前用户审批' },
];

interface FriendApprovalEditorProps {
  open: boolean;
  initialConfig: FriendApprovalConfig;
  onSearch: (keyword: string, signal?: AbortSignal) => Promise<OrganizationSearchEntry[]>;
  loading?: boolean;
  onClose: () => void;
  onSubmit: (config: FriendApprovalConfig) => void;
}

function getEditableMode(mode: FriendApprovalMode, partialExemptEnabled: boolean): FriendApprovalMode | null {
  return !partialExemptEnabled && mode === 'partial_exempt' ? null : mode;
}

export function FriendApprovalEditor({
  open,
  initialConfig,
  onSearch,
  loading,
  onClose,
  onSubmit,
}: FriendApprovalEditorProps) {
  const partialExemptEnabled = getCapabilities().getPartialFriendApprovalEnabled().value;
  const [mode, setMode] = useState<FriendApprovalMode | null>(() =>
    getEditableMode(initialConfig.mode, partialExemptEnabled),
  );
  const [selected, setSelected] = useState<OrganizationPath[]>(initialConfig.exemptOrganizationPaths);
  const [selectedEntries, setSelectedEntries] = useState<OrganizationSearchEntry[]>(
    initialConfig.exemptOrganizationEntries ?? [],
  );

  useEffect(() => {
    if (open) {
      setMode(getEditableMode(initialConfig.mode, partialExemptEnabled));
      setSelected(initialConfig.exemptOrganizationPaths);
      setSelectedEntries(initialConfig.exemptOrganizationEntries ?? []);
    }
  }, [open, initialConfig, partialExemptEnabled]);

  const availableModes = partialExemptEnabled ? modes : modes.filter((option) => option.value !== 'partial_exempt');
  const selectedKeys = new Set(selected.map((path) => path.join('\u0000')));
  const activeEntries = selectedEntries.filter((entry) => selectedKeys.has(entry.path.join('\u0000')));
  const exemptDepartmentNos =
    mode === 'partial_exempt' ? [...new Set(activeEntries.map((entry) => entry.deptNo).filter(Boolean))] : [];
  const nextConfig: FriendApprovalConfig | null = mode
    ? {
        mode,
        exemptOrganizationPaths: mode === 'partial_exempt' ? selected : [],
        exemptDepartmentNos,
        exemptOrganizationEntries: mode === 'partial_exempt' ? activeEntries : [],
      }
    : null;
  const invalid =
    nextConfig === null ||
    (nextConfig.mode === 'partial_exempt' &&
      nextConfig.exemptOrganizationPaths.length === 0 &&
      (nextConfig.exemptDepartmentNos ?? []).length === 0);
  const unchanged = nextConfig !== null && friendApprovalConfigsEqual(initialConfig, nextConfig);

  return (
    <Modal open={open} onOpenChange={(next) => !next && !loading && onClose()}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>变更好友审批策略</ModalTitle>
          <ModalDescription>该设置只影响新的好友申请，不改变已有好友关系。</ModalDescription>
        </ModalHeader>
        <div className="space-y-5">
          <ChoiceGroup
            value={mode ?? ('' as FriendApprovalMode)}
            options={availableModes}
            ariaLabel="好友审批策略"
            onChange={setMode}
            className={partialExemptEnabled ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}
          />
          {!partialExemptEnabled && initialConfig.mode === 'partial_exempt' && mode === null && (
            <p className="text-xs text-warning">当前策略“部分组织免审批”已下线，请重新选择“无需审批”或“全部审批”。</p>
          )}
          {partialExemptEnabled && mode === 'partial_exempt' && (
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
          <Button loading={loading} disabled={invalid || unchanged} onClick={() => nextConfig && onSubmit(nextConfig)}>
            保存策略
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

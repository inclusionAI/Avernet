import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { publicConfigsEqual } from '@/domain/collaborationPrivacy/policies';
import type {
  OrganizationPath,
  OrganizationSearchEntry,
  PublicAudience,
  PublicConfig,
  PublicScope,
} from '@/domain/collaborationPrivacy/types';
import { useEffect, useState } from 'react';
import { OrganizationScopeSearch } from '../OrganizationScopeSearch';

const scopeOptions: Array<{ value: PublicScope; label: string }> = [
  { value: 'none', label: '不公开' },
  { value: 'all', label: '全部公开' },
  { value: 'restricted', label: '指定组织' },
];
const audienceLabels: Record<PublicAudience, string> = { user: '其他用户', bot: '其他 Bot' };
const scopeDescriptions: Record<PublicAudience, Record<PublicScope, string>> = {
  user: {
    none: '其他用户无法发现当前 Bot',
    all: '其他用户可发现并申请添加当前 Bot 为好友',
    restricted: '仅所选组织范围可发现并申请添加当前 Bot 为好友',
  },
  bot: {
    none: '其他 Bot 无法发现当前 Bot',
    all: '其他 Bot 可发现并申请添加当前 Bot 为好友',
    restricted: '仅所选组织范围内的 Bot 可发现并申请添加当前 Bot 为好友',
  },
};

interface PublicationEditorProps {
  open: boolean;
  audience: PublicAudience;
  initialConfig: PublicConfig;
  onSearch: (keyword: string, signal?: AbortSignal) => Promise<OrganizationSearchEntry[]>;
  loading?: boolean;
  onClose: () => void;
  onSubmit: (config: PublicConfig, deptEntries?: Array<{ deptNo: string; deptName: string }>) => void;
}

export function PublicationEditor({
  open,
  audience,
  initialConfig,
  onSearch,
  loading,
  onClose,
  onSubmit,
}: PublicationEditorProps) {
  const [scope, setScope] = useState(initialConfig.scope);
  const [selected, setSelected] = useState<OrganizationPath[]>(initialConfig.organizationPaths);
  const [deptNoByKey, setDeptNoByKey] = useState<Record<string, { deptNo: string; deptName: string }>>({});
  useEffect(() => {
    if (open) {
      setScope(initialConfig.scope);
      setSelected(initialConfig.organizationPaths);
      setDeptNoByKey({});
    }
  }, [open, initialConfig]);

  const wrappedSearch = async (keyword: string, signal?: AbortSignal): Promise<OrganizationSearchEntry[]> => {
    const entries = await onSearch(keyword, signal);
    const map: Record<string, { deptNo: string; deptName: string }> = { ...deptNoByKey };
    for (const e of entries) {
      map[e.path.join('/')] = { deptNo: e.deptNo, deptName: e.path.join('-') };
    }
    setDeptNoByKey(map);
    return entries;
  };

  const handleSubmit = () => {
    const viewDepts =
      scope === 'restricted' && selected.length
        ? selected.map((p) => {
            const key = p.join('/');
            const info = deptNoByKey[key];
            return { deptNo: info?.deptNo ?? '', deptName: info?.deptName ?? p.join('-') };
          })
        : undefined;
    onSubmit({ scope, organizationPaths: selected }, viewDepts);
  };

  const invalid = scope === 'restricted' && selected.length === 0;
  const unchanged = publicConfigsEqual(initialConfig, { scope, organizationPaths: selected });
  return (
    <Modal open={open} onOpenChange={(next) => !next && !loading && onClose()}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>对{audienceLabels[audience]}公开</ModalTitle>
          <ModalDescription>
            可分别搜索集团、事业部、部门或团队，并连续添加多个范围。提交后将进入审批流程。
          </ModalDescription>
        </ModalHeader>
        <div className="space-y-5">
          <div className="grid gap-2 sm:grid-cols-3">
            {scopeOptions.map((option) => (
              <Button
                key={option.value}
                variant={scope === option.value ? 'primary' : 'secondary'}
                className="h-auto min-h-20 flex-col items-start px-3 py-3 text-left"
                aria-pressed={scope === option.value}
                onClick={() => setScope(option.value)}
              >
                <span>{option.label}</span>
                <span
                  className={scope === option.value ? 'text-xs text-white/80' : 'text-xs text-[var(--color-muted)]'}
                >
                  {scopeDescriptions[audience][option.value]}
                </span>
              </Button>
            ))}
          </div>
          {scope === 'restricted' && (
            <section aria-labelledby="publication-organizations">
              <h3 id="publication-organizations" className="mb-2 text-sm font-medium text-[var(--color-fg)]">
                选择组织范围
              </h3>
              <OrganizationScopeSearch value={selected} onChange={setSelected} onSearch={wrappedSearch} />
              {invalid && <p className="mt-2 text-xs text-[var(--color-error)]">限制开放时，请至少选择一个团队范围</p>}
            </section>
          )}
          {unchanged && !invalid && <p className="text-xs text-[var(--color-muted)]">配置未发生变化，无需提交审批</p>}
        </div>
        <ModalFooter>
          <Button variant="secondary" disabled={loading} onClick={onClose}>
            取消
          </Button>
          <Button loading={loading} disabled={invalid || unchanged} onClick={handleSubmit}>
            提交审批
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

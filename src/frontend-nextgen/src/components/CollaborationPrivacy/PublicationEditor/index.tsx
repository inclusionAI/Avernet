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
import { history } from '@umijs/max';
import { type MouseEvent, useEffect, useState } from 'react';
import { ChoiceGroup } from '../ChoiceGroup';
import { OrganizationScopeSearch } from '../OrganizationScopeSearch';

const scopeOptions: Array<{ value: PublicScope; label: string }> = [
  { value: 'none', label: '不公开' },
  { value: 'all', label: '全部公开' },
  { value: 'restricted', label: '限制组织范围' },
];
const audienceTitles: Record<PublicAudience, string> = { user: '对其他用户公开', bot: '对其他 Bot 公开' };
const audienceDescriptionPrefixes: Record<PublicAudience, string> = {
  user: '公开后，其他用户可在',
  bot: '公开后，其他 Bot 可在',
};
const collaborationSquareBotsPath = '/collaboration-square/bots';
const scopeDescriptions: Record<PublicAudience, Record<PublicScope, string>> = {
  user: {
    none: '其他用户无法发现当前 Bot',
    all: '其他用户可发现并申请添加当前 Bot 为好友',
    restricted: '仅所选组织范围可申请添加当前 Bot 为好友',
  },
  bot: {
    none: '其他 Bot 无法发现当前 Bot',
    all: '其他 Bot 可发现并申请添加当前 Bot 为好友',
    restricted: '仅所选组织范围可申请添加当前 Bot 为好友',
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
  const [selectedEntries, setSelectedEntries] = useState<OrganizationSearchEntry[]>(
    () => initialConfig.organizationEntries ?? initialConfig.organizationPaths.map((path) => ({ deptNo: '', path })),
  );
  useEffect(() => {
    if (open) {
      setScope(initialConfig.scope);
      setSelected(initialConfig.organizationPaths);
      setSelectedEntries(
        initialConfig.organizationEntries ?? initialConfig.organizationPaths.map((path) => ({ deptNo: '', path })),
      );
    }
  }, [open, initialConfig]);

  const wrappedSearch = async (keyword: string, signal?: AbortSignal): Promise<OrganizationSearchEntry[]> => {
    const entries = await onSearch(keyword, signal);
    const selectedKeys = new Set(selected.map((path) => path.join('\u0000')));
    setSelectedEntries((current) => {
      const byPath = new Map(current.map((entry) => [entry.path.join('\u0000'), entry]));
      entries.forEach((entry) => {
        if (selectedKeys.has(entry.path.join('\u0000'))) byPath.set(entry.path.join('\u0000'), entry);
      });
      return selected.map((path) => byPath.get(path.join('\u0000')) ?? { deptNo: '', path });
    });
    return entries;
  };

  const handleCollaborationSquareClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    history.push(collaborationSquareBotsPath);
  };

  const handleSubmit = () => {
    const viewDepts =
      scope === 'restricted' && selected.length
        ? selected.map((p) => {
            const key = p.join('\u0000');
            const entry = selectedEntries.find((item) => item.path.join('\u0000') === key);
            return { deptNo: entry?.deptNo ?? '', deptName: entry?.path.join(' / ') ?? p.join(' / ') };
          })
        : undefined;
    onSubmit({ scope, organizationPaths: selected, organizationEntries: selectedEntries }, viewDepts);
  };

  const invalid = scope === 'restricted' && selected.length === 0;
  const unchanged = publicConfigsEqual(initialConfig, { scope, organizationPaths: selected });
  return (
    <Modal open={open} onOpenChange={(next) => !next && !loading && onClose()}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>{audienceTitles[audience]}</ModalTitle>
          <ModalDescription>
            {audienceDescriptionPrefixes[audience]}{' '}
            <a
              href={collaborationSquareBotsPath}
              className="font-medium text-primary hover:opacity-80"
              onClick={handleCollaborationSquareClick}
            >
              [协作广场/公开Bot]
            </a>{' '}
            中发现当前 Bot，并申请添加为好友。
          </ModalDescription>
        </ModalHeader>
        <div className="space-y-5">
          <ChoiceGroup
            value={scope}
            options={scopeOptions.map((option) => ({
              ...option,
              description: scopeDescriptions[audience][option.value],
            }))}
            ariaLabel="公开范围"
            onChange={setScope}
            className="sm:grid-cols-3"
          />
          {scope === 'restricted' && (
            <section aria-labelledby="publication-organizations">
              <h3 id="publication-organizations" className="mb-2 text-sm font-medium text-foreground">
                选择组织范围
              </h3>
              <OrganizationScopeSearch
                value={selected}
                onChange={setSelected}
                onSearch={wrappedSearch}
                selectedEntries={selectedEntries}
                onEntriesChange={setSelectedEntries}
              />
              {invalid && <p className="mt-2 text-xs text-destructive">限制开放时，请至少选择一个团队范围</p>}
            </section>
          )}
          {unchanged && !invalid && <p className="text-xs text-muted-foreground">配置未发生变化，无需提交审批</p>}
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

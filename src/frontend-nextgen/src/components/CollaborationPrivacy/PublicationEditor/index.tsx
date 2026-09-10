import { getCapabilities } from '@/capabilities';
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
import {
  organizationScopeCopy,
  visibilityAudience,
  visibilityEditorDescription,
  visibilityScopeLabels,
} from '../botVisibilityCopy';
import { ChoiceGroup } from '../ChoiceGroup';
import { OrganizationScopeSearch } from '../OrganizationScopeSearch';

const audienceTitles: Record<PublicAudience, string> = {
  user: visibilityAudience.user.editorTitle,
  bot: visibilityAudience.bot.editorTitle,
};
const collaborationSquareBotsPath = '/collaboration-square/bots';
const scopeDescriptions: Record<PublicAudience, Record<PublicScope, string>> = {
  user: {
    none: '其他用户无法发现该 Bot',
    all: '其他用户可见，也可申请好友',
    restricted: '其他用户可见，仅选中组织范围内的用户可申请好友',
  },
  bot: {
    none: '其他 Bot 无法发现该 Bot',
    all: '其他 Bot 可见，也可申请好友',
    restricted: '其他 Bot 可见，仅选中组织范围内的用户的 Bot 可申请好友',
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
  const restrictedScopeEnabled = getCapabilities().getRestrictedPublicationScopeEnabled().value;
  const [scope, setScope] = useState<PublicScope>(() =>
    !restrictedScopeEnabled && initialConfig.scope === 'restricted' ? 'none' : initialConfig.scope,
  );
  const [selected, setSelected] = useState<OrganizationPath[]>(() =>
    restrictedScopeEnabled ? initialConfig.organizationPaths : [],
  );
  const [selectedEntries, setSelectedEntries] = useState<OrganizationSearchEntry[]>(() =>
    restrictedScopeEnabled
      ? initialConfig.organizationEntries ?? initialConfig.organizationPaths.map((path) => ({ deptNo: '', path }))
      : [],
  );
  useEffect(() => {
    if (open) {
      setScope(!restrictedScopeEnabled && initialConfig.scope === 'restricted' ? 'none' : initialConfig.scope);
      setSelected(restrictedScopeEnabled ? initialConfig.organizationPaths : []);
      setSelectedEntries(
        restrictedScopeEnabled
          ? initialConfig.organizationEntries ?? initialConfig.organizationPaths.map((path) => ({ deptNo: '', path }))
          : [],
      );
    }
  }, [open, initialConfig, restrictedScopeEnabled]);

  const availableScopeOptions: Array<{ value: PublicScope; label: string }> = [
    { value: 'none', label: visibilityScopeLabels.none },
    { value: 'all', label: visibilityScopeLabels.all },
    ...(restrictedScopeEnabled ? [{ value: 'restricted' as const, label: visibilityScopeLabels.restricted }] : []),
  ];

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
            {visibilityEditorDescription.leading}
            <a
              href={collaborationSquareBotsPath}
              className="font-medium text-primary hover:opacity-80"
              onClick={handleCollaborationSquareClick}
            >
              [协作广场/公开Bot]
            </a>
            {visibilityEditorDescription.trailing}
          </ModalDescription>
        </ModalHeader>
        <div className="space-y-5">
          <ChoiceGroup
            value={scope}
            options={availableScopeOptions.map((option) => ({
              ...option,
              description: scopeDescriptions[audience][option.value],
            }))}
            ariaLabel="Bot 可见性"
            onChange={setScope}
            className={restrictedScopeEnabled ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}
          />
          {!restrictedScopeEnabled && initialConfig.scope === 'restricted' && (
            <p className="text-xs text-muted-foreground">
              当前可见性为“限定组织可申请”。当前环境支持修改为“不可见”或“全部可见”。
            </p>
          )}
          {scope === 'restricted' && (
            <section aria-labelledby="publication-organizations">
              <h3 id="publication-organizations" className="mb-2 text-sm font-medium text-foreground">
                {organizationScopeCopy.editorTitle}
              </h3>
              <OrganizationScopeSearch
                value={selected}
                onChange={setSelected}
                onSearch={wrappedSearch}
                selectedEntries={selectedEntries}
                onEntriesChange={setSelectedEntries}
              />
              {invalid && <p className="mt-2 text-xs text-destructive">选择限定组织可申请时，请至少选择一个组织范围</p>}
            </section>
          )}
          {unchanged && !invalid && <p className="text-xs text-muted-foreground">可见性未发生变化，无需提交审批</p>}
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

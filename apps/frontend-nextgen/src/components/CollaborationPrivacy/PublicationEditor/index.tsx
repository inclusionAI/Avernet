import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotPublicRiskUiState } from '@/domain/collaborationPrivacy/botPublicRisk';
import { resolvePublishChannel } from '@/domain/collaborationPrivacy/botPublicRisk';
import { publicConfigsEqual } from '@/domain/collaborationPrivacy/policies';
import type {
  OrganizationPath,
  OrganizationSearchEntry,
  PublicAudience,
  PublicConfig,
  PublicScope,
} from '@/domain/collaborationPrivacy/types';
import type { BotPublicRiskCheckRecord } from '@/services/collaborationPrivacy/botPublicRiskService';
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
import { RiskFailureBanner, RiskFlowPreviewBanner, RiskInFlightBanner } from './RiskFlowBanners';

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
  /**
   * 外部 Bot：限定组织可申请按外部合同始终提供（不受受限选项能力开关影响），
   * 提交按钮按保存语义（PATCH 直改），不出现审批话术。
   */
  isExternalBot?: boolean;
  initialConfig: PublicConfig;
  onSearch: (keyword: string, signal?: AbortSignal) => Promise<OrganizationSearchEntry[]>;
  loading?: boolean;
  onClose: () => void;
  onSubmit: (config: PublicConfig, deptEntries?: Array<{ deptNo: string; deptName: string }>) => void;
  /**
   * 公开风险前置检测状态（bot-public-risk-check；仅 audience==='user' 且非外部 Bot 时由宿主传入）。
   * checking/advancing/approving → 已发起终态锁定；failed/attention → 失败提示 + 可编辑重发，
   * 并提供「刷新状态」入口（§2.2 手动刷新）：立即查询原安全单同步最新结论，不重新建单。
   */
  risk?: {
    uiState: BotPublicRiskUiState;
    record: BotPublicRiskCheckRecord | null;
    isCreating: boolean;
    refreshCurrent?: () => void;
    isRefreshing?: boolean;
  };
}

export function PublicationEditor({
  open,
  audience,
  isExternalBot = false,
  initialConfig,
  risk,
  onSearch,
  loading,
  onClose,
  onSubmit,
}: PublicationEditorProps) {
  const restrictedScopeEnabled = getCapabilities().getRestrictedPublicationScopeEnabled().value;
  // 外部 Bot 的限定组织可申请来自用户合同，与 TC 的能力开关解耦，不放宽 TC 默认形态。
  const scopeSelectionEnabled = restrictedScopeEnabled || isExternalBot;
  const [scope, setScope] = useState<PublicScope>(() =>
    !scopeSelectionEnabled && initialConfig.scope === 'restricted' ? 'none' : initialConfig.scope,
  );
  const [selected, setSelected] = useState<OrganizationPath[]>(() =>
    scopeSelectionEnabled ? initialConfig.organizationPaths : [],
  );
  const [selectedEntries, setSelectedEntries] = useState<OrganizationSearchEntry[]>(() =>
    scopeSelectionEnabled
      ? initialConfig.organizationEntries ?? initialConfig.organizationPaths.map((path) => ({ deptNo: '', path }))
      : [],
  );
  useEffect(() => {
    if (open) {
      setScope(!scopeSelectionEnabled && initialConfig.scope === 'restricted' ? 'none' : initialConfig.scope);
      setSelected(scopeSelectionEnabled ? initialConfig.organizationPaths : []);
      setSelectedEntries(
        scopeSelectionEnabled
          ? initialConfig.organizationEntries ?? initialConfig.organizationPaths.map((path) => ({ deptNo: '', path }))
          : [],
      );
    }
  }, [open, initialConfig, scopeSelectionEnabled]);

  const availableScopeOptions: Array<{ value: PublicScope; label: string }> = [
    { value: 'none', label: visibilityScopeLabels.none },
    { value: 'all', label: visibilityScopeLabels.all },
    ...(scopeSelectionEnabled ? [{ value: 'restricted' as const, label: visibilityScopeLabels.restricted }] : []),
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
  // 风险动线（仅对用户非外部 Bot）：触发动线展示提示条；发起后在途则进入已发起终态锁定。
  const riskActive = audience === 'user' && !isExternalBot ? risk : undefined;
  const riskKind = riskActive?.uiState.kind ?? 'idle';
  const riskInFlight = riskKind === 'checking' || riskKind === 'advancing' || riskKind === 'approving';
  const riskOutcome: 'none' | 'resolved' = riskKind === 'failed' || riskKind === 'attention' ? 'resolved' : 'none';
  const channel = resolvePublishChannel(
    { scope, organizationPaths: selected, organizationEntries: selectedEntries },
    initialConfig,
    isExternalBot,
  );
  const showFlowBanner = !riskInFlight && riskOutcome === 'none' && channel === 'risk-check';
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
              [发现/公开Bot]
            </a>
            {visibilityEditorDescription.trailing}
          </ModalDescription>
        </ModalHeader>
        <div className="flex flex-col gap-4">
          {riskInFlight && riskActive ? (
            <RiskInFlightBanner
              kind={riskKind === 'approving' ? 'approving' : riskKind === 'advancing' ? 'advancing' : 'checking'}
              record={riskActive.record}
              currentScopeNote={`${visibilityScopeLabels[initialConfig.scope]}${
                initialConfig.scope === 'restricted' ? `（${initialConfig.organizationPaths.length} 个组织范围）` : ''
              }`}
            />
          ) : (
            <>
              {/* 流程预告条置顶常读（触发动线即出现，配置改回原值随 channel 收起） */}
              {showFlowBanner ? <RiskFlowPreviewBanner /> : null}
              {riskOutcome === 'resolved' && riskActive ? (
                <RiskFailureBanner
                  kind={riskKind === 'attention' ? 'attention' : 'failed'}
                  record={riskActive.record}
                  onRefreshState={riskActive.refreshCurrent}
                  refreshing={riskActive.isRefreshing}
                />
              ) : null}
              <ChoiceGroup
                value={scope}
                options={availableScopeOptions.map((option) => ({
                  ...option,
                  description: scopeDescriptions[audience][option.value],
                }))}
                ariaLabel="Bot 可见性"
                onChange={setScope}
                className={scopeSelectionEnabled ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}
              />
              {!scopeSelectionEnabled && initialConfig.scope === 'restricted' && (
                <p className="text-xs text-muted-foreground">
                  当前可见性“限定组织可申请”已下线，请重新选择“不可见”或“全部可见”。
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
                  {invalid && (
                    <p className="mt-2 text-xs text-destructive">选择限定组织可申请时，请至少选择一个组织范围</p>
                  )}
                </section>
              )}
              {unchanged && !invalid && (
                <p className="text-xs text-muted-foreground">
                  {isExternalBot ? '可见性未发生变化，无需保存' : '可见性未发生变化，无需提交'}
                </p>
              )}
            </>
          )}
        </div>
        <ModalFooter>
          {riskInFlight ? (
            <Button variant="secondary" onClick={onClose}>
              关闭
            </Button>
          ) : (
            <>
              <Button variant="secondary" disabled={loading} onClick={onClose}>
                取消
              </Button>
              <Button loading={loading} disabled={invalid || unchanged} onClick={handleSubmit}>
                {isExternalBot ? '保存' : '发起变更'}
              </Button>
            </>
          )}
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

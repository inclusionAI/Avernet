import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Segmented } from '@/components/ui/Segmented';
import type { BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { useSkillCenterPicker } from '@/hooks/useSkillCenterPicker';
import { Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { readDirectoryFiles, type DirectoryHandle } from './capabilityPickerDirectory';
import { CapabilityPickerItemCard, pickerItemId } from './CapabilityPickerItemCard';
import { CapabilityPickerLocalSkills } from './CapabilityPickerLocalSkills';

type Source = 'mine' | 'market' | 'workshop';
type MarketSource = 'skillcenter-market' | 'teamclaw-market';
type McpMarketSource = 'internal' | 'open-platform';
type PickerItem = BotEditorSkill | BotEditorMcp;

interface CapabilityPickerModalProps {
  kind: 'skill' | 'mcp';
  open: boolean;
  marketItems: PickerItem[];
  skillCenterItems: BotEditorSkill[];
  workshopItems: PickerItem[];
  myItems: PickerItem[];
  existingIds: string[];
  loading?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (ids: string[], source: Source | MarketSource) => Promise<void>;
  onViewSkill?: (skill: BotEditorSkill) => void;
  onSearchMcp?: (source: McpMarketSource, keyword: string) => Promise<void>;
  onUploadFolder?: (files: File[]) => Promise<BotEditorSkill>;
  onLocalToggle?: (skill: BotEditorSkill) => Promise<void>;
  onLocalDelete?: (id: string) => Promise<void>;
  onLocalUpload?: (file: File) => Promise<void>;
  editable?: boolean;
}

export function CapabilityPickerModal({
  kind,
  open,
  marketItems,
  workshopItems,
  myItems,
  existingIds,
  loading = false,
  onOpenChange,
  onConfirm,
  onViewSkill,
  onSearchMcp,
  onUploadFolder,
  onLocalToggle,
  onLocalDelete,
  onLocalUpload,
  editable = true,
}: CapabilityPickerModalProps) {
  const skillSources = getCapabilities().getBotSkillPickerSources().value;
  const sources: Source[] = kind === 'skill' ? skillSources : ['market'];
  const [source, setSource] = useState<Source>(() => sources[0] ?? 'mine');
  const [marketSource, setMarketSource] = useState<MarketSource>('skillcenter-market');
  const [mcpMarketSource, setMcpMarketSource] = useState<McpMarketSource>('internal');
  const [keyword, setKeyword] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const searchMcpRef = useRef(onSearchMcp);
  searchMcpRef.current = onSearchMcp;
  const remoteSearch = kind === 'skill' && source === 'market' && marketSource === 'skillcenter-market';
  const remoteMcpSearch = kind === 'mcp';
  const skillCenter = useSkillCenterPicker(open && remoteSearch, keyword);
  const items =
    kind === 'mcp'
      ? marketItems
      : source === 'mine'
      ? myItems
      : source === 'market'
      ? marketSource === 'skillcenter-market'
        ? skillCenter.items
        : marketItems
      : workshopItems;
  const visibleItems = useMemo(() => {
    if (remoteSearch || remoteMcpSearch) return items;
    const normalized = keyword.trim().toLowerCase();
    return items.filter(
      (item) => !normalized || `${item.name} ${item.description ?? ''}`.toLowerCase().includes(normalized),
    );
  }, [items, keyword, remoteMcpSearch, remoteSearch]);
  useEffect(() => {
    if (!open || kind !== 'mcp' || !searchMcpRef.current) return;
    const timer = setTimeout(() => {
      void searchMcpRef.current?.(mcpMarketSource, keyword.trim()).catch(() => undefined);
    }, 300);
    return () => clearTimeout(timer);
  }, [kind, keyword, mcpMarketSource, open]);
  const canPickDirectory = typeof window !== 'undefined' && 'showDirectoryPicker' in window;
  const close = () => {
    setSelected([]);
    setKeyword('');
    onOpenChange(false);
  };
  const submit = async () => {
    setSubmitting(true);
    try {
      await onConfirm(selected, source === 'market' && kind === 'skill' ? marketSource : source);
      close();
    } finally {
      setSubmitting(false);
    }
  };
  const uploadFolder = async () => {
    if (!onUploadFolder || !canPickDirectory) return;
    setUploading(true);
    try {
      const pickerWindow = window as Window & {
        showDirectoryPicker?: (options?: { mode?: 'read' }) => Promise<DirectoryHandle>;
      };
      const directoryHandle = await pickerWindow.showDirectoryPicker?.({ mode: 'read' });
      if (!directoryHandle) return;
      const files = await readDirectoryFiles(directoryHandle);
      if (files.length) {
        const skill = await onUploadFolder(files);
        setSelected((current) => [...new Set([...current, skill.id])]);
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        console.error(error);
      }
    } finally {
      setUploading(false);
    }
  };
  return (
    <Modal open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <ModalContent size="lg" className="flex max-h-[calc(100dvh-2rem)] flex-col overflow-hidden">
        <ModalHeader className="shrink-0">
          <ModalTitle>添加 {kind === 'skill' ? 'Skill' : 'MCP'}</ModalTitle>
          <ModalDescription>
            {kind === 'mcp'
              ? '从 MCP 市场选择，可按来源分类和搜索后一次添加多个能力。'
              : kind === 'skill' && sources.length === 1
              ? '从我的 Skill 中选择，可一次添加多个能力。'
              : '从市场或能力工坊选择，可一次添加多个能力。'}
          </ModalDescription>
        </ModalHeader>
        <div className="app-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto">
          <Segmented
            value={source}
            onChange={(value) => {
              setSource(value as Source);
              setSelected([]);
              setKeyword('');
            }}
            options={[
              { value: 'market', label: kind === 'skill' ? '引用市场 Skill' : '引用市场 MCP' },
              ...(kind === 'skill' ? [{ value: 'workshop' as const, label: '引用空间 Skill' }] : []),
              ...(kind === 'skill' ? [{ value: 'mine' as const, label: '我的 Skill' }] : []),
            ].filter((option) => sources.includes(option.value as Source))}
            className="w-fit"
          />
          {kind === 'skill' && source === 'market' ? (
            <Segmented
              value={marketSource}
              onChange={(value) => {
                setMarketSource(value);
                setSelected([]);
                setKeyword('');
              }}
              options={[
                { value: 'skillcenter-market', label: 'SkillCenter' },
                { value: 'teamclaw-market', label: 'TeamClaw' },
              ]}
              className="w-fit"
            />
          ) : null}
          {kind === 'mcp' ? (
            <Segmented
              value={mcpMarketSource}
              onChange={(value) => {
                setMcpMarketSource(value);
                setSelected([]);
                setKeyword('');
              }}
              options={[
                { value: 'internal', label: '内部 MCP' },
                { value: 'open-platform', label: '开放平台' },
              ]}
              className="w-fit"
            />
          ) : null}
          <CapabilityPickerLocalSkills
            visible={kind === 'skill' && source === 'mine'}
            skills={myItems as BotEditorSkill[]}
            editable={editable}
            canPickDirectory={canPickDirectory}
            uploading={uploading}
            onUploadFolder={onUploadFolder ? uploadFolder : undefined}
            onLocalToggle={onLocalToggle}
            onLocalDelete={onLocalDelete}
            onLocalUpload={onLocalUpload}
          />
          <div className="relative">
            <Search aria-hidden className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder={`搜索${source === 'mine' ? '我的' : source === 'market' ? '市场' : '能力工坊'}中的 ${
                kind === 'skill' ? 'Skill' : 'MCP'
              }`}
            />
          </div>
          {remoteSearch ? (
            <div className="text-xs text-muted-foreground" role="status">
              已选 {selected.length}/20，单次最多添加 20 个 Skill
              {skillCenter.loading ? ' · 加载中…' : ''}
              {skillCenter.error ? (
                <>
                  <span role="alert">{skillCenter.error}</span>
                  <Button variant="ghost" onClick={skillCenter.retry}>
                    重试
                  </Button>
                </>
              ) : null}
            </div>
          ) : null}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {loading ? (
              <div className="col-span-full py-10 text-center text-xs text-muted-foreground" role="status">
                正在加载可引用的 {kind === 'skill' ? 'Skill' : 'MCP'}…
              </div>
            ) : visibleItems.length ? (
              visibleItems.map((item) => {
                const id = pickerItemId(item);
                const active = selected.includes(id);
                const alreadyAdded = existingIds.includes(id);
                return (
                  <CapabilityPickerItemCard
                    key={id}
                    item={item}
                    kind={kind}
                    active={active}
                    alreadyAdded={alreadyAdded}
                    selectionDisabled={alreadyAdded || (remoteSearch && selected.length >= 20 && !active)}
                    onSelect={() =>
                      setSelected((current) => (active ? current.filter((value) => value !== id) : [...current, id]))
                    }
                    onViewSkill={onViewSkill}
                  />
                );
              })
            ) : (
              <div className="col-span-full">
                <Empty compact title="暂无可添加能力" description="请更换来源或搜索条件。" />
              </div>
            )}
          </div>
          {remoteSearch && skillCenter.hasMore ? (
            <Button variant="secondary" disabled={skillCenter.loading} onClick={() => void skillCenter.loadMore()}>
              {skillCenter.loading ? '加载中…' : '加载更多'}
            </Button>
          ) : null}
        </div>
        <ModalFooter className="shrink-0 border-t border-border pt-4">
          <Button variant="secondary" onClick={close}>
            取消
          </Button>
          <Button disabled={!selected.length || submitting} onClick={() => void submit()}>
            添加{selected.length ? `（${selected.length}）` : ''}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

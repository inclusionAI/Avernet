import { getCapabilities } from '@/capabilities';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Switch } from '@/components/ui/Switch';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotCapabilitySet, BotEditorSkill } from '@/domain/botEditor';
import type { CapabilityDetailTarget } from '@/services/botWorkshop/botCapabilityDetailService';
import { ChevronDown, ChevronRight, Loader2, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { useCapabilitySetExpansion } from '@/hooks/useCapabilitySetExpansion';
import { CapabilityDetailDrawer } from './CapabilityDetailDrawer';
import { CapabilityMembers } from './CapabilityMembers';
import { CapabilityPickerModal } from './CapabilityPickerModal';
import type { CapabilityPicker, CapabilitySetManagerProps } from './capabilitySetManagerTypes';
export type { CapabilitySetManagerProps } from './capabilitySetManagerTypes';

export function CapabilitySetManager(props: CapabilitySetManagerProps) {
  const {
    sets,
    mySkills,
    marketSkills,
    skillCenterSkills,
    workshopSkills,
    marketMcps,
    editable,
    compact,
    onCreate,
    onDelete,
    onActive,
    onSkill,
    onSkillCenterReferences,
    onMcp,
    onUploadSkillFolder,
    onLoadCandidates,
  } = props;
  const expansion = useCapabilitySetExpansion(sets, props.botId, props.authenticatedUserId, props.ownerId);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [picker, setPicker] = useState<CapabilityPicker>();
  const [pickerSuspendedForDetail, setPickerSuspendedForDetail] = useState(false);
  const [detailTarget, setDetailTarget] = useState<CapabilityDetailTarget>();
  const canAddMcp = getCapabilities().getBotMcpPickerEnabled().value;
  const openPicker = (set: BotCapabilitySet, kind: CapabilityPicker['kind']) => {
    setPickerSuspendedForDetail(false);
    setPicker({ set, kind });
    if (kind === 'skill') void onLoadCandidates(kind);
  };
  const viewPickerSkill = (skill: BotEditorSkill) => {
    if (skill.source === 'local') {
      setPickerSuspendedForDetail(true);
      setDetailTarget({ kind: 'skill', id: skill.id, name: skill.name });
      return;
    }
    const url = getCapabilities().getBotSkillPickerDetailUrl(skill).value;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  };

  const create = async () => {
    await onCreate(name.trim());
    setName('');
    setCreateOpen(false);
  };
  return (
    <div className="flex min-h-full flex-col bg-background">
      <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-divider bg-card px-8 lg:px-10">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold">能力集管理</h2>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">按能力集组织 Bot 引用的 Skill、MCP 与 CLI。</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={!editable}
          leftIcon={<Plus className="size-4" />}
          onClick={() => setCreateOpen(true)}
        >
          新建
        </Button>
      </div>
      <div className="app-scrollbar flex-1 overflow-y-auto">
        {sets.length ? (
          // 稿面(artboard-047/061/062):能力集容器不带边框底色、与 header 同列对齐(px-8 lg:px-10)，
          // 仅靠组间距分组；标题行左侧零内边距，使能力集标题与「能力集管理」文字严格左对齐。
          <div className="w-full space-y-4 px-8 py-5 lg:px-10">
            {sets.map((set) => {
              const open = expansion.isExpanded(set);
              const displayName = set.isDefault ? '默认能力集' : set.name;
              return (
                <section key={set.id}>
                  <div className="group flex items-center gap-2">
                    <Button
                      variant="ghost"
                      aria-expanded={open}
                      className="h-auto min-w-0 flex-1 items-start justify-start px-0 py-0 text-left"
                      onClick={() => expansion.toggle(set)}
                    >
                      <span className="min-w-0">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-sm font-semibold">{displayName}</span>
                          {!set.active ? <Badge>已禁用</Badge> : null}
                          {set.isDefault ? <Badge tone="primary">系统默认</Badge> : null}
                          {/* 展开收起图标跟在标题右侧(稿面 12×12 箭标)，不再占据行首。 */}
                          {open ? (
                            <ChevronDown className="size-3 shrink-0 text-muted-foreground" aria-hidden />
                          ) : (
                            <ChevronRight className="size-3 shrink-0 text-muted-foreground" aria-hidden />
                          )}
                        </span>
                        <span className="mt-1 block text-xs font-normal text-muted-foreground">
                          {set.skills.length} Skill · {set.mcps.length} MCP · {set.clis.length} CLI
                        </span>
                      </span>
                    </Button>
                    {set.isDefault ? (
                      <TooltipProvider delayDuration={0}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span data-testid="default-capability-set-switch" className="inline-flex" tabIndex={0}>
                              <Switch checked disabled aria-label="默认能力集不可禁用" />
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>默认能力集不可禁用</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : (
                      <div className="flex items-center gap-2">
                        {props.pendingSkillSetToggle?.id === set.id ? (
                          <span role="status" className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                            <Loader2 className="size-3.5 animate-spin" aria-hidden />
                            {props.pendingSkillSetToggle.slow
                              ? `${props.pendingSkillSetToggle.active ? '启用' : '停用'}耗时较长，仍在等待结果…`
                              : `正在${props.pendingSkillSetToggle.active ? '启用' : '停用'}…`}
                          </span>
                        ) : null}
                        <Switch
                          checked={set.active}
                          loading={Boolean(props.pendingSkillSetToggle)}
                          disabled={!editable}
                          aria-label={`${set.active ? '停用' : '启用'}${displayName}`}
                          onCheckedChange={(value) => void onActive(set, value)}
                        />
                      </div>
                    )}
                    {!set.isDefault ? (
                      <ConfirmDialog
                        title="确认删除该能力集？"
                        description={`删除「${displayName}」会移除其中 Skill 与 MCP 的引用，且无法恢复。`}
                        confirmVariant="destructive"
                        disabled={!editable || set.active}
                        onConfirm={() => onDelete(set.id)}
                      >
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`删除${displayName}`}
                          leftIcon={<Trash2 className="size-4" />}
                        />
                      </ConfirmDialog>
                    ) : null}
                  </div>
                  {open ? (
                    <div className="mt-5 space-y-5">
                      <CapabilityMembers
                        kind="skill"
                        items={set.skills}
                        compact={compact}
                        onDetail={props.botId ? (id, name) => setDetailTarget({ kind: 'skill', id, name }) : undefined}
                        editable={editable}
                        onAdd={set.isDefault ? undefined : () => openPicker(set, 'skill')}
                        onRemove={(id) => onSkill(set.id, id, false)}
                      />
                      <CapabilityMembers
                        kind="mcp"
                        items={set.mcps}
                        compact={compact}
                        onDetail={props.botId ? (id, name) => setDetailTarget({ kind: 'mcp', id, name }) : undefined}
                        editable={editable}
                        identities={props.mcpCallTypes}
                        identityEditable={props.callerContextEditable}
                        identityDisabledReason={props.mcpIdentityDisabledReason}
                        updatingIdentityId={props.updatingCallType}
                        onIdentity={props.onMcpCallType}
                        onAdd={set.isDefault || !canAddMcp ? undefined : () => openPicker(set, 'mcp')}
                        onRemove={(id) => onMcp(set.id, id, false)}
                      />
                      {set.clis.length ? (
                        <CapabilityMembers kind="cli" items={set.clis} compact={compact} editable={false} />
                      ) : null}
                    </div>
                  ) : null}
                </section>
              );
            })}
          </div>
        ) : (
          <Empty title="暂无能力集" description="新建能力集后，可从市场或能力工坊引用 Skill 与 MCP。" />
        )}
      </div>
      <CapabilityDetailDrawer
        editable={editable && !pickerSuspendedForDetail}
        botId={props.botId}
        ownerId={props.ownerId}
        target={detailTarget}
        onClose={() => {
          setDetailTarget(undefined);
          setPickerSuspendedForDetail(false);
        }}
      />
      <Modal open={createOpen} onOpenChange={setCreateOpen}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle>新建能力集</ModalTitle>
            <ModalDescription>创建后默认为启用状态。</ModalDescription>
          </ModalHeader>
          <Input
            autoFocus
            value={name}
            maxLength={100}
            placeholder="输入能力集名称"
            onChange={(event) => setName(event.target.value)}
          />
          <ModalFooter>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button disabled={!name.trim()} onClick={() => void create()}>
              创建
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
      {picker ? (
        <CapabilityPickerModal
          kind={picker.kind}
          open={!pickerSuspendedForDetail}
          marketItems={picker.kind === 'skill' ? marketSkills : marketMcps}
          skillCenterItems={picker.kind === 'skill' ? skillCenterSkills : []}
          workshopItems={picker.kind === 'skill' ? workshopSkills : []}
          myItems={picker.kind === 'skill' ? mySkills : []}
          existingIds={
            picker.kind === 'skill'
              ? picker.set.skills.map((item) => item.id)
              : picker.set.mcps.map((item) => item.serverCode)
          }
          loading={props.candidatesLoading}
          onSearchMcp={(source, keyword) => onLoadCandidates('mcp', { source, keyword })}
          onOpenChange={(open) => {
            if (!open) {
              setPicker(undefined);
              setPickerSuspendedForDetail(false);
            }
          }}
          onViewSkill={viewPickerSkill}
          onConfirm={async (ids, source) => {
            if (picker.kind === 'skill' && source === 'skillcenter-market') {
              await onSkillCenterReferences(picker.set.id, ids);
              return;
            }
            await Promise.all(
              ids.map((id) =>
                picker.kind === 'skill' ? onSkill(picker.set.id, id, true) : onMcp(picker.set.id, id, true),
              ),
            );
          }}
          onUploadFolder={picker.kind === 'skill' ? onUploadSkillFolder : undefined}
          onLocalToggle={picker.kind === 'skill' ? props.onLocalToggle : undefined}
          onLocalDelete={picker.kind === 'skill' ? props.onLocalDelete : undefined}
          onLocalUpload={picker.kind === 'skill' ? props.onLocalUpload : undefined}
          editable={editable}
        />
      ) : null}
    </div>
  );
}

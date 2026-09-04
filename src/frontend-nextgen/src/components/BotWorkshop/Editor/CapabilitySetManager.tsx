import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Switch } from '@/components/ui/Switch';
import type { BotCapabilitySet, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { CapabilityMembers } from './CapabilityMembers';
import { CapabilityPickerModal } from './CapabilityPickerModal';

export interface CapabilitySetManagerProps {
  sets: BotCapabilitySet[];
  mySkills: BotEditorSkill[];
  marketSkills: BotEditorSkill[];
  skillCenterSkills: BotEditorSkill[];
  workshopSkills: BotEditorSkill[];
  marketMcps: BotEditorMcp[];
  editable: boolean;
  onCreate: (name: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onActive: (set: BotCapabilitySet, active: boolean) => Promise<void>;
  onSkill: (setId: string, skillId: string, active: boolean) => Promise<void>;
  onSkillCenterReferences: (setId: string, skillCodes: string[]) => Promise<void>;
  onUploadSkillFolder: (files: File[]) => Promise<BotEditorSkill>;
  onMcp: (setId: string, serverCode: string, active: boolean) => Promise<void>;
  onLoadCandidates: () => Promise<void>;
  mcpCallTypes?: Record<string, 'caller' | 'owner'>;
  callerContextEditable?: boolean;
  updatingCallType?: string;
  onMcpCallType?: (serverCode: string, callType: 'caller' | 'owner') => Promise<void>;
}

type Picker = { set: BotCapabilitySet; kind: 'skill' | 'mcp' };

export function CapabilitySetManager(props: CapabilitySetManagerProps) {
  const {
    sets,
    mySkills,
    marketSkills,
    skillCenterSkills,
    workshopSkills,
    marketMcps,
    editable,
    onCreate,
    onDelete,
    onActive,
    onSkill,
    onSkillCenterReferences,
    onMcp,
    onUploadSkillFolder,
    onLoadCandidates,
  } = props;
  const [expanded, setExpanded] = useState<string[]>(sets[0] ? [sets[0].id] : []);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [picker, setPicker] = useState<Picker>();
  useEffect(() => {
    if (sets[0]) setExpanded((current) => (current.length ? current : [sets[0].id]));
  }, [sets]);
  const create = async () => {
    await onCreate(name.trim());
    setName('');
    setCreateOpen(false);
  };
  return (
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex items-center justify-between border-b border-border px-5 py-4">
        <div>
          <h2 className="m-0 text-sm font-semibold">能力集管理</h2>
          <p className="m-0 mt-1 text-xs text-muted-foreground">按能力集组织 Bot 引用的 Skill、MCP 与 CLI。</p>
        </div>
        <Button
          size="sm"
          disabled={!editable}
          leftIcon={<Plus className="size-4" />}
          onClick={() => setCreateOpen(true)}
        >
          新建
        </Button>
      </div>
      <div className="app-scrollbar flex-1 overflow-y-auto py-3">
        {sets.length ? (
          sets.map((set) => {
            const open = expanded.includes(set.id);
            return (
              <section key={set.id} className="border-b border-border px-4 py-1">
                <div className="group flex items-center gap-2 py-2">
                  <Button
                    variant="ghost"
                    className="h-auto min-w-0 flex-1 justify-start px-1 py-1 text-left"
                    leftIcon={open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                    onClick={() =>
                      setExpanded((current) => (open ? current.filter((id) => id !== set.id) : [...current, set.id]))
                    }
                  >
                    <span className="min-w-0">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-xs font-semibold">{set.name}</span>
                        {!set.active ? <Badge>已禁用</Badge> : null}
                        {set.isDefault ? <Badge tone="primary">系统默认</Badge> : null}
                      </span>
                      <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                        {set.skills.length} Skill · {set.mcps.length} MCP · {set.clis.length} CLI
                      </span>
                    </span>
                  </Button>
                  <Switch
                    checked={set.active}
                    disabled={!editable || set.isDefault}
                    onCheckedChange={(value) => void onActive(set, value)}
                  />
                  {!set.isDefault ? (
                    <ConfirmDialog
                      title="确认删除该能力集？"
                      description={`删除「${set.name}」会移除其中 Skill 与 MCP 的引用，且无法恢复。`}
                      confirmVariant="destructive"
                      disabled={!editable || set.active}
                      onConfirm={() => onDelete(set.id)}
                    >
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`删除${set.name}`}
                        leftIcon={<Trash2 className="size-4" />}
                      />
                    </ConfirmDialog>
                  ) : null}
                </div>
                {open ? (
                  <div className="space-y-4 pb-4 pl-7">
                    <CapabilityMembers
                      kind="skill"
                      items={set.skills}
                      editable={editable}
                      onAdd={
                        set.isDefault
                          ? undefined
                          : () => void onLoadCandidates().then(() => setPicker({ set, kind: 'skill' }))
                      }
                      onRemove={(id) => onSkill(set.id, id, false)}
                    />
                    <CapabilityMembers
                      kind="mcp"
                      items={set.mcps}
                      editable={editable}
                      identities={props.mcpCallTypes}
                      identityEditable={props.callerContextEditable}
                      updatingIdentityId={props.updatingCallType}
                      onIdentity={props.onMcpCallType}
                      onAdd={
                        set.isDefault
                          ? undefined
                          : () => void onLoadCandidates().then(() => setPicker({ set, kind: 'mcp' }))
                      }
                      onRemove={(id) => onMcp(set.id, id, false)}
                    />
                    {set.clis.length ? <CapabilityMembers kind="cli" items={set.clis} editable={false} /> : null}
                  </div>
                ) : null}
              </section>
            );
          })
        ) : (
          <Empty title="暂无能力集" description="新建能力集后，可从市场或能力工坊引用 Skill 与 MCP。" />
        )}
      </div>
      <Modal open={createOpen} onOpenChange={setCreateOpen}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle>新建能力集</ModalTitle>
            <ModalDescription>创建后默认为未启用状态。</ModalDescription>
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
          open
          marketItems={picker.kind === 'skill' ? marketSkills : marketMcps}
          skillCenterItems={picker.kind === 'skill' ? skillCenterSkills : []}
          workshopItems={picker.kind === 'skill' ? workshopSkills : []}
          myItems={picker.kind === 'skill' ? mySkills : []}
          existingIds={
            picker.kind === 'skill'
              ? picker.set.skills.map((item) => item.id)
              : picker.set.mcps.map((item) => item.serverCode)
          }
          onOpenChange={(open) => {
            if (!open) setPicker(undefined);
          }}
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
        />
      ) : null}
    </div>
  );
}

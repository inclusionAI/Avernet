import { Button } from '@/components/ui';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { GroupView, IdentityView } from '@/domain/collaboration';
import { GROUP_CREATE_VIA_EXECUTE } from '@/services/workspace/groupCreateConfig';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useAllAvailableBots } from '../../hooks/useAllAvailableBots';
import { useCollaborationTemplates } from '../../hooks/useCollaborationTemplates';
import { useCreateGroup } from '../../hooks/useCreateGroup';
import { useGroupCollaborationPicker } from '../../hooks/useGroupCollaborationPicker';
import { useParticipantBinding } from '../../hooks/useParticipantBinding';
import { BindingSlot, YamlValidateButton } from './BindingSlot';
import { CollaborationFlowAside } from './CollaborationFlowAside';
import { CollaborationTemplatePicker } from './CollaborationTemplatePicker';
import { GroupConfigFields, type GroupStrategyKind } from './GroupConfigFields';
import type { GroupLeaderOption } from './GroupLeaderSelect';
import { formatAutoGroupName } from './groupNaming';
import { GroupParticipantPicker } from './GroupParticipantPicker';
import { summarizeYaml } from './groupYamlUtils';

export interface CreateGroupModalProps {
  open: boolean;
  /** 当前对话协作身份；决定好友列表与可协作 Bot 列表的查询视角。 */
  activeIdentity?: IdentityView | null;
  onClose: () => void;
  /** 创建成功后回传群详情及初始 Manager run。 */
  onCreated: (group: GroupView) => void;
}

export function CreateGroupModal({ open, activeIdentity, onClose, onCreated }: CreateGroupModalProps) {
  const { run, friendlyError, creating, clearError } = useCreateGroup();
  const picker = useGroupCollaborationPicker(activeIdentity?.id, open, activeIdentity?.kind === 'user');
  const [kind, setKind] = useState<GroupStrategyKind>('free_chat');
  const [name, setName] = useState('');
  const [context, setContext] = useState('');
  const [botUuids, setBotUuids] = useState<string[]>([]);
  const [deliveryPolicy, setDeliveryPolicy] = useState<'send_to_driver' | 'inject_observers'>('send_to_driver');
  const [definitionYaml, setDefinitionYaml] = useState('');
  const [driverBotId, setDriverBotId] = useState('');
  const [managerBotId, setManagerBotId] = useState('');
  const [enableTaskExecute, setEnableTaskExecute] = useState(GROUP_CREATE_VIA_EXECUTE);
  const wasOpenRef = useRef(false);
  const supportsStateMachine = activeIdentity?.kind === 'bot';
  const templates = useCollaborationTemplates(open && kind === 'task_dag', (yaml) => {
    setDefinitionYaml(yaml);
  });
  const { reset: resetTemplates } = templates;
  const binding = useParticipantBinding(kind === 'task_dag');

  const yamlSummary = useMemo(() => {
    if (kind !== 'task_dag' || !definitionYaml.trim()) return [];
    const keys = summarizeYaml(definitionYaml);
    return keys.filter((k) => k === 'participants' || k === 'roles');
  }, [kind, definitionYaml]);
  const selectedBots = useMemo(() => {
    const map = new Map<string, { id: string; name: string }>();
    [...picker.friends, ...picker.mine, ...picker.candidates].forEach((bot) => {
      if (botUuids.includes(bot.id)) map.set(bot.id, { id: bot.id, name: bot.name });
    });
    if (activeIdentity?.kind === 'bot' && botUuids.includes(activeIdentity.id)) {
      map.set(activeIdentity.id, { id: activeIdentity.id, name: activeIdentity.displayName });
    }
    return Array.from(map.values());
  }, [activeIdentity, botUuids, picker.candidates, picker.friends, picker.mine]);

  const leaderOptions = useMemo<GroupLeaderOption[]>(() => {
    return selectedBots.map((bot) => ({
      id: bot.id,
      name: bot.name,
      current: activeIdentity?.kind === 'bot' && bot.id === activeIdentity.id,
    }));
  }, [activeIdentity, selectedBots]);

  const allAvailableBots = useAllAvailableBots(activeIdentity, picker);
  const allBotName = (botId: string) => allAvailableBots.find((b) => b.id === botId)?.name ?? botId;
  const boundBotIds = Array.from(new Set(Object.values(binding.participantBindings).filter(Boolean)));
  const activeRoleBotId = binding.participantBindings[binding.activeParticipantKey] ?? '';
  const activeRoleBotOption = activeRoleBotId ? [{ id: activeRoleBotId, name: allBotName(activeRoleBotId) }] : [];

  useEffect(() => {
    const opening = open && !wasOpenRef.current;
    wasOpenRef.current = open;
    if (!opening) return;

    setKind('free_chat');
    setName('');
    setContext('');
    setBotUuids(activeIdentity?.kind === 'bot' ? [activeIdentity.id] : []);
    setDeliveryPolicy('send_to_driver');
    setDefinitionYaml('');
    setDriverBotId(activeIdentity?.kind === 'bot' ? activeIdentity.id : '');
    setManagerBotId('');
    setEnableTaskExecute(GROUP_CREATE_VIA_EXECUTE);
    resetTemplates();
    binding.reset();
  }, [activeIdentity?.id, activeIdentity?.kind, open, resetTemplates, binding.reset]);

  const toggleBot = (id: string) => {
    clearError();
    if (kind === 'task_dag') {
      const activeKey = binding.activeParticipantKey;
      if (!activeKey) return;
      if (binding.participantBindings[activeKey] === id) {
        binding.handleUnbind(activeKey);
      } else {
        binding.handleBind(activeKey, id);
      }
      return;
    }
    if (activeIdentity?.kind === 'bot' && id === activeIdentity.id) return;
    setBotUuids((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
    if (driverBotId === id) setDriverBotId(activeIdentity?.kind === 'bot' ? activeIdentity.id : '');
    if (managerBotId === id) setManagerBotId('');
  };

  const switchKind = (next: GroupStrategyKind) => {
    clearError();
    setKind(next);
    if (next === 'free_chat') {
      setDriverBotId(activeIdentity?.kind === 'bot' ? activeIdentity.id : '');
    } else {
      setDriverBotId('');
    }
    if (next !== 'task_master_slave') setManagerBotId('');
  };

  const setField = (setter: (value: string) => void) => (value: string) => {
    clearError();
    setter(value);
  };

  const hasParticipants =
    kind === 'task_dag' ? Object.values(binding.participantBindings).some(Boolean) : botUuids.length > 0;
  const hasLeader = kind === 'task_dag' ? true : kind === 'free_chat' ? Boolean(driverBotId) : Boolean(managerBotId);
  const canSubmit = hasParticipants && hasLeader && binding.canSubmitTaskDag;

  const bindingSlot: ReactNode = (
    <BindingSlot visible={kind === 'task_dag' && supportsStateMachine} binding={binding} botNameResolver={allBotName} />
  );
  const showFlowAside = kind === 'task_dag' && binding.yamlValidation.isValidated;

  const templateSlot: ReactNode =
    kind === 'task_dag' && supportsStateMachine ? (
      <CollaborationTemplatePicker
        mode={templates.mode}
        templates={templates.templates}
        selectedTemplateId={templates.selectedTemplateId}
        selectedTemplate={templates.selectedTemplate}
        loadingTemplates={templates.loadingTemplates}
        loadingYaml={templates.loadingYaml}
        tagLabel={templates.tagLabel}
        onModeChange={templates.setMode}
        onSelect={templates.selectTemplate}
        action={<YamlValidateButton yaml={definitionYaml} binding={binding} />}
      />
    ) : null;

  const handleSubmit = async () => {
    const currentHuman =
      activeIdentity?.kind === 'user' && activeIdentity.id && activeIdentity.id !== 'me' ? activeIdentity.id : null;
    let effectiveLeader: string;
    let memberIds: string[];
    if (kind === 'task_dag') {
      effectiveLeader = boundBotIds[0] ?? '';
      memberIds = [currentHuman, ...boundBotIds].filter((id): id is string => Boolean(id));
    } else {
      effectiveLeader = kind === 'task_master_slave' ? managerBotId : driverBotId;
      memberIds = [currentHuman, effectiveLeader, ...botUuids].filter((id): id is string => Boolean(id));
    }
    const participantIds = Array.from(new Set(memberIds));
    const participants = participantIds.map((id) => ({ actor_id: id }));
    const participantName = (id: string) => (id === activeIdentity?.id ? activeIdentity.displayName : allBotName(id));
    const participantBindingsArr = Object.entries(binding.participantBindings)
      .filter(([, botId]) => Boolean(botId))
      .map(([binding, botId]) => ({ binding, actor_ids: [botId] }));
    const res = await run(
      {
        name: name.trim() || formatAutoGroupName(participantIds, effectiveLeader, kind === 'task_dag', participantName),
        strategy: kind === 'free_chat' ? 'chat' : kind === 'task_master_slave' ? 'manager_worker' : 'state_machine',
        deliveryPolicy,
        definitionYaml,
        driverBotUuid: effectiveLeader,
        originator: activeIdentity?.id ?? '',
        participants,
        context: context.trim() || undefined,
        participantBindings: kind === 'task_dag' ? participantBindingsArr : undefined,
      },
      { viaExecute: enableTaskExecute },
    );
    if (res.ok) onCreated(res.data);
  };

  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent
        size={showFlowAside ? 'xl' : 'lg'}
        closeLabel="关闭发起协作弹窗"
        className="min-w-0 gap-0 overflow-hidden p-0"
      >
        <ModalHeader className="border-b border-border px-6 pb-4 pt-5">
          <div className="flex min-w-0 items-center gap-2">
            <ModalTitle className="m-0 shrink-0 text-base font-semibold text-foreground">发起协作</ModalTitle>
            {activeIdentity && (
              <>
                <span className="text-xs text-muted-foreground">为</span>
                <span className="shrink-0 rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
                  {activeIdentity.kind === 'bot' ? 'Bot' : '用户'}
                </span>
                <span className="max-w-44 truncate rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  {activeIdentity.displayName}
                </span>
              </>
            )}
          </div>
        </ModalHeader>

        <div className="flex min-h-0 min-w-0 max-w-full overflow-x-hidden">
          <div
            data-testid="create-group-modal-body"
            className="app-scrollbar max-h-[560px] min-w-0 max-w-full flex-1 space-y-5 overflow-x-hidden overflow-y-auto px-6 py-6"
          >
            <GroupConfigFields
              kind={kind}
              name={name}
              context={context}
              driverBotId={driverBotId}
              managerBotId={managerBotId}
              deliveryPolicy={deliveryPolicy}
              definitionYaml={definitionYaml}
              yamlSummary={yamlSummary}
              yamlValidated={kind === 'task_dag' && binding.yamlValidation.isValidated}
              leaderOptions={leaderOptions}
              supportsStateMachine={supportsStateMachine}
              viaExecute={enableTaskExecute}
              onViaExecuteChange={(value) => {
                clearError();
                setEnableTaskExecute(value);
              }}
              templateSlot={templateSlot}
              bindingSlot={bindingSlot}
              onKindChange={switchKind}
              onNameChange={setField(setName)}
              onContextChange={setField(setContext)}
              onDriverChange={(value) => {
                clearError();
                setDriverBotId(value);
              }}
              onManagerChange={(value) => {
                clearError();
                setManagerBotId(value);
              }}
              onDeliveryChange={setDeliveryPolicy}
              onYamlChange={(value) => {
                clearError();
                setDefinitionYaml(value);
                binding.yamlValidation.invalidate();
              }}
            />

            {(kind !== 'task_dag' || binding.yamlValidation.isValidated) && (
              <GroupParticipantPicker
                picker={picker}
                selectedIds={kind === 'task_dag' ? (activeRoleBotId ? [activeRoleBotId] : []) : botUuids}
                selectedOptions={kind === 'task_dag' ? activeRoleBotOption : selectedBots}
                showMineTab={activeIdentity?.kind === 'user'}
                onToggle={toggleBot}
                excludeId={kind === 'task_dag' ? null : activeIdentity?.id}
                prependBot={
                  kind === 'task_dag' && activeIdentity?.kind === 'bot'
                    ? { id: activeIdentity.id, name: activeIdentity.displayName }
                    : null
                }
              />
            )}

            {friendlyError && (
              <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{friendlyError}</p>
            )}
          </div>
          {showFlowAside && <CollaborationFlowAside leaderOptions={allAvailableBots} binding={binding} />}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-4">
          <Button variant="secondary" size="md" disabled={creating} onClick={onClose}>
            取消
          </Button>
          <Button size="md" loading={creating} disabled={creating || !canSubmit} onClick={handleSubmit}>
            确认创建
          </Button>
        </div>
      </ModalContent>
    </Modal>
  );
}

export default CreateGroupModal;

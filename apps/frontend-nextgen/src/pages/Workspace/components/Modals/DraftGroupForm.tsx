import type { IdentityView } from '@/domain/collaboration';
import type { CreateGroupInput } from '@/services/workspace/groupCreateRequest';
import { useEffect, useState, type ReactNode } from 'react';
import { useAllAvailableBots } from '../../hooks/useAllAvailableBots';
import { useGroupCollaborationPicker } from '../../hooks/useGroupCollaborationPicker';
import { useParticipantBinding } from '../../hooks/useParticipantBinding';
import { BindingSlot, YamlValidateButton } from './BindingSlot';
import { CollaborationFlowAside } from './CollaborationFlowAside';
import { CreateGroupFooter } from './CreateGroupFooter';
import { CreateGroupHumanNotifyMode, useCreateGroupHumanNotifyMode } from './CreateGroupHumanNotifyMode';
import { GroupConfigFields } from './GroupConfigFields';
import { GroupParticipantPicker } from './GroupParticipantPicker';
import { formatAutoGroupName } from './groupNaming';
import { useResolvedServiceBotRouting } from './serviceBotRoutingSlots';

export interface DraftGroupValues {
  name: string;
  context: string;
  yaml: string;
}

interface Props {
  originator?: IdentityView;
  originatorSlot: ReactNode;
  values: DraftGroupValues;
  onChange: (values: DraftGroupValues) => void;
  creating: boolean;
  blocked: boolean;
  error?: string;
  onSubmit: (input: CreateGroupInput) => Promise<void>;
  onClose: () => void;
}

/** 发起 Bot 变化时重新挂载绑定与候选，编辑中的原文和目标由外层保留。 */
export function DraftGroupForm({
  originator,
  originatorSlot,
  values,
  onChange,
  creating,
  blocked,
  error,
  onSubmit,
  onClose,
}: Props) {
  const binding = useParticipantBinding(true);
  const picker = useGroupCollaborationPicker(originator?.id, Boolean(originator), false, 'bot');
  const allBots = useAllAvailableBots(originator, picker);
  const [initialYaml] = useState(values.yaml);
  const humanNotify = useCreateGroupHumanNotifyMode(true, () => {});
  useEffect(() => {
    picker.setTab('candidates');
    void binding.handleValidate(initialYaml);
    return binding.reset;
  }, [initialYaml, binding.handleValidate, binding.reset, picker.setTab]);
  const boundIds = Array.from(new Set(Object.values(binding.participantBindings).filter(Boolean)));
  const memberIds = Array.from(new Set([originator?.id, ...boundIds].filter((id): id is string => Boolean(id))));
  const routing = useResolvedServiceBotRouting(memberIds, true);
  const botName = (id: string) => allBots.find((bot) => bot.id === id)?.name ?? id;
  const boundOptions = memberIds.map((id) => allBots.find((bot) => bot.id === id) ?? { id, name: botName(id) });
  const activeBot = binding.participantBindings[binding.activeParticipantKey];
  const locked = creating || blocked;
  const canSubmit =
    Boolean(originator && values.context.trim()) && binding.canSubmitTaskDag && routing.ready && !locked;
  const update = (field: keyof DraftGroupValues, value: string) => {
    if (!locked) onChange({ ...values, [field]: value });
  };
  const submit = async () => {
    if (!canSubmit || !originator) return;
    await onSubmit({
      name: values.name.trim() || formatAutoGroupName(memberIds, originator.id, true, botName),
      strategy: 'state_machine',
      context: values.context.trim(),
      definitionYaml: values.yaml,
      originator: originator.id,
      driverBotUuid: originator.id,
      humanMentionNotifyMode: humanNotify.value,
      participants: routing.applyTags(memberIds.map((id) => ({ actor_id: id }))),
      participantBindings: Object.entries(binding.participantBindings)
        .filter(([, id]) => Boolean(id))
        .map(([key, id]) => ({ binding: key, actor_ids: [id] })),
    });
  };
  return (
    <>
      <div className="app-scrollbar col-start-1 row-start-2 min-h-0 min-w-0 overflow-x-hidden overflow-y-auto overscroll-contain px-4 py-5 sm:px-7 sm:py-6"
        onScroll={({ currentTarget: el }) => { if (el.scrollHeight - el.scrollTop - el.clientHeight < 80 && originator && binding.yamlValidation.isValidated) picker.loadMore(); }}>
        <fieldset disabled={locked} className="m-0 flex min-w-0 flex-col gap-6 border-0 p-0 [&>*]:shrink-0">
          {originatorSlot}
          <GroupConfigFields
            fixedKind
            kind="task_dag"
            name={values.name}
            context={values.context}
            definitionYaml={values.yaml}
            yamlValidated={binding.yamlValidation.isValidated}
            driverBotId={originator?.id ?? ''}
            managerBotId=""
            deliveryPolicy="send_to_driver"
            supportsStateMachine
            leaderOptions={[]}
            viaExecute={false}
            onKindChange={() => {}}
            onViaExecuteChange={() => {}}
            onDriverChange={() => {}}
            onManagerChange={() => {}}
            onDeliveryChange={() => {}}
            onNameChange={(value) => update('name', value)}
            onContextChange={(value) => update('context', value)}
            onYamlChange={(value) => {
              binding.reset();
              update('yaml', value);
            }}
            templateSlot={<YamlValidateButton yaml={values.yaml} binding={binding} />}
            bindingSlot={
              <BindingSlot
                visible
                binding={binding}
                botNameResolver={botName}
                summaryAction={<CreateGroupHumanNotifyMode {...humanNotify} />}
              />
            }
          />
          {originator && binding.yamlValidation.isValidated && (
            <GroupParticipantPicker
              picker={picker}
              selectedIds={activeBot ? [activeBot] : []}
              activeRoleName={binding.yamlValidation.participantDefinitions.find((role) => role.key === binding.activeParticipantKey)?.displayName || binding.activeParticipantKey}
              listViewportClassName="max-h-none overflow-visible"
              selectedOptions={boundOptions}
              showMineTab={false}
              routing={routing}
              additionalRoutingOptions={boundOptions}
              prependBot={{ id: originator.id, name: originator.displayName, avatarUrl: originator.avatarUrl }}
              onToggle={(id) => {
                if (locked || !binding.activeParticipantKey) return;
                if (activeBot === id) binding.handleUnbind(binding.activeParticipantKey);
                else binding.handleBind(binding.activeParticipantKey, id);
              }}
            />
          )}
        </fieldset>
        {error && (
          <p role="alert" className="mt-3 rounded-md bg-destructive/10 p-3 text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
      {binding.yamlValidation.isValidated && <CollaborationFlowAside leaderOptions={allBots} binding={binding} />}
      <CreateGroupFooter
        yamlValidationError={binding.yamlValidation.validationError}
        creating={creating}
        canSubmit={canSubmit}
        onClose={onClose}
        onSubmit={submit}
      />
    </>
  );
}

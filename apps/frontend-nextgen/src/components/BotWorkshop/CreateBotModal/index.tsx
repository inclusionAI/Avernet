import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { ExpertSummary } from '@/domain/expertMarket';
import { useAgentCodingTemplates } from '@/hooks/useAgentCodingTemplates';
import { useExpertBotCreation } from '@/hooks/useExpertBotCreation';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import type { BotCreateAuthorization, BotCreateInput, BotCreateScenario, BotCreateSpace } from '@/services/botWorkshop';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import { Cloud, Laptop } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { AuthorizationPanel } from './AuthorizationPanel';
import { CreateBotFormFields } from './CreateBotFormFields';
import { ExpertCreationResult } from './ExpertCreationResult';
import { ExpertTemplatePicker } from './ExpertTemplatePicker';

interface CreateBotModalProps {
  scenario?: BotCreateScenario;
  expert?: ExpertSummary;
  spaces: BotCreateSpace[];
  creating: boolean;
  authorization?: BotCreateAuthorization & { message?: string; error?: string };
  onClose: () => void;
  onExpertCreated?: () => void;
  onSubmit: (input: BotCreateInput) => Promise<void>;
}

const initialValues = (scenario: BotCreateScenario, spaces: BotCreateSpace[]): BotCreateInput => {
  const firstSpace = spaces[0] ?? { id: '', ownership: 'personal' as const };
  return {
    scenario,
    name: '',
    description: '',
    engine: 'openclaw',
    spaceId: firstSpace.id,
    ownership: firstSpace.ownership,
    serviceMode: 'non-service',
    initialize: true,
    avatarUrl: generateBotAvatar('new-bot'),
  };
};

const CreateBotModal: React.FC<CreateBotModalProps> = ({
  scenario,
  expert: fixedExpert,
  spaces: suppliedSpaces,
  creating,
  authorization,
  onClose,
  onExpertCreated,
  onSubmit,
}) => {
  const accessibleSpaces = useSpaceContext((state) => state.spaces);
  const spaces =
    accessibleSpaces.length && scenario === 'cloud'
      ? accessibleSpaces.map((space) => ({
          id: String(space.spaceId),
          name: space.spaceName,
          ownership: space.spaceType === 'PERSONAL' ? ('personal' as const) : ('team' as const),
          canCreate: true,
        }))
      : suppliedSpaces;
  const [expertMode, setExpertMode] = useState(Boolean(fixedExpert));
  const [selectedExpert, setSelectedExpert] = useState<ExpertSummary | undefined>(fixedExpert);
  const blankDraft = useRef<BotCreateInput>();
  const templateDraft = useRef<BotCreateInput>();
  const [values, setValues] = useState<BotCreateInput>(() => initialValues('cloud', spaces));
  const [error, setError] = useState<string>();
  const [agentCodingError, setAgentCodingError] = useState<string>();
  const agentCodingValidatorRef = useRef<(() => Promise<string | undefined>) | null>(null);
  const initializedScenarioRef = useRef<BotCreateScenario>();
  const open = Boolean(scenario);
  const expertCreation = useExpertBotCreation(open);
  const notifiedBot = useRef<string>();
  useEffect(() => {
    const status = expertCreation.status;
    if (status?.bot && ['READY', 'APPLY_FAILED'].includes(status.state) && notifiedBot.current !== status.bot_id) {
      notifiedBot.current = status.bot_id;
      onExpertCreated?.();
    }
  }, [expertCreation.status, onExpertCreated]);
  const busy = creating || expertCreation.submitting;
  const selectExpert = (next: ExpertSummary) => {
    setValues((current) => ({
      ...current,
      name: !current.name || current.name === selectedExpert?.name.slice(0, 40) ? next.name.slice(0, 40) : current.name,
      description:
        !current.description || current.description === selectedExpert?.description.slice(0, 200)
          ? next.description.slice(0, 200)
          : current.description,
    }));
    setSelectedExpert(next);
  };
  const isLocal = scenario === 'local';
  const isAgentCoding = values.engine === 'aicoding';

  // 模板列表属于 AgentCoding 一级选项的二级数据；选中该引擎后才加载，避免打开云端创建弹窗即预取。
  const agentCodingTemplates = useAgentCodingTemplates(scenario === 'cloud' && isAgentCoding);

  useEffect(() => {
    if (!scenario) {
      initializedScenarioRef.current = undefined;
      return;
    }
    if (initializedScenarioRef.current !== scenario) {
      initializedScenarioRef.current = scenario;
      const initial = initialValues(scenario, suppliedSpaces.length ? suppliedSpaces : spaces);
      setValues(
        fixedExpert
          ? { ...initial, name: fixedExpert.name.slice(0, 40), description: fixedExpert.description.slice(0, 200) }
          : initial,
      );
      setSelectedExpert(fixedExpert);
      setExpertMode(Boolean(fixedExpert));
      blankDraft.current = undefined;
      templateDraft.current = undefined;
      setError(undefined);
      setAgentCodingError(undefined);
      agentCodingValidatorRef.current = null;
      return;
    }
    if (!values.spaceId && spaces[0]?.id) {
      setValues((current) =>
        current.spaceId ? current : { ...current, spaceId: spaces[0].id, ownership: spaces[0].ownership },
      );
    }
  }, [scenario, spaces, values.spaceId]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy || expertCreation.locked) return;
    if (expertMode && !selectedExpert) {
      setError('请选择专家模板');
      return;
    }
    setError(undefined);
    if (isAgentCoding) {
      const validationError = await agentCodingValidatorRef.current?.();
      if (validationError) {
        setAgentCodingError(validationError);
        setError(validationError);
        return;
      }
    }
    try {
      if (expertMode && selectedExpert) await expertCreation.submit(selectedExpert.expert_id, values);
      else await onSubmit(values);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '创建失败，请重试');
    }
  };

  // AgentPass 授权沿用老版全屏承载方式。此时必须卸载创建 Modal，避免其遮罩、内容框和关闭按钮覆盖授权页。
  const expertAuth =
    expertCreation.status?.state === 'AWAITING_AUTHORIZATION'
      ? expertCreation.status
      : !expertCreation.status
      ? expertCreation.accepted
      : undefined;
  if (expertAuth && (expertAuth.iframe_url || expertAuth.redirect_url))
    return (
      <AuthorizationPanel
        authorization={{
          type: 'authorization_required',
          botId: expertAuth.bot_id,
          iframeUrl: expertAuth.iframe_url,
          redirectUrl: expertAuth.redirect_url,
          request: {
            bot_name: values.name,
            bot_desc: values.description,
            engine: values.engine,
            cluster_name: 'ACRA',
            bot_type: values.serviceMode === 'service' ? 'service' : 'personal',
          },
        }}
      />
    );
  if (authorization) return <AuthorizationPanel authorization={authorization} />;

  return (
    <Modal open={open} onOpenChange={(nextOpen) => !nextOpen && !busy && onClose()}>
      <ModalContent
        size="lg"
        aria-describedby="create-bot-description"
        className="overlay-scrollbar max-h-[calc(100vh-3rem)] max-w-[710px] overscroll-y-none p-4"
      >
        <>
          <ModalHeader className="flex-row items-center gap-2.5 space-y-0">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              {isLocal ? <Laptop aria-hidden className="size-5" /> : <Cloud aria-hidden className="size-5" />}
            </div>
            <div className="min-w-0 space-y-1">
              <ModalTitle className="text-base leading-6">{isLocal ? '创建本地 Bot' : '创建云端 Bot'}</ModalTitle>
              <ModalDescription id="create-bot-description" className="text-xs leading-5">
                {isLocal
                  ? 'Bot 在个人设备中运行，不提供服务化能力。'
                  : 'Bot 在云端运行，可按引擎能力选择是否提供服务。'}
              </ModalDescription>
            </div>
          </ModalHeader>
          {expertMode && selectedExpert && (
            <p className="text-sm text-muted-foreground">来自专家市场 · {selectedExpert.name}</p>
          )}
          {expertCreation.accepted || expertCreation.unknown ? (
            <ExpertCreationResult
              botId={expertCreation.accepted?.bot_id}
              status={expertCreation.status}
              error={expertCreation.error}
              unknown={expertCreation.unknown}
              input={values}
              onClose={onClose}
            />
          ) : (
            <>
              {!isLocal && !fixedExpert && (
                <div className="flex gap-2" aria-label="创建方式">
                  {[false, true].map((mode) => (
                    <Button
                      key={String(mode)}
                      type="button"
                      variant={expertMode === mode ? 'secondary' : 'ghost'}
                      disabled={busy}
                      aria-pressed={expertMode === mode}
                      onClick={() => {
                        if (expertMode) templateDraft.current = values;
                        else blankDraft.current = values;
                        setValues(
                          (mode ? templateDraft.current : blankDraft.current) || {
                            ...values,
                            name: mode ? selectedExpert?.name || '' : '',
                            description: mode ? selectedExpert?.description || '' : '',
                          },
                        );
                        setExpertMode(mode);
                      }}
                    >
                      {mode ? '从专家模板创建' : '空白创建'}
                    </Button>
                  ))}
                </div>
              )}
              <fieldset disabled={busy} className="min-w-0 space-y-4">
                {expertMode && !fixedExpert && (
                  <ExpertTemplatePicker selected={selectedExpert} onSelect={selectExpert} />
                )}
                <CreateBotFormFields
                  values={values}
                  setValues={setValues}
                  spaces={spaces}
                  creating={busy}
                  error={error || expertCreation.error}
                  agentCodingError={agentCodingError}
                  agentCodingTemplates={agentCodingTemplates.templates}
                  agentCodingTemplatesLoading={agentCodingTemplates.loading}
                  agentCodingTemplatesError={agentCodingTemplates.error}
                  onRetryAgentCodingTemplates={agentCodingTemplates.retry}
                  onValidateReady={(validator) => {
                    agentCodingValidatorRef.current = validator;
                  }}
                  onAgentCodingErrorChange={setAgentCodingError}
                  onCancel={onClose}
                  onSubmit={submit}
                />
              </fieldset>
            </>
          )}
        </>
      </ModalContent>
    </Modal>
  );
};

export default CreateBotModal;

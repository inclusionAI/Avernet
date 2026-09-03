import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotCreateAuthorization, BotCreateInput, BotCreateScenario, BotCreateSpace } from '@/services/botWorkshop';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import { Cloud, Laptop } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { AuthorizationPanel } from './AuthorizationPanel';
import { CreateBotFormFields } from './CreateBotFormFields';

interface CreateBotModalProps {
  scenario?: BotCreateScenario;
  spaces: BotCreateSpace[];
  creating: boolean;
  authorization?: BotCreateAuthorization & { message?: string; error?: string };
  onClose: () => void;
  onSubmit: (input: BotCreateInput) => Promise<void>;
  agentCodingTemplates?: AgentCodingTemplate[];
  agentCodingTemplatesLoading?: boolean;
  agentCodingTemplatesError?: string;
  onRetryAgentCodingTemplates?: () => void;
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
  };
};

const CreateBotModal: React.FC<CreateBotModalProps> = ({
  scenario,
  spaces,
  creating,
  authorization,
  onClose,
  onSubmit,
  agentCodingTemplates = [],
  agentCodingTemplatesLoading,
  agentCodingTemplatesError,
  onRetryAgentCodingTemplates,
}) => {
  const [values, setValues] = useState<BotCreateInput>(() => initialValues('cloud', spaces));
  const [error, setError] = useState<string>();
  const [agentCodingError, setAgentCodingError] = useState<string>();
  const agentCodingValidatorRef = useRef<(() => Promise<string | undefined>) | null>(null);
  const initializedScenarioRef = useRef<BotCreateScenario>();
  const open = Boolean(scenario);
  const isLocal = scenario === 'local';
  const isAgentCoding = values.engine === 'aicoding';

  useEffect(() => {
    if (!scenario) {
      initializedScenarioRef.current = undefined;
      return;
    }
    if (initializedScenarioRef.current !== scenario) {
      initializedScenarioRef.current = scenario;
      setValues(initialValues(scenario, spaces));
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
      await onSubmit(values);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '创建失败，请重试');
    }
  };

  return (
    <Modal open={open} onOpenChange={(nextOpen) => !nextOpen && !creating && onClose()}>
      <ModalContent
        size="lg"
        aria-describedby="create-bot-description"
        className="overlay-scrollbar max-h-[calc(100vh-3rem)] max-w-[710px] p-4"
      >
        {authorization ? (
          <AuthorizationPanel authorization={authorization} creating={creating} onClose={onClose} />
        ) : (
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
            <CreateBotFormFields
              values={values}
              setValues={setValues}
              spaces={spaces}
              creating={creating}
              error={error}
              agentCodingError={agentCodingError}
              agentCodingTemplates={agentCodingTemplates}
              agentCodingTemplatesLoading={agentCodingTemplatesLoading}
              agentCodingTemplatesError={agentCodingTemplatesError}
              onRetryAgentCodingTemplates={onRetryAgentCodingTemplates}
              onValidateReady={(validator) => {
                agentCodingValidatorRef.current = validator;
              }}
              onAgentCodingErrorChange={setAgentCodingError}
              onCancel={onClose}
              onSubmit={submit}
            />
          </>
        )}
      </ModalContent>
    </Modal>
  );
};

export default CreateBotModal;

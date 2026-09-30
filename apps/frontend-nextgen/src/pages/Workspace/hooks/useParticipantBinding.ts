import { useCallback, useState } from 'react';
import { useYamlValidation } from './useYamlValidation';

export interface UseParticipantBindingResult {
  yamlValidation: ReturnType<typeof useYamlValidation>;
  graph: ReturnType<typeof useYamlValidation>['graph'];
  summary: ReturnType<typeof useYamlValidation>['summary'];
  participantBindings: Record<string, string>;
  activeParticipantKey: string;
  setActiveParticipantKey: (key: string) => void;
  boundCount: number;
  missingRequiredCount: number;
  canSubmitTaskDag: boolean;
  handleValidate: (yaml: string) => Promise<boolean>;
  handleBind: (key: string, botId: string) => void;
  handleUnbind: (key: string) => void;
  reset: () => void;
}

/**
 * 组合 YAML 校验 + participant 绑定状态管理，供 CreateGroupModal 使用。
 * 拆分为独立 Hook 以控制 CreateGroupModal 文件体积（TC-G005）。
 */
export function useParticipantBinding(isTaskDag: boolean): UseParticipantBindingResult {
  const yamlValidation = useYamlValidation();
  const [participantBindings, setParticipantBindings] = useState<Record<string, string>>({});
  const [activeParticipantKey, setActiveParticipantKey] = useState('');

  const handleValidate = useCallback(
    async (yaml: string): Promise<boolean> => {
      const result = await yamlValidation.validate(yaml);
      if (result.ok && result.definitions.length > 0) {
        setActiveParticipantKey((prev) =>
          result.definitions.some((d) => d.key === prev) ? prev : result.definitions[0].key,
        );
      }
      return result.ok;
    },
    [yamlValidation.validate],
  );

  const handleBind = useCallback((key: string, botId: string) => {
    setParticipantBindings((prev) => ({ ...prev, [key]: botId }));
  }, []);

  const handleUnbind = useCallback((key: string) => {
    setParticipantBindings((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    yamlValidation.reset();
    setParticipantBindings({});
    setActiveParticipantKey('');
  }, [yamlValidation.reset]);

  const boundCount = Object.values(participantBindings).filter(Boolean).length;
  const missingRequiredCount = (isTaskDag ? yamlValidation.participantDefinitions : []).filter(
    (d) => d.required && !participantBindings[d.key],
  ).length;
  const canSubmitTaskDag = !isTaskDag || (yamlValidation.isValidated && missingRequiredCount === 0);

  return {
    yamlValidation,
    participantBindings,
    activeParticipantKey,
    graph: yamlValidation.graph,
    summary: yamlValidation.summary,
    setActiveParticipantKey,
    boundCount,
    missingRequiredCount,
    canSubmitTaskDag,
    handleValidate,
    handleBind,
    handleUnbind,
    reset,
  };
}

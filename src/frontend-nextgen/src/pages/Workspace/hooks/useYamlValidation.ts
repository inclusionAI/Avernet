import type { CollaborationDefinitionGraphPreview } from '@/domain/collaboration/graphTypes';
import {
  buildParticipantDefinitions,
  collaborationDefinitionService,
  formatValidationErrors,
  type ParticipantDefinition,
} from '@/services/workspace/collaborationDefinitionService';
import { useCallback, useRef, useState } from 'react';

export interface UseYamlValidationResult {
  isValidating: boolean;
  validatedYaml: string;
  participantDefinitions: ParticipantDefinition[];
  /** 校验返回的图预览（valid 且 backend 返回时才有值）。 */
  graph: CollaborationDefinitionGraphPreview | null;
  /** 最近一次校验的 summary（含 initial_nodes 等）。 */
  summary: { initial_nodes: string[] } | null;
  /** 校验错误文案（仅最近一次校验失败时）。 */
  validationError: string | undefined;
  /** 校验是否通过且 YAML 未变更。 */
  isValidated: boolean;
  validate: (yaml: string) => Promise<{ ok: boolean; definitions: ParticipantDefinition[] }>;
  reset: () => void;
  /** YAML 变更后调用，使 isValidated 失效。 */
  invalidate: () => void;
}

/**
 * 自定义协作 YAML 校验 Hook：
 * 竞态用 requestId 保护；校验通过后保留 participantDefinitions + graph 供绑定面板和流程预览使用。
 * YAML 变更后 isValidated 置 false（须重新校验）。
 */
export function useYamlValidation(): UseYamlValidationResult {
  const [isValidating, setIsValidating] = useState(false);
  const [validatedYaml, setValidatedYaml] = useState('');
  const [participantDefinitions, setParticipantDefinitions] = useState<ParticipantDefinition[]>([]);
  const [graph, setGraph] = useState<CollaborationDefinitionGraphPreview | null>(null);
  const [summary, setSummary] = useState<{ initial_nodes: string[] } | null>(null);
  const [validationError, setValidationError] = useState<string | undefined>(undefined);
  const requestIdRef = useRef(0);

  const reset = useCallback(() => {
    requestIdRef.current += 1;
    setIsValidating(false);
    setValidatedYaml('');
    setParticipantDefinitions([]);
    setGraph(null);
    setSummary(null);
    setValidationError(undefined);
  }, []);

  const invalidate = useCallback(() => {
    setValidatedYaml('');
  }, []);

  const validate = useCallback(async (yaml: string): Promise<{ ok: boolean; definitions: ParticipantDefinition[] }> => {
    if (!yaml.trim()) {
      setValidationError('请输入自定义协作 YAML');
      return { ok: false, definitions: [] };
    }
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setIsValidating(true);
    setValidationError(undefined);
    setValidatedYaml('');

    const res = await collaborationDefinitionService.validate(yaml);
    if (requestId !== requestIdRef.current) return { ok: false, definitions: [] };

    setIsValidating(false);
    if (!res.ok) {
      setValidationError(res.error.friendlyMessage);
      setParticipantDefinitions([]);
      setGraph(null);
      setSummary(null);
      return { ok: false, definitions: [] };
    }
    if (!res.data.valid) {
      setValidationError(formatValidationErrors(res.data.errors));
      setParticipantDefinitions([]);
      setGraph(null);
      setSummary(null);
      return { ok: false, definitions: [] };
    }
    const definitions = buildParticipantDefinitions(res.data.participants);
    if (definitions.length === 0) {
      setValidationError('YAML 校验通过，但未返回可绑定的 participant');
      setParticipantDefinitions([]);
      setGraph(null);
      setSummary(null);
      return { ok: false, definitions: [] };
    }
    setParticipantDefinitions(definitions);
    setGraph(res.data.graph ?? null);
    setSummary({ initial_nodes: res.data.summary?.initial_nodes ?? [] });
    setValidatedYaml(yaml);
    return { ok: true, definitions };
  }, []);

  return {
    isValidating,
    validatedYaml,
    participantDefinitions,
    graph,
    summary,
    validationError,
    isValidated: validatedYaml !== '',
    validate,
    reset,
    invalidate,
  };
}

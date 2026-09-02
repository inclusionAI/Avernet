import {
  agentCodingTemplateService,
  type AgentCodingTemplate,
} from '@/services/botWorkshop/agentCodingTemplateService';
import { useCallback, useEffect, useState } from 'react';

export function useAgentCodingTemplates(enabled: boolean) {
  const [templates, setTemplates] = useState<AgentCodingTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const load = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setError(undefined);
    try {
      setTemplates(await agentCodingTemplateService.list());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'AgentCoding 模板加载失败');
    } finally {
      setLoading(false);
    }
  }, [enabled]);
  useEffect(() => {
    void load();
  }, [load]);
  return { templates, loading, error, retry: load };
}

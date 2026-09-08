import type { BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export type McpCandidateQuery = { source: 'internal' | 'open-platform'; keyword?: string };

export function useBotEditorCandidates(botId: string | null, spaceId?: string) {
  const [availableMcps, setAvailableMcps] = useState<BotEditorMcp[]>([]);
  const [marketSkills, setMarketSkills] = useState<BotEditorSkill[]>([]);
  const [skillCenterSkills, setSkillCenterSkills] = useState<BotEditorSkill[]>([]);
  const [workshopSkills, setWorkshopSkills] = useState<BotEditorSkill[]>([]);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const sequenceRef = useRef(0);

  useEffect(() => {
    sequenceRef.current += 1;
    setAvailableMcps([]);
    setMarketSkills([]);
    setSkillCenterSkills([]);
    setWorkshopSkills([]);
  }, [botId]);

  const loadCapabilityCandidates = useCallback(
    async (kind: 'skill' | 'mcp', mcpQuery?: McpCandidateQuery) => {
      if (!botId) return;
      const sequence = ++sequenceRef.current;
      setCandidatesLoading(true);
      try {
        const candidates = await botEditorService.loadCapabilityCandidates(botId, spaceId, kind, mcpQuery);
        if (sequence !== sequenceRef.current) return;
        if (kind === 'mcp') setAvailableMcps(candidates.availableMcps);
        else {
          setMarketSkills(candidates.marketSkills);
          setSkillCenterSkills(candidates.skillCenterSkills);
          setWorkshopSkills(candidates.workshopSkills);
        }
      } catch (error) {
        if (sequence !== sequenceRef.current) return;
        toast.error(error instanceof Error ? error.message : '可选能力加载失败');
        throw error;
      } finally {
        if (sequence === sequenceRef.current) setCandidatesLoading(false);
      }
    },
    [botId, spaceId],
  );

  return {
    availableMcps,
    marketSkills,
    skillCenterSkills,
    workshopSkills,
    candidatesLoading,
    loadCapabilityCandidates,
  };
}

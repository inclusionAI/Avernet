import { standardEvaluationService, type StandardEvaluationSetItem } from '@/services/botWorkshop/standardEvaluationService';
import { useCallback, useEffect, useRef, useState } from 'react';

export interface StandardEvaluationSampleSetsInput {
  botId: string;
  botVersion: string;
  ownerId?: string;
  enabled: boolean;
}

export function useStandardEvaluationSampleSets({
  botId,
  botVersion,
  ownerId,
  enabled,
}: StandardEvaluationSampleSetsInput) {
  const [sampleSets, setSampleSets] = useState<StandardEvaluationSetItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [selectedSampleSetId, setSelectedSampleSetId] = useState<string>();
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const loadSampleSets = useCallback(async () => {
    if (!botId || !enabled) return;
    setLoading(true);
    setError(undefined);
    try {
      const result = await standardEvaluationService.listEvaluationSets(botId, botVersion, ownerId);
      if (!aliveRef.current) return;
      setSampleSets(result.sets || []);
      setSelectedSampleSetId(current => current || result.sets?.[0]?.uuid || undefined);
    } catch (cause) {
      if (!aliveRef.current) return;
      setSampleSets([]);
      setSelectedSampleSetId(undefined);
      setError(cause instanceof Error ? cause.message : '获取评测样本集失败');
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [botId, botVersion, enabled, ownerId]);

  useEffect(() => {
    void loadSampleSets();
  }, [loadSampleSets]);

  return {
    sampleSets,
    sampleSetLoading: loading,
    sampleSetError: error,
    selectedSampleSetId,
    selectedSampleSet: sampleSets.find(item => item.uuid === selectedSampleSetId),
    setSelectedSampleSetId,
    reloadSampleSets: loadSampleSets,
  };
}

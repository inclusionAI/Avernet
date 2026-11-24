import { EVALUATION_GOAL_INPUT_ENABLED } from '@/services/botWorkshop/standardEvaluationWorkflow';
import { useState } from 'react';

export function useStandardEvaluationGoal() {
  const [goalType, setGoalType] = useState<'json' | 'semantic'>('json');
  const [goalFile, setGoalFile] = useState<File>();
  const [goalPrompt, setGoalPrompt] = useState('');

  const goalReady = EVALUATION_GOAL_INPUT_ENABLED
    ? goalType === 'json' ? Boolean(goalFile) : Boolean(goalPrompt.trim())
    : true;

  return {
    goalType,
    setGoalType,
    goalFile,
    setGoalFile,
    goalPrompt,
    setGoalPrompt,
    goalReady,
  };
}

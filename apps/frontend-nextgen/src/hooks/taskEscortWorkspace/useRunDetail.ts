import type { TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace/types';
import { getRunDetailPresentation } from '@/services/taskEscortWorkspace/runPresentation';
import { useState } from 'react';

export type { RunStep } from '@/services/taskEscortWorkspace/runPresentation';

export function useRunDetail(demo: TaskEscortWorkspaceController) {
  const [activeStep, setActiveStep] = useState<{ runId: string; id: string } | null>(null);
  const detail = getRunDetailPresentation(
    demo.runs,
    demo.issues,
    demo.selectedRun,
    activeStep?.runId === demo.selectedRun ? activeStep.id : null,
  );
  const close = () => {
    demo.setSelectedRun(null);
    setActiveStep(null);
  };
  return { detail, close, setActiveStep };
}

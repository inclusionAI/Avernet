import type { TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace/types';
import { getIssueDetailPresentation } from '@/services/taskEscortWorkspace/issuePresentation';
import { useState } from 'react';

type Confirmation = { id: string; action: 'apply' | 'verify' } | null;

export function useIssueDetail(demo: TaskEscortWorkspaceController) {
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const detail = getIssueDetailPresentation(demo.issues, demo.experiences, demo.selectedIssue, demo.mode === 'demo');
  const issue = detail?.issue;
  const actions = detail?.actions ?? { canApply: false, canVerify: false, canSave: false, saved: false };
  const closeConfirmation = () => {
    setConfirmation(null);
    setAcknowledged(false);
  };
  const close = () => {
    demo.setSelectedIssue(null);
    closeConfirmation();
  };
  const beginConfirmation = (action: 'apply' | 'verify') => {
    if (!issue || !(action === 'apply' ? actions.canApply : actions.canVerify)) return;
    setAcknowledged(false);
    setConfirmation({ id: issue.id, action });
  };
  const confirm = () => {
    if (!issue || !acknowledged || confirmation?.id !== issue.id) return;
    if (confirmation.action === 'apply' && actions.canApply) demo.applySuggestion(issue.id);
    else if (confirmation.action === 'verify' && actions.canVerify) demo.verifyIssue(issue.id);
    else demo.notice('状态已更新，请重新查看可用操作。');
    closeConfirmation();
  };
  const saveExperience = () => {
    if (issue && actions.canSave) demo.saveExperience(issue.id);
  };
  const isApply = confirmation?.action === 'apply';
  return {
    issue,
    actions,
    close,
    beginConfirmation,
    confirm,
    closeConfirmation,
    saveExperience,
    acknowledged,
    setAcknowledged,
    confirming: !!issue && confirmation?.id === issue.id,
    isApply,
    allowed: isApply ? actions.canApply : actions.canVerify,
    lifecycleMessage: detail?.lifecycleMessage ?? '',
    lifecycleStep: detail?.lifecycleStep ?? 0,
    verified: detail?.verified ?? false,
  };
}

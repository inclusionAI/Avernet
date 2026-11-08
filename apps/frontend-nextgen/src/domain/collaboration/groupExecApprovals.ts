export type GroupExecDecision = 'allow_once' | 'deny';

export interface GroupExecApproval {
  key: string;
  runId: string;
  botId: string;
  interactionId: string;
  toolName: string;
  command?: string;
  reason?: string;
  cwd?: string;
  options: Array<{ decision: GroupExecDecision; label: string }>;
}

export interface GroupExecResolution {
  decision: GroupExecDecision;
}

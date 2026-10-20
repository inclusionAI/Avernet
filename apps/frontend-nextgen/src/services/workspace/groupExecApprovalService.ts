import type { GroupExecApproval, GroupExecResolution } from '@/domain/collaboration/groupExecApprovals';
import {
  GroupInteractionService,
  type GroupInteractionEvent,
  type GroupInteractionSnapshot,
  type GroupQuestionServiceConfig,
} from './groupInteractionService';
import type { GroupQuestionEventIdentity } from './groupQuestionFrames';

export type GroupExecApprovalSnapshot = GroupInteractionSnapshot<GroupExecApproval>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function nonBlank(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

/** Only authenticated envelope identity selects the run; payload cannot override it. */
export function parseGroupExecApprovalEvent(
  frame: unknown,
  identity: GroupQuestionEventIdentity,
): GroupInteractionEvent<GroupExecApproval> | null {
  if (
    !isRecord(frame) ||
    frame.type !== 'event' ||
    frame.event !== 'interaction' ||
    frame.group_id !== identity.groupId ||
    frame.bcsSessionId !== identity.bcsSessionId ||
    !nonBlank(frame.bcsRunId) ||
    !nonBlank(frame.bot_uuid)
  )
    return null;
  const payload = frame.payload;
  if (!isRecord(payload) || payload.kind !== 'exec' || !nonBlank(payload.interactionId)) return null;
  const key = JSON.stringify([frame.bcsRunId, payload.interactionId]);
  if (payload.phase === 'accepted' || payload.phase === 'resolved' || payload.phase === 'invalidated') {
    return { phase: payload.phase, key };
  }
  if (
    payload.phase !== 'requested' ||
    !nonBlank(payload.toolName) ||
    !Array.isArray(payload.options) ||
    payload.options.length === 0
  )
    return null;
  const options: GroupExecApproval['options'] = [];
  for (const option of payload.options) {
    if (
      !isRecord(option) ||
      (option.decision !== 'allow_once' && option.decision !== 'deny') ||
      !nonBlank(option.label) ||
      options.some((item) => item.decision === option.decision)
    )
      return null;
    options.push({ decision: option.decision, label: option.label });
  }
  for (const field of ['command', 'reason', 'cwd']) {
    if (payload[field] !== undefined && typeof payload[field] !== 'string') return null;
  }
  return {
    phase: 'requested',
    key,
    interaction: {
      key,
      runId: frame.bcsRunId,
      botId: frame.bot_uuid,
      interactionId: payload.interactionId,
      toolName: payload.toolName,
      options,
      ...(typeof payload.command === 'string' ? { command: payload.command } : {}),
      ...(typeof payload.reason === 'string' ? { reason: payload.reason } : {}),
      ...(typeof payload.cwd === 'string' ? { cwd: payload.cwd } : {}),
    },
  };
}

export class GroupExecApprovalService extends GroupInteractionService<GroupExecApproval, GroupExecResolution> {
  constructor(config: GroupQuestionServiceConfig) {
    super(config, {
      parse: parseGroupExecApprovalEvent,
      fingerprint: (_, resolution) => JSON.stringify([resolution.decision]),
      validate(interaction, resolution) {
        if (!interaction.options.some((option) => option.decision === resolution.decision)) {
          throw new Error('该审批决定不在服务端提供的选项中');
        }
      },
    });
  }
}

import {
  createGroup, GROUP_CREATION_HOST, groupCreationBody,
  readGroupUser, UserApiError, validateGroupDefinition,
} from '@/services/backend-api/GroupCreationController';
import type { GroupCreationResult, GroupUser } from '@/services/backend-api/GroupCreationController';
import type { CreateGroupParams } from '../types';

export interface GroupSubmissionState {
  phase: 'editing' | 'preflight' | 'creating' | 'unknown' | 'success';
  error?: string;
  result?: GroupCreationResult;
  userId?: string;
}

// One instance belongs to one mounted modal, including while closed.
export class GroupCreationSubmission {
  state: GroupSubmissionState = { phase: 'editing' };
  private controller?: AbortController;

  constructor(private changed: (state: GroupSubmissionState) => void) {}

  private update(state: GroupSubmissionState) {
    this.state = state;
    this.changed(state);
  }

  dispose() { this.controller?.abort(); }

  async submit(input: { user: GroupUser; params: CreateGroupParams }): Promise<GroupCreationResult | undefined> {
    if (this.state.phase !== 'editing') return;
    // Snapshot the current form before any await; a Draft is no longer involved.
    const params: CreateGroupParams = structuredClone(input.params);
    this.update({ phase: 'preflight', userId: input.user.user_id });
    const controller = new AbortController();
    this.controller = controller;
    let posted = false;
    let created = false;
    try {
      const user = await readGroupUser(GROUP_CREATION_HOST, controller.signal);
      if (user.user_id !== input.user.user_id) throw new UserApiError('登录用户已变化，请重新配置', 'user_context_changed');
      const checked = params.group_strategy === 'state_machine'
        ? await validateGroupDefinition(params.collaboration_definition_yaml || '', controller.signal) : undefined;
      const body = groupCreationBody(params, checked);
      if (controller.signal.aborted) return;
      this.update({ phase: 'creating', userId: user.user_id });
      posted = true;
      const result = await createGroup(GROUP_CREATION_HOST, user, body, controller.signal);
      created = true;
      const after = await readGroupUser(GROUP_CREATION_HOST, controller.signal);
      if (after.user_id !== user.user_id) throw new Error('登录用户已变化，请到群列表确认创建结果');
      if (controller.signal.aborted) return;
      this.update({ phase: 'success', result, userId: user.user_id });
      return result;
    } catch (error) {
      if (controller.signal.aborted) return;
      const rejected = error instanceof UserApiError && !error.unknownOutcome;
      this.update({ phase: created || (posted && !rejected) ? 'unknown' : 'editing',
        error: error instanceof Error ? error.message : '创建失败', userId: input.user.user_id });
    }
  }
}

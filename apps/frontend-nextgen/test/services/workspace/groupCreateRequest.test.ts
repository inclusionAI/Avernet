/** @jest-environment node */
import { buildCreateGroupBody, type CreateGroupInput } from '@/services/workspace/groupCreateRequest';
import { createGroupViaExecute } from '@/services/workspace/groupExecuteService';
import { describe, expect, it, jest } from '@jest/globals';

const input: CreateGroupInput = {
  name: '群',
  strategy: 'chat',
  driverBotUuid: 'service',
  originator: 'human_1',
  participants: [
    { actor_id: 'service', tags: ['online'] },
    { actor_id: 'human_1', message_view_scope: 'participant' },
    { actor_id: 'personal' },
  ],
  definitionYaml: 'participants: []',
};
it.each([
  ['chat', 'driver', 'consultant'],
  ['manager_worker', 'manager', 'worker'],
  ['state_machine', 'driver', 'consultant'],
] as const)('%s assigns participant roles allowed by the strategy', (strategy, leadRole, memberRole) => {
  const body = buildCreateGroupBody({ ...input, strategy });
  expect(body.participants).toEqual([
    { actor_id: 'service', tags: ['online'], role: leadRole },
    { actor_id: 'human_1', message_view_scope: 'participant', role: memberRole },
    { actor_id: 'personal', role: memberRole },
  ]);
});

describe.each(['chat', 'manager_worker', 'state_machine'] as const)('%s notification source', (strategy) => {
  it.each(['all', 'driver_bot_only', 'none'] as const)('sends %s at the top level', (humanMentionNotifyMode) => {
    const body = buildCreateGroupBody({ ...input, strategy, humanMentionNotifyMode });
    expect(body).toHaveProperty('human_mention_notify_mode', humanMentionNotifyMode);
    expect(body.collaboration).not.toHaveProperty('human_mention_notify_mode');
  });

  it('preserves server defaults for callers omitting the option', () => {
    expect(buildCreateGroupBody({ ...input, strategy })).not.toHaveProperty('human_mention_notify_mode');
  });
});

it.each(['chat', 'manager_worker', 'state_machine'] as const)(
  '%s preserves tags and human visibility scope',
  (strategy) => {
    expect(buildCreateGroupBody({ ...input, strategy }).participants).toEqual([
      expect.objectContaining({ actor_id: 'service', tags: ['online'] }),
      expect.objectContaining({ actor_id: 'human_1', message_view_scope: 'participant' }),
      expect.objectContaining({ actor_id: 'personal' }),
    ]);
    expect(buildCreateGroupBody({ ...input, strategy }).participants[2]).not.toHaveProperty('tags');
  },
);

it('rejects unsupported execute routing before sending a task that would lose tags', async () => {
  const fallback = jest.fn<typeof createGroupViaExecute>();
  const res = await createGroupViaExecute({ ...input, strategy: 'state_machine' }, '1', fallback);
  expect(res).toEqual({ ok: false, error: expect.objectContaining({ code: 'GROUP_ROUTE_UNSUPPORTED' }) });
  expect(fallback).not.toHaveBeenCalled();
});

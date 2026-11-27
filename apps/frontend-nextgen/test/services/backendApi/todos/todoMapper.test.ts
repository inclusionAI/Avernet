import type { TodoStatus } from '@/domain/todos/models';
import {
  mapTodoDto,
  mapTodoProductInteractionDto,
  mapTodoProductInteractionPageDto,
  mapTodoProgressDto,
} from '@/services/backendApi/todos/todoMapper';

const supportedStatuses: TodoStatus[] = [
  'not_started',
  'in_progress',
  'interrupted',
  'pending_action',
  'completed',
  'cancelled',
];

describe('mapTodoDto status contract', () => {
  test.each(supportedStatuses)('preserves supported status %s', (status) => {
    expect(mapTodoDto({ todo_id: 'todo-1', status }).status).toBe(status);
  });

  test.each([undefined, '', 'pending', 'pending_acceptance', ' IN_PROGRESS ', 'COMPLETED', 'unknown'])(
    'rejects unsupported status %p instead of silently changing it',
    (status) => {
      expect(() => mapTodoDto({ todo_id: 'todo-1', status })).toThrow(
        `Unsupported Todo status: ${JSON.stringify(status)}`,
      );
    },
  );
});

const stage = {
  stage_instance_id: 'stage-1',
  stage: 'stage-1',
  code: 'analyze',
  name: '需求分析',
  order: 1,
  kind: 'bot',
  status: 'in_progress',
  owner_user_id: 'human-1',
  owner_user_name: 'Human One',
  executor_subject_id: 'bot-1',
  executor_subject_name: 'Coding Bot',
  summary: '正在分析需求',
  current_step: '需求分析完成',
  steps: [{ step_id: 'm1', name: '需求分析完成', status: 'in_progress', detail: '需求分析完成' }],
  outputs: [{ artifact_id: 'artifact-1', kind: 'text', name: '需求理解', summary: '已形成需求理解' }],
  blocker: { reason: '等待确认', requested_action: '请验收', error_code: null },
  attempt_count: 2,
  details_loaded: true,
  messages_complete: false,
  ignored_message_count: 1,
  updated_at: '2026-11-27T10:00:00Z',
};

describe('mapTodoProgressDto contract', () => {
  it('映射稳定产品进度并保留 unknown 状态语义', () => {
    const progress = mapTodoProgressDto({
      todo_id: 'todo-1',
      status: 'in_progress',
      runtime_kind: 'product_collet',
      runtime_unavailable: false,
      current_stage_code: 'analyze',
      rollout_revision: 3,
      stopped: false,
      stages: [stage],
    });

    expect(progress).toEqual({
      todoId: 'todo-1',
      status: 'in_progress',
      runtimeKind: 'product_collet',
      runtimeUnavailable: false,
      runtimeErrorCode: undefined,
      currentStageCode: 'analyze',
      rolloutRevision: 3,
      stopped: false,
      stages: [
        expect.objectContaining({
          code: 'analyze',
          status: 'in_progress',
          detailsLoaded: true,
          messagesComplete: false,
          ignoredMessageCount: 1,
          attemptCount: 2,
          blocker: {
            reason: '等待确认',
            requestedAction: '请验收',
            errorCode: undefined,
          },
        }),
      ],
    });
  });

  it('未知阶段状态直接失败，不默认为未开始', () => {
    expect(() => mapTodoProgressDto({
      todo_id: 'todo-1',
      status: 'in_progress',
      runtime_kind: 'product_collet',
      runtime_unavailable: false,
      stages: [{ ...stage, status: 'weird' }],
    })).toThrow('Unsupported Todo progress stage status: "weird"');
  });
});


describe('mapTodoProductInteractionDto contract', () => {
  const interaction = {
    interaction_id: 'pi-1',
    todo_id: 'todo-1',
    kind: 'human_message',
    actor_id: '149844',
    actor_name: 'Human One',
    request_message: '请补充价格来源',
    delivery_state: 'succeeded',
    product_collet_id: 1,
    collet_rollout_id: 10,
    expected_revision: 3,
    response: { type: 'RE_DO', message: '已接受处理要求。' },
    created_at: '2026-11-27T10:00:00Z',
    updated_at: '2026-11-27T10:00:01Z',
  };

  it('映射 Product 互动并保留协议响应', () => {
    expect(mapTodoProductInteractionDto(interaction)).toEqual({
      interactionId: 'pi-1',
      todoId: 'todo-1',
      kind: 'human_message',
      actorId: '149844',
      actorName: 'Human One',
      requestMessage: '请补充价格来源',
      deliveryState: 'succeeded',
      productColletId: 1,
      colletRolloutId: 10,
      expectedRevision: 3,
      targetInteractionId: undefined,
      response: { type: 'RE_DO', message: '已接受处理要求。' },
      failureCode: undefined,
      createdAt: '2026-11-27T10:00:00Z',
      updatedAt: '2026-11-27T10:00:01Z',
    });
  });

  it('未知互动类型、投递状态和响应类型全部严格失败', () => {
    expect(() => mapTodoProductInteractionDto({ ...interaction, kind: 'Unknown' })).toThrow(
      'Unsupported Todo Product interaction kind: "Unknown"',
    );
    expect(() => mapTodoProductInteractionDto({ ...interaction, delivery_state: 'unknown' })).toThrow(
      'Unsupported Todo Product interaction state: "unknown"',
    );
    expect(() => mapTodoProductInteractionDto({ ...interaction, response: { type: 'Nope' } })).toThrow(
      'Unsupported Todo Product interaction response: "Nope"',
    );
  });

  it('映射页面写入锁并保持空列表稳定', () => {
    expect(mapTodoProductInteractionPageDto({ items: [], can_send: false, blocking_interaction_id: null })).toEqual({
      items: [],
      canSend: false,
      blockingInteractionId: undefined,
    });
  });
});

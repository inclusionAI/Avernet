import { mapTodoDto, mapTodoEventDto } from '@/services/backendApi/todos/todoMapper';

describe('todoMapper', () => {
  it('映射模板 Todo 与节点执行版本字段', () => {
    const todo = mapTodoDto({
      todo_id: 'todo-1',
      space_id: 10,
      title: '多节点任务',
      description: '从第三个节点重跑',
      bot_id: 'bot-1',
      template_id: 'template-1',
      status: 'completed',
      created_at: '2026-11-25T01:00:00Z',
      updated_at: '2026-11-25T02:00:00Z',
    });
    const event = mapTodoEventDto({
      event_id: 'event-stage-3-v2',
      event_seq: 1,
      event_type: 'artifact.reported',
      actor_id: 'bot-3',
      actor_type: 'bot',
      content: '第三个节点的新结果',
      execution_id: 'context-execution-3-v2',
      todo_execution_id: 'todoexec-stage-3-v2',
      stage_instance_id: 'stage-3',
      generation: 2,
      is_latest: true,
      replyable: true,
      created_at: '2026-11-25T02:00:00Z',
    });

    expect(todo.templateId).toBe('template-1');
    expect(event).toEqual(
      expect.objectContaining({
        todoExecutionId: 'todoexec-stage-3-v2',
        stageInstanceId: 'stage-3',
        generation: 2,
        isLatest: true,
        replyable: true,
        replyDisabledReason: undefined,
      }),
    );
  });

  it('旧响应缺少版本字段时禁止回复', () => {
    const event = mapTodoEventDto({
      event_id: 'legacy-event',
      event_seq: 1,
      event_type: 'user_interaction.posted',
      actor_id: 'user-1',
      actor_type: 'human',
      content: '旧响应',
      execution_id: 'legacy-execution',
      created_at: '2026-11-25T01:00:00Z',
    });

    expect(event.isLatest).toBe(true);
    expect(event.replyable).toBe(false);
    expect(event.todoExecutionId).toBeUndefined();
    expect(event.stageInstanceId).toBeUndefined();
  });

  it('映射历史版本的禁用原因', () => {
    const event = mapTodoEventDto({
      event_id: 'event-stage-3-v1',
      event_seq: 1,
      event_type: 'artifact.reported',
      actor_id: 'bot-3',
      actor_type: 'bot',
      content: '第三个节点的历史结果',
      execution_id: 'context-execution-3-v1',
      todo_execution_id: 'todoexec-stage-3-v1',
      stage_instance_id: 'stage-3',
      generation: 1,
      is_latest: false,
      replyable: false,
      reply_disabled_reason: 'superseded',
      created_at: '2026-11-25T01:00:00Z',
    });

    expect(event.isLatest).toBe(false);
    expect(event.replyable).toBe(false);
    expect(event.replyDisabledReason).toBe('superseded');
  });
});

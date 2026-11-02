import { projectGroupTasks } from '@/domain/groupTasks/projection';
import { projectTaskReceipts } from '@/domain/groupTasks/receiptProjection';
import { mapGroupHistoryMessages } from '@/services/workspace/groupMessageMapper';
import type { ChatMessage, ToolExecutionBlock } from '@tc-chat/core';

const receipt = {
  __bcs_coordination__: true,
  v: 2,
  tool: 'bcs_assign_task',
  intent_id: 'bcs_intent_04485427ac7c49608500000000000000',
  status: 'stored',
};
const participants = [{ actorId: 'worker-ray', kind: 'bot', name: 'ray', role: 'worker', mode: 'auto' }] as const;
const toolMessage = (
  output = JSON.stringify(receipt),
  input = JSON.stringify({
    command: 'mcporter call mcp.example.bcs_assign_task target_bot="ray" message="检查支付日志"',
  }),
): ChatMessage => ({
  id: 'manager-message',
  role: 'assistant',
  status: 'done',
  content: '已派发',
  createdAt: 1700000000000,
  blocks: [
    {
      type: 'tool_execution',
      steps: [{ id: 'call-1', tool: 'Bash', title: '执行脚本', status: 'success', input, output }],
    } as ToolExecutionBlock,
  ],
});

it('shows the actual pre-release stored acknowledgement without inventing execution', () => {
  const tasks = projectTaskReceipts([toolMessage()], [...participants]);
  expect(tasks).toHaveLength(1);
  expect(tasks[0]).toMatchObject({
    taskRef: 'T-04485427',
    summary: '检查支付日志',
    workerName: 'ray',
    workerId: 'worker-ray',
    status: 'unknown',
  });
  expect(tasks[0].deliveryId).toBeUndefined();
  expect(tasks[0].startedAt).toBeUndefined();
  expect(tasks[0].finishedAt).toBeUndefined();
});

it.each([
  JSON.stringify(receipt),
  JSON.stringify({ stdout: JSON.stringify(receipt, null, 2), stderr: '' }),
  JSON.stringify({ content: [{ type: 'text', text: JSON.stringify(receipt) }] }),
  `Exit code: 0\nOutput:\n${JSON.stringify(receipt, null, 2)}\n`,
])('reads a tool result wrapper and canonicalizes intent refs', (output) => {
  expect(projectTaskReceipts([toolMessage(output)], [])[0].taskRef).toBe('T-04485427');
});

it('retains complete intent identity and ignores a conflicting display ref', () => {
  const message = toolMessage(
    JSON.stringify({ ...receipt, task_ref: 'T-1234abcd' }),
    JSON.stringify({ target_bot: 'worker-ray', message: '检查索引' }),
  );
  expect(projectTaskReceipts([message], [...participants])[0]).toMatchObject({
    taskRef: 'T-04485427',
    intentId: receipt.intent_id,
    summary: '检查索引',
    workerName: 'ray',
  });
});

it('keeps tasks distinct for one worker and deduplicates repeated segments', () => {
  const second = toolMessage(JSON.stringify({ ...receipt, intent_id: 'bcs_intent_12345678000000000000000000000000' }));
  expect(projectTaskReceipts([toolMessage(), toolMessage(), second], [...participants])).toHaveLength(2);
});

it('does not parse assistant prose, failed acknowledgements, or unmarked tool JSON as task evidence', () => {
  expect(projectTaskReceipts([{ ...toolMessage(), blocks: [], content: JSON.stringify(receipt) }], [])).toEqual([]);
  expect(projectTaskReceipts([toolMessage(JSON.stringify({ ...receipt, __bcs_coordination__: false }))], [])).toEqual(
    [],
  );
  expect(projectTaskReceipts([toolMessage(JSON.stringify({ ...receipt, intent_id: 'invalid' }))], [])).toEqual([]);
});

it('preserves structured MCP receipt objects in restored history tool outputs', () => {
  const mapped = mapGroupHistoryMessages([
    {
      id: 'history-tool',
      sender: 'manager',
      message_type: 'bot',
      role: 'assistant',
      content: '',
      timestamp: 1700000000000,
      metadata: {
        tool_name: 'bcs_assign_task',
        tool_call_id: 'call-1',
        arguments: { target_bot: 'ray', message: '历史任务' },
        result: receipt,
      },
    } as never,
  ]);
  expect(projectGroupTasks([], mapped, [...participants])[0]).toMatchObject({
    taskRef: 'T-04485427',
    summary: '历史任务',
    status: 'unknown',
  });
});

it('reads the actual mcporter --args command syntax', () => {
  const message = toolMessage(JSON.stringify(receipt), JSON.stringify({ command: `mcporter call mcp.ant.agentclawscs.bcs_mcp.bcs_assign_task --args '{"target_bot":"ray","message":"核对日志"}'` }));
  expect(projectTaskReceipts([message], [...participants])[0]).toMatchObject({ workerName: 'ray', summary: '核对日志', source: 'receipt' });
});

it('does not treat a shell command reading old receipt text as a new assignment', () => {
  const message = toolMessage(JSON.stringify(receipt), JSON.stringify({ command: 'cat previous-tool-output.json' }));
  expect(projectTaskReceipts([message], [...participants])).toEqual([]);
});

it('replaces the provisional receipt with its authoritative delivery without double counting', () => {
  const tasks = projectGroupTasks([{
    delivery_id: 'd1', message_id: 'dispatch', target_bot_id: 'worker-ray', kind: 'send', flow_kind: 'task', status: 'running',
    state_version: 1, run_id: 'run-1', admission_error: null, wait_reason: null,
    task: { intent_id: receipt.intent_id, task_ref: 'T-04485427', summary: '已确认任务', manager_id: 'manager', worker_id: 'worker-ray', worker_name: 'ray',
      queued_at: 1, started_at: 2, last_activity_at: 3, finished_at: null, timed_out: false },
  }], [toolMessage()], [...participants]);
  expect(tasks).toHaveLength(1);
  expect(tasks[0]).toMatchObject({ source: 'delivery', status: 'running', summary: '已确认任务' });
});


it('keeps colliding full intents separate and only joins deliveries by complete identity', () => {
  const otherId = 'bcs_intent_04485427ffffffffffffffffffffffff';
  const messages = [toolMessage(), toolMessage(JSON.stringify({ ...receipt, intent_id: otherId }))];
  expect(projectTaskReceipts([...messages, ...messages], [])).toHaveLength(2);
  const delivery = {
    delivery_id: 'other-delivery', message_id: 'dispatch', target_bot_id: 'other-worker', kind: 'send', flow_kind: 'task', status: 'running',
    state_version: 1, run_id: 'run-2', admission_error: null, wait_reason: null,
    task: { intent_id: otherId, task_ref: 'T-04485427', summary: 'Other assignment', manager_id: 'manager', worker_id: 'other-worker', worker_name: 'Other',
      queued_at: 1, started_at: 2, last_activity_at: 3, finished_at: null, timed_out: false },
  } as const;
  const tasks = projectGroupTasks([delivery], messages, []);
  expect(tasks).toHaveLength(2);
  expect(tasks.find((t) => t.intentId === receipt.intent_id)).toMatchObject({ source: 'receipt', summary: '检查支付日志' });
  expect(tasks.find((t) => t.intentId === otherId)).toMatchObject({ source: 'delivery', workerId: 'other-worker', deliveryId: 'other-delivery' });
  const legacy = projectGroupTasks([{ ...delivery, task: { ...delivery.task, intent_id: undefined } }], messages, []);
  expect(legacy).toHaveLength(3);
  expect(legacy.find((t) => t.deliveryId)).toMatchObject({ intentId: undefined });
  // A collision may be outside loaded history: even one visible receipt cannot prove a short-ref join.
  expect(projectGroupTasks([{ ...delivery, task: { ...delivery.task, intent_id: undefined } }], [messages[0]], [])).toHaveLength(2);
});

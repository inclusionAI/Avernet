// Black-box lifecycle scenarios shared by the manual probe and Singlebox E2E.
const assert = require('node:assert/strict');

module.exports = async function scenarios(context) {
  const {api, until, manager, worker, dispatch, ref, status, cancel, policy} = context;
  const updatePolicy = async () => {
    const current = await api('/admin/message-delivery/policy');
    await api('/admin/message-delivery/policy', {
      expected_version: current.version,
      policy,
    }, null, 'PUT');
  };
  const sent = async reference => until(
    () => worker.sends.find(request => worker.text(request).includes(`ref=${reference}`)),
    `Worker receives ${reference}`,
  );
  const result = async (reference, heading, body) => until(
    () => manager.sends.find(request => {
      const text = manager.text(request);
      return text.startsWith(`[${heading}] ref=${reference}`) && text.includes(body);
    }),
    `Manager receives ${heading} for ${reference}`,
  );
  const assign = async (message, timeout) => {
    const task = await dispatch(message, timeout);
    const reference = ref(task);
    const request = await sent(reference);
    if (policy.flow_enabled.task) {
      await until(async () => await status(reference) === 'running', `acceptance of ${reference}`);
    }
    return {task, reference, request};
  };

  // Validate the actual HTTP adapter, including normalized, repeated selectors.
  const missing = 'T-00000000';
  assert.equal((await cancel(missing)).status, 'not_found');
  for (const input of [[], Array(21).fill(missing), ['not-a-ref'], ['12345678%']]) {
    await assert.rejects(api('/tasks/status', {task_refs: input}), /400/);
  }
  await assert.rejects(api('/tasks/cancel', {task_ref: missing, reason: ' '}), /400/);
  await assert.rejects(api('/tasks/cancel', {task_ref: missing, reason: 'x'.repeat(1025)}), /400/);
  await assert.rejects(manager.request('task.status', {task_refs: []}));

  const normalized = await assign('normalized reference');
  const selectors = [normalized.reference, normalized.task.task_id,
    normalized.reference.slice(2).toUpperCase(), normalized.reference];
  const batch = (await api('/tasks/status', {task_refs: selectors})).tasks;
  assert.deepEqual(batch.map(task => task.ref), selectors);
  assert(batch.every(task => task.status === 'running'));
  assert(batch.every(task => Number.isInteger(task.running_s)));
  worker.final(normalized.request, 'body stays unchanged\n---\n🙂');
  await until(async () => await status(normalized.reference) === 'completed', 'normalized completion');
  await result(normalized.reference, '任务完成', 'body stays unchanged\n---\n🙂');
  console.log('PASS: HTTP validation, normalized batched references and canonical result body');

  // A bounded synchronous wait must preserve an eventual confirmed stop.
  policy.task_cancel_wait_ms = 100;
  await updatePolicy();
  const delayed = await assign('delayed abort acknowledgement');
  worker.abortDelayMs = 350;
  assert.equal((await cancel(delayed.reference)).status, 'unknown');
  await until(async () => await status(delayed.reference) === 'cancelled', 'late abort confirmation');
  await result(delayed.reference, '任务中断', 'isolated test stop');
  worker.abortDelayMs = 0;
  policy.task_cancel_wait_ms = 5000;
  await updatePolicy();

  // Refusing an abort cannot be represented as a confirmed cancellation.
  const refused = await assign('Worker refuses cancellation');
  worker.rejectAbort = true;
  assert.equal((await cancel(refused.reference)).status, 'cancel_failed');
  worker.rejectAbort = false;
  worker.final(refused.request, 'finished after refusing abort');
  await until(async () => await status(refused.reference) === 'completed', 'refused task completes');
  console.log('PASS: delayed abort confirmation and explicit abort refusal');

  // Admission failure has a durable failed handle and never reaches the Worker.
  policy.bots[worker.id] = {...policy.bots[worker.id], max_queued: 1};
  await updatePolicy();
  const blocker = await assign('capacity blocker');
  const queued = await dispatch('capacity queued');
  const sendsBeforeRejection = worker.sends.length;
  const noticesBeforeRejection = manager.sends.length;
  await assert.rejects(dispatch('capacity rejected'));
  const rejection = await until(
    () => manager.sends.slice(noticesBeforeRejection).find(request =>
      manager.text(request).includes('[任务派发失败]')),
    'capacity rejection reaches Manager',
  );
  const rejectedRef = manager.text(rejection).match(/ref=(T-[0-9a-f]{8})/)[1];
  assert.equal(await status(rejectedRef), 'failed');
  assert.equal((await cancel(rejectedRef)).status, 'already_finished');
  assert.equal(worker.sends.length, sendsBeforeRejection);
  assert.equal((await cancel(ref(queued))).status, 'cancelled');
  assert.equal((await cancel(blocker.reference)).status, 'cancelled');
  delete policy.bots[worker.id];
  await updatePolicy();
  console.log('PASS: capacity rejection does not deliver to the Worker');

  // A timeout without an abort acknowledgement remains unknown, then accepts
  // a real late final. No direct DB/cache writes or domain calls create states.
  worker.ignoreAbort = true;
  const uncertain = await assign('unconfirmed timeout', 1);
  await until(
    () => manager.sends.some(request => manager.text(request).includes(
      `[任务超时，停止结果未确认] ref=${uncertain.reference}`)),
    'timeout uncertainty reaches Manager', 40000,
  );
  assert.equal(await status(uncertain.reference), 'unknown');
  assert.equal((await cancel(uncertain.reference)).status, 'unknown');
  worker.ignoreAbort = false;
  worker.final(uncertain.request, 'late confirmed result');
  await until(async () => await status(uncertain.reference) === 'completed', 'late final resolves uncertainty');
  console.log('PASS: unconfirmed timeout remains unknown until a real late final');

  // A fresh Session has no managed lane left to drain when disabling the task
  // queue. This exercises direct delivery without racing the previous results.
  await context.newSession();
  policy.flow_enabled.task = false;
  await updatePolicy();
  const legacy = await assign('legacy completion');
  worker.send({type: 'event', event: 'chat.event', seq: 0, payload: {
    run_id: legacy.request.id, bcs_group_id: legacy.request.params.bcs_group_id,
    state: 'delta', delta_text: 'working',
  }});
  await until(async () => await status(legacy.reference) === 'running', 'legacy progress');
  worker.final(legacy.request, 'legacy final body');
  await until(async () => await status(legacy.reference) === 'completed', 'legacy completion');
  await result(legacy.reference, '任务完成', 'legacy final body');
  assert.equal((await cancel(legacy.reference)).status, 'already_finished');

  const legacyFailure = await assign('legacy error');
  worker.final(legacyFailure.request, 'legacy failure reason', 'error');
  await until(async () => await status(legacyFailure.reference) === 'failed', 'legacy failure');
  await result(legacyFailure.reference, '任务失败', 'legacy failure reason');

  const legacyCancel = await assign('legacy cancellation');
  assert.equal((await manager.request('task.cancel', {
    task_ref: legacyCancel.reference, reason: 'stop legacy execution',
  })).status, 'cancelled');
  assert.equal((await cancel(legacyCancel.reference)).status, 'cancelled');
  await result(legacyCancel.reference, '任务中断', 'stop legacy execution');

  const legacyTimeout = await assign('legacy timeout', 1);
  await until(async () => await status(legacyTimeout.reference) === 'timed_out', 'legacy custom timeout');
  await result(legacyTimeout.reference, '任务超时', '超时');
  console.log('PASS: validation, normalized batches, delayed/rejected aborts, capacity, uncertainty, legacy lifecycle');
};

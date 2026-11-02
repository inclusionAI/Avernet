// Live HTTP/WS task controls backed by the real coordination cache store.
// The test-only producer fixture seeds cache records; all lifecycle transitions
// go through BCS transports. SQLite inspection is read-only.
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { execFileSync } = require('node:child_process');
module.exports = async ({ api, publicTaskApi, until, worker, dispatch, policy, newSession }) => {
  const fixture = process.env.BCS_TASK_CACHE_FIXTURE;
  if (!fixture) return;
  assert.match(fixture, /^http:\/\/127\.0\.0\.1:\d+$/);
  const id = () => `bcs_intent_${randomUUID().replaceAll('-', '')}`;
  const seed = async (intent_id, suffix, value) => {
    const response = await fetch(`${fixture}/record`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ intent_id, suffix, value }) });
    assert.equal(response.status, 200);
  };
  const status = async (intent) => (await publicTaskApi('status', { intent_ids: [intent] })).tasks[0].status;
  const cancel = (intent) => publicTaskApi('cancel', { intent_id: intent, reason: 'cache lookup E2E' });
  const missing = id();
  assert.equal(await status(missing), 'unknown');
  assert.equal((await cancel(missing)).status, 'unknown');
  const failed = id();
  await seed(failed, 'result', { status: 'failed', task_id: null, error_code: 'rejected' });
  assert.equal(await status(failed), 'failed');
  assert.equal((await cancel(failed)).status, 'already_finished');
  const stale = id();
  await seed(stale, 'result', { status: 'applied', task_id: randomUUID(), error_code: null });
  assert.equal(await status(stale), 'unknown');
  const orphan = id();
  await seed(orphan, 'delivery', randomUUID());
  assert.equal(await status(orphan), 'unknown');
  await newSession();
  policy.flow_enabled.task = true;
  const enabled = await api('/admin/message-delivery/policy');
  await api('/admin/message-delivery/policy', { expected_version: enabled.version, policy }, null, 'PUT');
  const task = await dispatch('managed complete-intent lookup');
  const deliveryId = execFileSync('python3', ['-c',
    'import sqlite3,sys; db=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); print(db.execute("SELECT delivery_id FROM bcs_message_deliveries WHERE json_extract(semantic_projection_json, \'$.task.task_id\') = ? AND json_extract(semantic_projection_json, \'$.task.leg\') = \'dispatch\'", (sys.argv[2],)).fetchone()[0])',
    process.env.BCS_TASK_CACHE_DB, task.task_id], { encoding: 'utf8' }).trim();
  const managed = id();
  await seed(managed, 'delivery', deliveryId);
  await until(async () => await status(managed) === 'running', 'full intent managed running');
  assert.equal((await api('/tasks/status', { intent_ids: [managed] })).tasks[0].status, 'running');
  const batch = await publicTaskApi('status', { intent_ids: [managed, missing, failed, managed, 'T-00000000'] });
  assert.deepEqual(batch.tasks.map(t => t.status), ['running', 'unknown', 'failed', 'running', 'not_found']);
  assert.equal((await cancel(managed)).status, 'cancelled');
  assert.equal(await status(managed), 'cancelled');
  // A managed ledger alone cannot authorize cancellation after correlation loss.
  const unmapped = id();
  await seed(unmapped, 'result', { status: 'applied', task_id: task.task_id, error_code: null });
  assert.equal(await status(unmapped), 'unknown');
  await newSession();
  const current = await api('/admin/message-delivery/policy');
  await api('/admin/message-delivery/policy', { expected_version: current.version, policy: { ...policy, flow_enabled: { ...policy.flow_enabled, task: false } } }, null, 'PUT');
  const count = worker.sends.length;
  const legacy = await dispatch('legacy complete-intent lookup');
  await until(() => worker.sends.length > count, 'legacy dispatch');
  const request = worker.sends[count];
  worker.send({ type: 'event', event: 'chat.event', seq: 0, payload: {
    run_id: request.id, bcs_group_id: request.params.bcs_group_id, state: 'delta', delta_text: 'working',
  } });
  const legacyIntent = id();
  await seed(legacyIntent, 'result', { status: 'applied', task_id: legacy.task_id, error_code: null });
  await until(async () => await status(legacyIntent) === 'running', 'full intent legacy running');
  assert.equal((await cancel(legacyIntent)).status, 'cancelled');
  assert.equal(await status(legacyIntent), 'cancelled');
  console.log('PASS: full intent HTTP/cache managed and legacy lookup, mixed batches, cancellation, rejected and missing correlations');
};

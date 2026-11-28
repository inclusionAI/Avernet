import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { ApiClient } from "../src/db/api-client.js";
import { FlowRunApiRepository } from "../src/db/api-repositories/flow-run-api-repository.js";
import { NodeExecutionApiRepository } from "../src/db/api-repositories/node-execution-api-repository.js";

type Request = { method: string; path: string; body: any };
async function fixture(t: any, handler: (request: Request) => { status?: number; body: unknown }) {
  const requests: Request[] = [];
  const server = createServer(async (req, res) => {
    let raw = ""; for await (const chunk of req) raw += chunk;
    const request = { method: req.method!, path: req.url!, body: raw ? JSON.parse(raw) : undefined };
    requests.push(request);
    const response = handler(request);
    res.writeHead(response.status ?? 200, { "content-type": "application/json" });
    res.end(JSON.stringify(response.body));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())));
  const { port } = server.address() as { port: number };
  const client = new ApiClient({ baseUrl: `http://127.0.0.1:${port}`, maxRetries: 1 });
  return { requests, runs: new FlowRunApiRepository(client), nodes: new NodeExecutionApiRepository(client) };
}
const completion = { status: "succeeded", currentPhase: "done", completedAt: 50 };
const reap = { reason: "timeout", currentPhase: "timeout", completedAt: 50 };

test("HTTP writes carry current_phase/retry_epoch, preserving positional customErrorText", async t => {
  const { requests, runs, nodes } = await fixture(t, () => ({ body: { success: true, data: { claimed: true, reconciled: 2 } } }));
  assert.equal(await runs.updateCompletion("flow /one", { ...completion, retryEpoch: 4 }), true);
  assert.equal(await runs.markFailedIfRunning("flow /one", { ...reap, retryEpoch: 4 }), true);
  assert.equal(await nodes.reconcileStaleRunning("flow /one", "failed", "custom error", 4), 2);
  assert.equal(await runs.updateCurrentPhase("flow /one", "run"), true);
  assert.equal(await runs.updateCompletion("legacy", completion), true);
  assert.equal(await runs.markFailedIfRunning("legacy", reap), true);
  assert.equal(await nodes.reconcileStaleRunning("legacy", "failed", "third argument"), 2);
  assert.deepEqual(requests.map(r => r.body.retry_epoch), [4, 4, 4, undefined, 0, 0, 0]);
  assert.equal(requests[0].body.current_phase, "done"); assert.equal(requests[0].body.phase, undefined);
  assert.equal(requests[1].body.current_phase, "timeout");
  assert.equal(requests[2].body.custom_error_text, "custom error");
  assert.equal(requests[6].body.custom_error_text, "third argument");
  assert.deepEqual(requests[3].body, { current_phase: "run", phase: "run" });
  assert.match(requests[0].path, /flow%20%2Fone\/completion$/);
  assert.ok(requests.every(r => r.method === "PUT"), "closeout must not query current epoch");
});

test("HTTP scan preserves retry epoch/state and legacy missing epoch means only zero", async t => {
  const { runs } = await fixture(t, () => ({ body: { success: true, data: [
    { flow_id: "new", status: "running", started_at: 1, retry_epoch: 5, state_json: '{"retryEpoch":5}' },
    { flow_id: "old", status: "running", started_at: 1 },
  ] } }));
  const rows = await runs.findStaleRunning(10);
  assert.equal(rows[0].retry_epoch, 5); assert.equal(rows[0].state_json, '{"retryEpoch":5}');
  assert.equal(rows[1].retry_epoch, 0);
});

test("HTTP invalid explicit epochs reject before sending any request", async t => {
  const { runs, nodes, requests } = await fixture(t, () => ({ body: { success: true, data: { claimed: true, reconciled: 1 } } }));
  for (const retryEpoch of [-1, "0", null, NaN, Infinity, 0.5]) {
    await assert.rejects(runs.updateCompletion("flow", { ...completion, retryEpoch } as any), /epoch/i);
    await assert.rejects(runs.markFailedIfRunning("flow", { ...reap, retryEpoch } as any), /epoch/i);
    await assert.rejects(nodes.reconcileStaleRunning("flow", "failed", undefined, retryEpoch as any), /epoch/i);
  }
  assert.equal(requests.length, 0);
});

for (const status of [404, 409]) {
  test(`HTTP ${status} errors retain error.status and never retry completion/reap/reconcile`, async t => {
    const { runs, nodes, requests } = await fixture(t, () => ({ status, body: { success: false, error: "rejected write" } }));
    const operations = [
      () => runs.updateCompletion("flow", completion),
      () => runs.markFailedIfRunning("flow", reap),
      () => nodes.reconcileStaleRunning("flow", "failed"),
      () => nodes.updateCompletion(1, completion),
      () => nodes.updateCompletionByFlowNode("flow", "node", 1, completion),
    ];
    for (const operation of operations) {
      await assert.rejects(operation(), (error: any) => error.status === status && /rejected|HTTP/.test(error.message));
    }
    assert.equal(requests.length, operations.length);
  });
}

test("HTTP rejected 2xx writes are errors, not successful persistence", async t => {
  const { runs, nodes } = await fixture(t, () => ({ body: { success: false, error: "database rejected write" } }));
  await assert.rejects(runs.updateCompletion("flow", completion), /rejected/);
  await assert.rejects(runs.markFailedIfRunning("flow", reap), /rejected/);
  await assert.rejects(nodes.reconcileStaleRunning("flow", "failed"), /rejected/);
});

test("HTTP 5xx persistence errors propagate after bounded transport retry", async t => {
  const { runs, nodes, requests } = await fixture(t, () => ({ status: 503, body: { success: false, error: "unavailable" } }));
  await assert.rejects(runs.updateCompletion("flow", completion), (error: any) => error.status === 503);
  await assert.rejects(runs.markFailedIfRunning("flow", reap), (error: any) => error.status === 503);
  await assert.rejects(nodes.reconcileStaleRunning("flow", "failed"), (error: any) => error.status === 503);
  assert.equal(requests.length, 6);
});

test("HTTP lost timeout CAS is false, not failure or claimed success", async t => {
  const { runs } = await fixture(t, () => ({ body: { success: true, data: { claimed: false } } }));
  assert.equal(await runs.markFailedIfRunning("flow", reap), false);
});


for (const data of [false, { success: false }]) {
  test(`HTTP explicit false write acknowledgement is rejected: ${JSON.stringify(data)}`, async t => {
    const { runs, nodes } = await fixture(t, () => ({ body: { success: true, data } }));
    await assert.rejects(runs.updateCompletion("flow", completion), /rejected/);
    await assert.rejects(nodes.updateCompletion(1, completion), /rejected/);
    await assert.rejects(nodes.updateCompletionByFlowNode("flow", "node", 1, completion), /rejected/);
  });
}

test("HTTP missing claim/reconciliation acknowledgement is not a benign zero", async t => {
  const { runs, nodes } = await fixture(t, () => ({ body: { success: true, data: {} } }));
  await assert.rejects(runs.markFailedIfRunning("flow", reap), /acknowledgement/);
  await assert.rejects(nodes.reconcileStaleRunning("flow", "failed"), /acknowledgement/);
});

test('manual retry wire uses retry-resume epoch+1; status mirrors carry captured epoch', async t => {
  const { runs, requests } = await fixture(t, () => ({ body: { success: true } }));
  assert.equal(await runs.resumeForRetry('flow', 8), 'resumed');
  assert.equal(await runs.updateStatus('flow', 'running', 8), true);
  assert.equal(await runs.updateStatus('flow', 'failed', 8), false);
  assert.equal(await runs.updateCurrentPhase('flow', 'P3', 8), true);
  assert.deepEqual(requests.map(r => r.body.retry_epoch), [8, 8, 8]);
  assert.match(requests[0].path, /\/retry-resume$/);
  assert.equal(requests[2].body.current_phase, 'P3');
  await assert.rejects(runs.resumeForRetry('flow', 0), /positive/);
});


test('retry admission sends a captured next-attempt checkpoint and rejects mismatches before HTTP', async t => {
  const { runs, requests } = await fixture(t, () => ({ body: { success: true } }));
  const stateJson = JSON.stringify({ workflowId: 'wf', retryEpoch: 8, effectiveFlowTimeoutMinutes: 8200 });
  assert.equal(await runs.resumeForRetry('flow', 8, stateJson), 'resumed');
  assert.deepEqual(requests[0].body, { retry_epoch: 8, state_json: stateJson });
  await assert.rejects(runs.resumeForRetry('flow', 9, stateJson), /attempt/);
  assert.equal(requests.length, 1);
});

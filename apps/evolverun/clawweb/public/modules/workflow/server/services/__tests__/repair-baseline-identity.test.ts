// @vitest-environment node
import { expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { parse } from 'yaml';
import { digestCanonicalJson } from '@avernet/clawevolve/server/services/evolution/contracts';
import { repairProposalSpecDigest } from '../../contracts/repair-baseline-identity.js';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { assertProposalBaseline } from '../repair-selection-review.js';
import express from 'express';
import { createRepairBatchesRouter } from '../../routes/repair-batches.js';

// Actual tag f3ce902517df7d47485dbd2ac364b8675d85d36d, deploy160/run version161.
// Original report AN-5FD1FCE072BA4731A340 has this unchanged baseSpecDigest.
const originalDigest = 'a8ec32900df132bf751addce2dc84bb8b46ec9640cd1f31bd699a5bf648f039f';
const originalId = '515c4fb8c5bb8ca8bf8966c7acd3732fc0177e12b474fd722ba1c573c90c41b5';
const yaml = readFileSync(new URL('./fixtures/tech-research-v2-deploy-160.yaml', import.meta.url), 'utf8');
const tagged = parse(yaml);
const { version: _version, ...deployed } = tagged;
const proposal = JSON.parse(readFileSync(new URL('./fixtures/tech-research-v2-report-proposal.json', import.meta.url), 'utf8'));

it('binds the actual fresh report identity to deploy160 without replacing the raw Git digest', async () => {
  expect(createHash('sha256').update(yaml).digest('hex')).toBe('2438c3929eecd0fea9ab459f7ef26acbea2d84b913715d83b412d1aef72c6855');
  expect(digestCanonicalJson(tagged)).toBe('98a9aa4bec4130bc12bada0669f091cca2704a29d84d636ed1849665ce4dae84');
  expect(digestCanonicalJson(deployed)).toBe(originalDigest);
  expect(digestCanonicalJson(proposal)).toBe('805fc37557042f98370559a06a00e3c00dab1e0727b2cc245c407e94eb499469');
  const f = await repairFixture(1);
  let server: ReturnType<ReturnType<typeof express>['listen']> | undefined;
  try {
    await f.db.exec("INSERT INTO workflow_specs VALUES ('tech-research-v2')");
    const baseline = { ...await f.executionPort.resolveBaseline('tech-research-v2'), packId: 'tech-research-v2', releaseRevision: 161, activeDeployNumber: 160,
      specPath: 'workflows/tech-research-v2.yaml', packCommit: 'f3ce902517df7d47485dbd2ac364b8675d85d36d', packDigest: '037da30b48f840ffab2218398f1693f12581e0d1', repoId: 'clawflows',
      specDigest: createHash('sha256').update(yaml).digest('hex'), proposalSpecDigest: repairProposalSpecDigest(tagged, deployed) };
    f.executionPort.resolveBaseline = async () => baseline;
    f.items[0].itemId = originalId;
    f.items[0].proposal = structuredClone(proposal);
    const original = structuredClone(f.items[0]);
    const source = { ...f.sourcePort, load: async () => [{ item: f.items[0], episodeKey: 'initial' }] };
    const service = createRepairWorkbenchService(f.db, source, f.executionPort);
    const app = express(); app.use(express.json());
    app.use('/api/workflow-repairs', createRepairBatchesRouter({ service, authorize: async () => ({ actorId: 'human', canEdit: true }) }));
    server = app.listen(0, '127.0.0.1'); await new Promise<void>(resolve => server!.once('listening', resolve));
    const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/workflow-repairs`;
    const shown = await (await fetch(url + '/candidates?workflowId=tech-research-v2')).json();
    const request = { workflowId: 'tech-research-v2', itemIds: [originalId], inputDigest: shown.inputDigest,
      selection: { [originalId]: shown.items[0].selectionDigest! }, instructions: '保留业务约束', requestId: 'real-report-regression' };
    const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) });
    expect(response.status).toBe(202);
    const created = await response.json();
    expect(created.input.integration).toBeUndefined();
    expect(created.input.baseline).toEqual(baseline);
    expect(created.input.items[0].proposal).toEqual(original.proposal);
    expect(created.taskId).toBeTruthy(); expect(f.calls).toHaveLength(1);
    expect((await service.create('human', request)).taskId).toBe(created.taskId);
    expect(f.calls).toHaveLength(1);
  } finally { if (server) await new Promise<void>(resolve => server!.close(() => resolve())); f.raw.close(); }
});

it.each([
  (spec: any) => { spec.nodes[0].executor.timeoutSeconds = 999; },
  (spec: any) => { spec.nodes.reverse(); },
  (spec: any) => { spec._businessPolicy = 'unknown fields count'; },
  (spec: any) => { spec.nodes[0].version = 'nested business version'; },
  (spec: any) => { spec.version = { businessPolicy: 'not scalar metadata' }; },
])('uses deployed configuration identity without blocking background work on a Git representation difference', change => {
  const changed = structuredClone(deployed); change(changed);
  expect(repairProposalSpecDigest(tagged, changed)).toBe(digestCanonicalJson(changed));
});

it('does not confuse raw bytes with semantic identity or silently accept missing proposal metadata', () => {
  const baseline = { proposalSpecDigest: originalDigest, specDigest: '2438c3929eecd0fea9ab459f7ef26acbea2d84b913715d83b412d1aef72c6855' } as any;
  const item = { itemId: 'actual-report', proposal: { baseSpecDigest: baseline.specDigest, operations: [{ op: 'replace' }] } } as any;
  expect(() => assertProposalBaseline([item], baseline)).toThrow('基线已变化');
  delete item.proposal.baseSpecDigest;
  expect(() => assertProposalBaseline([item], baseline)).toThrow('缺少可核对的配置摘要');
  item.proposal.baseSpecDigest = originalDigest;
  expect(() => assertProposalBaseline([item], baseline)).not.toThrow();
});

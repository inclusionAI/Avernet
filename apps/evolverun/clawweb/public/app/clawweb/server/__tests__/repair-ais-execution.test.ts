import { afterEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { RepairGitStore, createRepairAisExecution, resolveRepairAisConfiguration } from '../repair-ais-execution.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { AistudioService } from '@avernet/clawinsight/server/services/aistudio-service';
import { digestRepairJson } from '@avernet/workflow/server/contracts/repair-batch';
import { RepairResultAbsentError } from '@avernet/workflow/server/contracts/repair-workbench';

const roots: string[] = [];
afterEach(() => { for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true }); });

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'repair-git-test-'));
  roots.push(root);
  const source = join(root, 'source');
  const remote = join(root, 'remote.git');
  const reader = join(root, 'reader.git');
  mkdirSync(source);
  const git = (cwd: string, ...args: string[]) => execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
  git(source, 'init', '-q');
  git(source, 'config', 'user.name', 'test');
  git(source, 'config', 'user.email', 'test@example.com');
  mkdirSync(join(source, 'workflows'));
  writeFileSync(join(source, 'workflow.pack.yaml'), 'id: pack\nworkflows:\n  - id: wf\n    file: workflows/wf.yaml\n');
  const spec = 'id: wf\nnodes: []\n';
  writeFileSync(join(source, 'workflows/wf.yaml'), spec);
  git(source, 'add', '.');
  git(source, 'commit', '-qm', 'baseline');
  git(source, 'tag', 'deploy/wf/#1');
  const baseline = git(source, 'rev-parse', 'HEAD');
  git(root, 'init', '--bare', '-q', remote);
  git(source, 'remote', 'add', 'origin', remote);
  git(source, 'push', '-q', 'origin', 'HEAD:refs/heads/master', 'refs/tags/deploy/wf/#1');
  git(root, 'clone', '--bare', '-q', remote, reader);
  return { source, remote, reader, git, baseline, spec };
}

describe('Workflow Repair Git-backed AIS execution', () => {
  it.each(['valid','wrong-execution','changed-tree'])('reads failure diagnostics without a candidate and rejects invalid binding: %s',async(mode)=>{
    const f=fixture();
    const identity={stepId:'step',attempt:1,executionId:'execution'};
    const error={code:'CANDIDATE_WRITE_FAILED',message:'模型结果不是合法 JSON',stage:'parse_model_output',details:{line:1,column:2,itemIds:['item-1']}};
    const payload={schemaVersion:'workflow-repair-result/v1',taskId:'task',revision:1,...identity,parentCandidateCommit:f.baseline,status:'failed',error};
    if(mode==='wrong-execution')payload.executionId='other';
    if(mode==='changed-tree'){writeFileSync(join(f.source,'workflows/wf.yaml'),'partial change');f.git(f.source,'add','.');}
    f.git(f.source,'commit','--allow-empty','-qm','ClawMind workflow-repair-failure/v1\n'+JSON.stringify(payload));
    f.git(f.source,'push','-q','origin','HEAD:refs/heads/repair-failure/task/1-execution');
    const request={actorId:'owner',jobId:'job',identity,input:{taskId:'task',revision:1,taskBranch:'repair/wf/task',baseline:{packCommit:f.baseline}}} as never;
    const result=new RepairGitStore(f.reader).result(request);
    if(mode==='valid')await expect(result).resolves.toEqual({status:'failed',error});
    else await expect(result).rejects.toThrow();
  });
  it('stops with the original submission identity supplied by the lifecycle', async () => {
    const stopExecution = vi.fn();
    const adapter = createRepairAisExecution({} as IDatabase, { stopExecution } as unknown as AistudioService,
      {} as RepairGitStore, 1, 'https://clawweb.example');
    await adapter.stop({ jobId: 'job', actorId: '001234' } as Parameters<typeof adapter.stop>[0]);
    expect(stopExecution).toHaveBeenCalledWith('job', '001234');
  });
  it('declares its existing v1 integration transport for historical goal generation', () => {
    const adapter = createRepairAisExecution({} as IDatabase, {} as AistudioService, {} as RepairGitStore, 1, 'https://clawweb.example');
    expect(adapter.integrationVersion).toBe('workflow-proposal-plan/v1');
  });

  it('rejects partial or invalid repair configuration but permits an unconfigured optional provider', async () => {
    await expect(resolveRepairAisConfiguration('fixture')).resolves.toBeUndefined();
    await expect(resolveRepairAisConfiguration('fixture', 'not-a-number', '/missing/repair-mirror.git'))
      .rejects.toThrow('snapshot');
    await expect(resolveRepairAisConfiguration('fixture', '28720256'))
      .rejects.toThrow('Git');
    await expect(resolveRepairAisConfiguration(undefined, '28720256', '/missing/repair-mirror.git'))
      .rejects.toThrow('token');
    await expect(resolveRepairAisConfiguration('fixture', '28720256', '/missing/repair-mirror.git'))
      .rejects.toThrow('Git');
    const f = fixture();
    await expect(resolveRepairAisConfiguration('fixture', '28720256', f.reader))
      .resolves.toMatchObject({ snapshotId: 28720256 });
  }, 15_000);

  it('agrees with the TaskGuard full-draft canonical digest fixture', () => {
    expect(digestRepairJson({ repoId: 'clawflows', branch: 'repair/example/WR-1',
      candidateCommit: 'a'.repeat(40), parentCandidateCommit: 'b'.repeat(40),
      packDigest: 'c'.repeat(40), summary: '修复建议', itemResults: [] }))
      .toBe('441d2670ec7322879c1dd954da12c3ca2b98ef4c0b58cdb6262c9a621229903c');
  });
  it('reads both draft and background baselines without a configuration-equality gate and retains Pack identity checks', async () => {
    const f = fixture();
    const deployedSpec = { id: 'wf', nodes: [{ id: 'changed-in-history' }] };
    const row = { pack_id: 'pack', version: 4, deploy_number: 1, tag_name: 'deploy/wf/#1', spec_json: JSON.stringify(deployedSpec) };
    const db = { query: async () => [row] } as unknown as IDatabase;
    const execute = vi.fn();
    const store = new RepairGitStore(f.reader);
    const adapter = createRepairAisExecution(db, { execute } as unknown as AistudioService, store, 28720256, 'https://clawweb.example');
    await expect(adapter.resolveBaseline('wf', 'draft')).resolves.toMatchObject({
      workflowId: 'wf', packId: 'pack', packCommit: f.baseline,
      packDigest: f.git(f.source, 'rev-parse', 'HEAD^{tree}'), specPath: 'workflows/wf.yaml',
      specDigest: createHash('sha256').update(f.spec).digest('hex'), proposalSpecDigest: digestRepairJson(deployedSpec),
    });
    await expect(adapter.resolveBaseline('wf')).resolves.toMatchObject({
      workflowId: 'wf', packCommit: f.baseline, proposalSpecDigest: digestRepairJson(deployedSpec),
      specDigest: createHash('sha256').update(f.spec).digest('hex'),
    });
    row.pack_id = 'wrong-pack';
    await expect(adapter.resolveBaseline('wf', 'draft')).rejects.toThrow('expected Pack workflow');
    expect(execute).not.toHaveBeenCalled();
  });
  it('resolves a frozen active tag and reads only identity-bound Git result metadata', async () => {
    const f = fixture();
    const store = new RepairGitStore(f.reader);
    await expect(store.verify()).resolves.toBeUndefined();
    await expect(new RepairGitStore(f.source).verify()).rejects.toThrow('bare mirror');
    const baseline = await store.baseline({ workflowId: 'wf', packId: 'pack', version: 4,
      deployNumber: 1, tagName: 'deploy/wf/#1', specJson: f.spec });
    expect(baseline).toMatchObject({ packCommit: f.baseline, releaseRevision: 4,
      specDigest: createHash('sha256').update(f.spec).digest('hex'), proposalSpecDigest: digestRepairJson({ id: 'wf', nodes: [] }), specPath: 'workflows/wf.yaml' });

    writeFileSync(join(f.source, 'workflows/wf.yaml'), 'id: wf\nnodes: [fixed]\n');
    f.git(f.source, 'commit', '-qam', 'candidate');
    const candidate = f.git(f.source, 'rev-parse', 'HEAD');
    const tree = f.git(f.source, 'rev-parse', 'HEAD^{tree}');
    const branch = 'repair/wf/WR-1';
    const draft = { repoId: 'clawflows', branch, candidateCommit: candidate,
      parentCandidateCommit: f.baseline, packDigest: tree, summary: 'fixed', itemResults: [] };
    const checks = { candidateCommit: candidate, packDigest: tree };
    const result = { schemaVersion: 'workflow-repair-result/v1', taskId: 'WR-1', stepId: 'WR-1-draft-2',
      revision: 2, attempt: 1, executionId: 'WRX-1', status: 'succeeded', draft, checks };
    const message = `ClawMind workflow-repair-result/v1\n${JSON.stringify(result)}`;
    f.git(f.source, 'commit', '--allow-empty', '-qm', message);
    f.git(f.source, 'push', '-q', 'origin', `HEAD:refs/heads/${branch}`);
    const request = { actorId: '123', identity: { stepId: 'WR-1-draft-2', attempt: 1, executionId: 'WRX-1' },
      input: { taskId: 'WR-1', revision: 2, parentCandidateCommit: null, taskBranch: branch, baseline } } as Parameters<typeof store.result>[0];
    expect(await store.result(request)).toMatchObject({ status: 'succeeded', draft });
    const failure={...result,status:'failed',draft:undefined,checks:undefined,parentCandidateCommit:f.baseline,error:{code:'CANDIDATE_WRITE_FAILED',message:'late push error'}};
    const failureCommit=f.git(f.source,'commit-tree',f.git(f.source,'rev-parse',`${f.baseline}^{tree}`),'-p',f.baseline,'-m','ClawMind workflow-repair-failure/v1\n'+JSON.stringify(failure));
    f.git(f.source,'push','-q','origin',`${failureCommit}:refs/heads/repair-failure/WR-1/2-${request.identity.executionId}`);
    expect(await store.result(request)).toMatchObject({status:'succeeded',draft});
    expect(await store.diff({ workflowId: 'wf', taskId: 'WR-1', revision: 1,
      baseCommit: f.baseline, candidateCommit: candidate })).toMatchObject({
        files: [{ path: 'workflows/wf.yaml', status: 'M', before: f.spec, after: 'id: wf\nnodes: [fixed]\n',
          patch: expect.stringContaining('-nodes: []\n+nodes: [fixed]'), binary: false, truncated: false }],
      truncated: false,
    });
    await expect(store.result({ ...request, identity: { ...request.identity, executionId: 'other' } }))
      .rejects.toThrow('does not match');
    await expect(store.result({ ...request, input: { ...request.input, revision: 4,
      parentCandidateCommit: candidate, previousReportRef: 'WR-1/revisions/2' } }))
      .rejects.toBeInstanceOf(RepairResultAbsentError);

    writeFileSync(join(f.source, 'workflows/wf.yaml'), 'id: wf\nnodes: [second]\n');
    f.git(f.source, 'commit', '-qam', 'candidate revision two');
    const secondCandidate = f.git(f.source, 'rev-parse', 'HEAD');
    const secondTree = f.git(f.source, 'rev-parse', 'HEAD^{tree}');
    const secondDraft = { ...draft, candidateCommit: secondCandidate,
      parentCandidateCommit: candidate, packDigest: secondTree };
    const secondResult = { ...result, stepId: 'WR-1-draft-4', revision: 4,
      executionId: 'WRX-2', draft: secondDraft, checks: { candidateCommit: secondCandidate, packDigest: secondTree } };
    f.git(f.source, 'commit', '--allow-empty', '-qm', `ClawMind workflow-repair-result/v1\n${JSON.stringify(secondResult)}`);
    f.git(f.source, 'push', '-q', 'origin', `HEAD:refs/heads/${branch}`);
    expect(await store.result({ ...request, identity: { stepId: 'WR-1-draft-4', attempt: 1,
      executionId: 'WRX-2' }, input: { ...request.input, revision: 4, parentCandidateCommit: candidate, previousReportRef: 'WR-1/revisions/2' } }))
      .toMatchObject({ status: 'succeeded', draft: secondDraft });
    f.git(f.reader, 'gc', '--prune=now');
    expect(await store.diff({ workflowId: 'wf', taskId: 'WR-1', revision: 1,
      baseCommit: f.baseline, candidateCommit: candidate })).toMatchObject({
        files: [{ path: 'workflows/wf.yaml', status: 'M' }], truncated: false,
      });
  }, 20_000);

  it('returns bounded hunks for large text, detects binary and retains mode-only changes', async () => {
    const f = fixture();
    const text = Array.from({ length: 2500 }, (_, i) => `line-${i}`).join('\n') + '\n';
    writeFileSync(join(f.source, 'large.txt'), text);
    writeFileSync(join(f.source, 'binary.dat'), Buffer.from([0, 1, 2]));
    f.git(f.source, 'add', '.'); f.git(f.source, 'commit', '-qm', 'large baseline');
    const baseCommit = f.git(f.source, 'rev-parse', 'HEAD');
    writeFileSync(join(f.source, 'large.txt'), text.replace('line-1250\n', 'changed-1250\n'));
    writeFileSync(join(f.source, 'binary.dat'), Buffer.from([0, 3, 4]));
    f.git(f.source, 'update-index', '--chmod=+x', 'workflows/wf.yaml');
    f.git(f.source, 'add', 'large.txt', 'binary.dat'); f.git(f.source, 'commit', '-qm', 'candidate');
    const candidateCommit = f.git(f.source, 'rev-parse', 'HEAD');
    f.git(f.source, 'push', '-q', 'origin', 'HEAD:refs/heads/diff-cases');
    f.git(f.reader, 'fetch', '-q', 'origin', '+refs/heads/diff-cases:refs/workflow-repair-fixture/diff-cases');
    const result = await new RepairGitStore(f.reader).diff({ workflowId: 'wf', taskId: 'WR-1', revision: 1, baseCommit, candidateCommit });
    expect(result.files.find(file => file.path === 'large.txt')).toMatchObject({
      patch: expect.stringContaining('-line-1250\n+changed-1250'), truncated: false, binary: false });
    expect(result.files.find(file => file.path === 'binary.dat')).toMatchObject({ binary: true });
    expect(result.files.find(file => file.path === 'workflows/wf.yaml')).toMatchObject({
      patch: expect.stringContaining('old mode 100644\nnew mode 100755'), truncated: false });
  }, 20_000);

  it('marks oversized patches incomplete and bounds the total patch payload', async () => {
    const f = fixture();
    for (let i = 0; i < 10; i++) writeFileSync(join(f.source, `patch-${i}.txt`), 'x'.repeat(110_000));
    writeFileSync(join(f.source, 'oversized.txt'), 'x'.repeat(150_000));
    f.git(f.source, 'add', '.'); f.git(f.source, 'commit', '-qm', 'bounded patches');
    const candidateCommit = f.git(f.source, 'rev-parse', 'HEAD');
    f.git(f.source, 'push', '-q', 'origin', 'HEAD:refs/heads/bounded-patches');
    f.git(f.reader, 'fetch', '-q', 'origin', '+refs/heads/bounded-patches:refs/workflow-repair-fixture/bounded-patches');
    const result = await new RepairGitStore(f.reader).diff({ workflowId: 'wf', taskId: 'WR-1', revision: 1,
      baseCommit: f.baseline, candidateCommit });
    expect(result.files.find(file => file.path === 'oversized.txt')).toMatchObject({ patch: undefined, truncated: true });
    expect(result.files.some(file => file.path.startsWith('patch-') && file.truncated)).toBe(true);
    expect(result.files.reduce((n, file) => n + Buffer.byteLength(file.patch ?? ''), 0)).toBeLessThanOrEqual(1024 * 1024);
  }, 20_000);

  it('truncates a valid long-path file list instead of exhausting the Git output buffer', async () => {
    const f = fixture();
    const directory = join('workflows', 'a'.repeat(220), 'b'.repeat(220), 'c'.repeat(220));
    mkdirSync(join(f.source, directory), { recursive: true });
    for (let index = 0; index < 180; index += 1) {
      writeFileSync(join(f.source, directory, `${'d'.repeat(160)}-${String(index).padStart(3, '0')}.txt`), 'x');
    }
    f.git(f.source, 'add', '.');
    f.git(f.source, 'commit', '-qm', 'many valid paths');
    const candidate = f.git(f.source, 'rev-parse', 'HEAD');
    f.git(f.source, 'push', '-q', 'origin', 'HEAD:refs/heads/large-diff');
    f.git(f.reader, 'fetch', '-q', 'origin', '+refs/heads/large-diff:refs/workflow-repair-fixture/large-diff');
    const result = await new RepairGitStore(f.reader).diff({ workflowId: 'wf', taskId: 'WR-1',
      revision: 1, baseCommit: f.baseline, candidateCommit: candidate });
    expect(result).toMatchObject({ truncated: true });
    expect(result.files).toHaveLength(20);
  }, 30_000);

  it('dispatches the frozen v2 envelope as one AIS global parameter', async () => {
    const execute = vi.fn().mockResolvedValue('job-1');
    const adapter = createRepairAisExecution({} as IDatabase, { execute } as unknown as AistudioService,
      {} as RepairGitStore, 28720256, 'https://clawweb.example');
    const input = { taskId: 'WR-1', revision: 1, excludedSources: { count: 3, digest: 'a'.repeat(64) } };
    expect(await adapter.dispatch({ actorId: '123', identity: { stepId: 'WR-1-draft-2', attempt: 1,
      executionId: 'WRX-1' }, input } as Parameters<typeof adapter.dispatch>[0])).toEqual({ jobId: 'job-1' });
    const [actor, globals, snapshot] = execute.mock.calls[0];
    expect(actor).toBe('123');
    expect(snapshot).toBe(28720256);
    expect(JSON.parse(globals['${clawevolve_params}'])).toMatchObject({ schemaVersion: 'clawevolve-task/v1',
      taskType: 'workflow_repair', input });
  });
  it.each([false,true])('dispatches core and scoped materials through the production adapter (integration=%s)',async(integrated)=>{
    const execute=vi.fn().mockResolvedValue('job-materials');
    const adapter=createRepairAisExecution({} as IDatabase,{execute} as unknown as AistudioService,{} as RepairGitStore,99,'https://clawweb.example');
    const input={taskId:'WR-1',revision:1,materials:{schemaVersion:'repair-frozen-materials/v1',recordId:'a'.repeat(64),sha256:'b'.repeat(64),bytes:5600000},items:[{itemId:'item-1',instruction:'keep original goal',context:{facts:['expected string, actual object']}}],...(integrated?{integration:{schemaVersion:'workflow-proposal-plan/v1'}}:{})};
    const materialAccess={schemaVersion:'repair-material-download/v1',token:'dummy',files:[{itemId:'item-1',bytes:5600000,sha256:'c'.repeat(64)}]};
    await adapter.dispatch({actorId:'owner',identity:{stepId:'step',attempt:1,executionId:'exec'},input,materialAccess} as Parameters<typeof adapter.dispatch>[0]);
    const envelope=JSON.parse(execute.mock.calls[0][1]['${clawevolve_params}']);
    expect(adapter.materialDeliveryVersion).toBe('repair-material-download/v1');
    expect(envelope.input).toEqual(input);
    expect(envelope.materialAccess).toEqual({...materialAccess,baseUrl:'https://clawweb.example/api/workflow-repairs/material-download'});
    expect(envelope.execution.action).toBe(integrated?'integrate_candidate_materials_v1':'generate_candidate_materials_v1');
    expect(Buffer.byteLength(JSON.stringify(envelope))).toBeLessThan(10000);
  });

  it.each(['http://remote.example','https://clawweb.example/path','https://clawweb.example/?token=example'])('rejects an unsafe material service origin before any AIS call: %s',(origin)=>{
    const execute=vi.fn();
    expect(()=>createRepairAisExecution({} as IDatabase,{execute} as unknown as AistudioService,{} as RepairGitStore,99,origin)).toThrow('origin');
    expect(execute).not.toHaveBeenCalled();
  });
  it('refuses a frozen material reference without download access before calling AIS',async()=>{
    const execute=vi.fn();
    const adapter=createRepairAisExecution({} as IDatabase,{execute} as unknown as AistudioService,{} as RepairGitStore,99,'https://clawweb.example');
    await expect(adapter.dispatch({input:{materials:{}},identity:{stepId:'step',attempt:1,executionId:'exec'},actorId:'actor'} as Parameters<typeof adapter.dispatch>[0])).rejects.toThrow('Frozen material access');
    expect(execute).not.toHaveBeenCalled();
  });

});

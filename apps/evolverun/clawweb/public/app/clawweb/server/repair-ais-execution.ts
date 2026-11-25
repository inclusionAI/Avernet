/** Host-owned Workflow Repair AIS and ClawFlows Git adapter. Never trust AIS
 * lifecycle status as a business result; only the checked Git result is read. */
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { parse as parseYaml } from 'yaml';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import {repairFailureRef,readRepairFailure} from '@avernet/workflow/server/contracts/repair-failure';
import { WorkflowDeployHistoryRepository } from '@avernet/workflow/server/repositories/workflow-deploy-history-repository';
import { digestRepairJson, type RepairBaseline } from '@avernet/workflow/server/contracts/repair-batch';
import { repairProposalSpecDigest } from '@avernet/workflow/server/contracts/repair-baseline-identity';
import type { RepairExecutionPort, RepairExecutionReadRequest, RepairGeneratedResult } from '@avernet/workflow/server/contracts/repair-workbench';
import { RepairResultAbsentError } from '@avernet/workflow/server/contracts/repair-workbench';
import type { AistudioService } from '@avernet/clawinsight/server/services/aistudio-service';

const runFile = promisify(execFile);
const objectId = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const safeBranch = /^repair\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+$/;
const MAX_GIT_OUTPUT = 2 * 1024 * 1024;
const MAX_FILE_PATCH = 128 * 1024;
const MAX_PATCH_TOTAL = 1024 * 1024;

export class RepairGitStore {
  constructor(private readonly gitDir: string) {}

  async verify(): Promise<void> {
    if ((await this.git(['rev-parse', '--is-bare-repository'])).trim() !== 'true') {
      throw new Error('Workflow Repair Git directory must be an independent bare mirror');
    }
    if (!(await this.git(['remote', 'get-url', 'origin'])).trim()) {
      throw new Error('Workflow Repair Git mirror has no origin');
    }
  }

  private async git(args: string[], maxBuffer = MAX_GIT_OUTPUT): Promise<string> {
    try {
      const { stdout } = await runFile('git', ['-C', this.gitDir, ...args], {
        encoding: 'utf8', timeout: 45_000, maxBuffer,
      });
      return stdout;
    } catch {
      // Git stderr can echo a credential-bearing origin URL.
      throw new Error(`Repair Git ${args[0]} failed`);
    }
  }

  private async blob(commit: string, path: string): Promise<{ text: string | null; binary: boolean; truncated: boolean }> {
    const object = `${commit}:${path}`;
    const size = Number((await this.git(['cat-file', '-s', object], 1024)).trim());
    if (!Number.isSafeInteger(size) || size < 0) throw new Error('Invalid repair Git blob size');
    if (size > 8_000) return { text: null, binary: false, truncated: true };
    let stdout: Buffer;
    try {
      ({ stdout } = await runFile('git', ['-C', this.gitDir, 'show', object], {
        encoding: 'buffer', timeout: 45_000, maxBuffer: 9_000,
      }));
    } catch { throw new Error('Repair Git show failed'); }
    try { return { text: new TextDecoder('utf-8', { fatal: true }).decode(stdout as Buffer), binary: false, truncated: false }; }
    catch { return { text: null, binary: true, truncated: false }; }
  }

  private async fetch(ref: string): Promise<string> {
    if (!ref.startsWith('refs/tags/') && !ref.startsWith('refs/heads/')) throw new Error('Invalid repair Git ref');
    // Keep fetched heads/tags reachable across mirror GC for saved historical diffs.
    const retained = `refs/workflow-repair-cache/${ref.slice('refs/'.length)}`;
    await this.git(['fetch', '--no-tags', 'origin', `+${ref}:${retained}`], 64 * 1024);
    const commit = (await this.git(['rev-parse', `${retained}^{commit}`])).trim();
    if (!objectId.test(commit)) throw new Error('Invalid repair Git commit');
    return commit;
  }

  async baseline(row: { workflowId: string; packId: string; version: number; deployNumber: number;
    tagName: string | null; specJson: string }, purpose?: 'draft'): Promise<RepairBaseline> {
    if (!row.tagName || !/^deploy\/[A-Za-z0-9_-]+\/#\d+$/.test(row.tagName)) {
      throw new Error('Active deployment has no valid ClawFlows tag');
    }
    const commit = await this.fetch(`refs/tags/${row.tagName}`);
    const tree = (await this.git(['rev-parse', `${commit}^{tree}`])).trim();
    const manifestText = await this.git(['show', `${commit}:workflow.pack.yaml`]);
    const manifest = parseYaml(manifestText) as { id?: string; workflows?: Array<{ id?: string; file?: string }> };
    const specPath = manifest?.workflows?.find(item => item.id === row.workflowId)?.file;
    if (manifest?.id !== row.packId || !specPath || specPath.startsWith('/')
      || specPath.split('/').some(part => !part || part === '.' || part === '..')) {
      throw new Error('Active deployment tag does not contain the expected Pack workflow');
    }
    const spec = await this.git(['show', `${commit}:${specPath}`]);
    const deployedSpec = parseYaml(row.specJson) as unknown;
    const taggedSpec = parseYaml(spec) as unknown;
    const proposalSpecDigest = purpose === 'draft' ? digestRepairJson(deployedSpec) : repairProposalSpecDigest(taggedSpec, deployedSpec);
    return { workflowId: row.workflowId, packId: row.packId, releaseRevision: row.version,
      activeDeployNumber: row.deployNumber, specDigest: createHash('sha256').update(spec).digest('hex'),
      proposalSpecDigest, repoId: 'clawflows', specPath, packCommit: commit, packDigest: tree };
  }

  async result(request: RepairExecutionReadRequest): Promise<RepairGeneratedResult> {
    let absent: unknown;
    try{return await this.candidateResult(request);}catch(error){if(!(error instanceof RepairResultAbsentError))throw error;absent=error;}
    const failureIdentity={taskId:request.input.taskId,revision:request.input.revision,...request.identity,
      parentCandidateCommit:request.input.parentCandidateCommit??request.input.baseline.packCommit};
    const failureRef=repairFailureRef(failureIdentity);
    if((await this.git(['ls-remote','origin',failureRef],64*1024)).trim()){
      const commit=await this.fetch(failureRef);
      const parent=failureIdentity.parentCandidateCommit;
      const parents=(await this.git(['rev-list','--parents','-n','1',commit])).trim().split(/\s+/).slice(1);
      if(parents.length!==1||parents[0]!==parent
        ||(await this.git(['rev-parse',`${commit}^{tree}`])).trim()!==(await this.git(['rev-parse',`${parent}^{tree}`])).trim())throw new Error('Failure metadata changed the frozen Pack');
      return readRepairFailure(await this.git(['log','-1','--format=%B',commit],32*1024),failureIdentity);
    }
    throw absent;
  }
  private async candidateResult(request:RepairExecutionReadRequest):Promise<RepairGeneratedResult>{
    const branch = request.input.taskBranch;
    if (!safeBranch.test(branch)) throw new Error('Invalid repair task branch');
    const advertised = (await this.git(['ls-remote', 'origin', `refs/heads/${branch}`], 64 * 1024)).trim();
    if (!advertised && !request.input.parentCandidateCommit) throw new RepairResultAbsentError('Repair execution has not published a result');
    const head = await this.fetch(`refs/heads/${branch}`);
    const message = await this.git(['log', '-1', '--format=%B', head], 1024 * 1024);
    const prefix = 'ClawMind workflow-repair-result/v1\n';
    if (!message.startsWith(prefix)) throw new Error('Repair branch has no checked result');
    const result = JSON.parse(message.slice(prefix.length)) as Record<string, unknown>;
    const draft = result.draft as Record<string, unknown> | undefined;
    const checks = result.checks as Record<string, unknown> | undefined;
    if (request.input.parentCandidateCommit && request.input.previousReportRef
      && result.schemaVersion === 'workflow-repair-result/v1' && result.taskId === request.input.taskId
      && request.input.previousReportRef === `${result.taskId}/revisions/${result.revision}`
      && Number.isSafeInteger(result.revision) && Number(result.revision) < request.input.revision
      && ['succeeded', 'blocked', 'no_change'].includes(String(result.status))
      && draft?.candidateCommit === request.input.parentCandidateCommit && draft.repoId === 'clawflows' && draft.branch === branch
      && (await this.git(['rev-parse', `${head}^1`])).trim() === draft.candidateCommit
      && (await this.git(['rev-parse', `${head}^{tree}`])).trim() === (await this.git(['rev-parse', `${draft.candidateCommit}^{tree}`])).trim()) {
      throw new RepairResultAbsentError('Repair branch still contains the previous successful result');
    }
    if (result.schemaVersion !== 'workflow-repair-result/v1' || result.taskId !== request.input.taskId
      || result.stepId !== request.identity.stepId || result.revision !== request.input.revision
      || result.attempt !== request.identity.attempt || result.executionId !== request.identity.executionId
      || !['succeeded', 'blocked', 'no_change'].includes(String(result.status)) || !draft || !checks
      || draft.repoId !== 'clawflows' || draft.branch !== branch
      || !objectId.test(String(draft.candidateCommit)) || !objectId.test(String(draft.packDigest))) {
      throw new Error('Repair Git result does not match the AIS execution');
    }
    const candidate = String(draft.candidateCommit);
    const [parent, headTree, candidateTree] = await Promise.all([
      this.git(['rev-parse', `${head}^1`]), this.git(['rev-parse', `${head}^{tree}`]),
      this.git(['rev-parse', `${candidate}^{tree}`]),
    ]);
    if (parent.trim() !== candidate || headTree.trim() !== candidateTree.trim()
      || candidateTree.trim() !== draft.packDigest || checks.candidateCommit !== candidate
      || checks.packDigest !== draft.packDigest) {
      throw new Error('Repair result metadata does not bind the candidate Pack tree');
    }
    const candidateParent = (await this.git(['rev-parse', `${candidate}^1`])).trim();
    if (!request.input.parentCandidateCommit) {
      if (candidateParent !== request.input.baseline.packCommit) throw new Error('Repair candidate does not descend from the frozen baseline');
    } else {
      const priorCandidate = request.input.parentCandidateCommit;
      if (!priorCandidate || (await this.git(['rev-parse', `${candidateParent}^1`])).trim() !== priorCandidate
        || (await this.git(['rev-parse', `${candidateParent}^{tree}`])).trim()
          !== (await this.git(['rev-parse', `${priorCandidate}^{tree}`])).trim()) {
        throw new Error('Repair candidate does not descend from the previous result');
      }
      const priorMessage = await this.git(['log', '-1', '--format=%B', candidateParent], 1024 * 1024);
      if (!priorMessage.startsWith(prefix)) throw new Error('Previous repair result metadata is missing');
      const prior = JSON.parse(priorMessage.slice(prefix.length)) as Record<string, unknown>;
      if (prior.taskId !== request.input.taskId || !Number.isSafeInteger(prior.revision) || Number(prior.revision) >= request.input.revision
        || (request.input.previousReportRef != null && request.input.previousReportRef !== `${request.input.taskId}/revisions/${prior.revision}`)
        || (prior.draft as Record<string, unknown> | undefined)?.candidateCommit !== priorCandidate) {
        throw new Error('Previous repair result does not match this revision');
      }
    }
    return { status: result.status as 'succeeded' | 'blocked' | 'no_change', draft, checks };
  }

  async diff(input: { workflowId: string; taskId: string; revision: number; baseCommit: string; candidateCommit: string }) {
    if (!objectId.test(input.baseCommit) || !objectId.test(input.candidateCommit)) throw new Error('Invalid repair diff commits');
    // A valid diff can list up to 2 × 500 paths of up to 1,024 bytes each.
    const fields = (await this.git(['diff', '--name-status', '--no-renames', '-z', input.baseCommit, input.candidateCommit], MAX_GIT_OUTPUT))
      .split('\0').filter(Boolean);
    if (fields.length % 2 !== 0) throw new Error('Invalid repair Git diff listing');
    const truncated = fields.length / 2 > 20;
    const files = [];
    let patchBytes = 0;
    for (let index = 0; index < Math.min(fields.length, 40); index += 2) {
      const status = fields[index]; const path = fields[index + 1];
      if (!path || path.startsWith('/') || path.split('/').some(part => !part || part === '.' || part === '..')) throw new Error('Unsafe repair diff path');
      const before = status === 'A' ? null : await this.blob(input.baseCommit, path);
      const after = status === 'D' ? null : await this.blob(input.candidateCommit, path);
      const stats = await this.git(['diff', '--no-ext-diff', '--no-textconv', '--numstat', input.baseCommit, input.candidateCommit, '--', path], 4096);
      const binary = !!before?.binary || !!after?.binary || stats.startsWith('-\t-\t');
      let patch: string | undefined;
      let patchTruncated = false;
      if (!binary) {
        try {
          const { stdout } = await runFile('git', ['-C', this.gitDir, 'diff', '--no-ext-diff', '--no-textconv',
            '--no-renames', '--no-color', '--unified=3', input.baseCommit, input.candidateCommit, '--', path],
          { encoding: 'utf8', timeout: 45_000, maxBuffer: MAX_FILE_PATCH });
          const size = Buffer.byteLength(stdout, 'utf8');
          if (size + patchBytes > MAX_PATCH_TOTAL) patchTruncated = true;
          else { patch = stdout; patchBytes += size; }
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER') patchTruncated = true;
          else throw new Error('Repair Git diff failed');
        }
      }
      files.push({ path, status, before: before?.text ?? null, after: after?.text ?? null,
        patch, binary, truncated: patchTruncated });
    }
    return { baseCommit: input.baseCommit, candidateCommit: input.candidateCommit, files, truncated };
  }
}

/** A shared AIS token alone does not enable repair; repair-specific settings do. */
export async function resolveRepairAisConfiguration(aisToken?: string, rawSnapshotId?: string, rawGitDir?: string):
Promise<{ snapshotId: number; gitStore: RepairGitStore } | undefined> {
  if (rawSnapshotId === undefined && rawGitDir === undefined) return undefined;
  if (!aisToken?.trim()) throw new Error('Workflow Repair AIS token is required');
  const snapshot = rawSnapshotId?.trim();
  if (!snapshot || !/^[1-9]\d*$/.test(snapshot) || !Number.isSafeInteger(Number(snapshot))) {
    throw new Error('Workflow Repair snapshot ID must be a positive integer');
  }
  const gitDir = rawGitDir?.trim();
  if (!gitDir) throw new Error('Workflow Repair Git directory is required');
  const gitStore = new RepairGitStore(gitDir);
  await gitStore.verify();
  return { snapshotId: Number(snapshot), gitStore };
}

export function createRepairAisExecution(db: IDatabase, ais: AistudioService,
  git: RepairGitStore, snapshotId: number, publicBaseUrl: string): RepairExecutionPort {
  const base = new URL(publicBaseUrl);
  const localHttp = base.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname);
  if ((base.protocol !== 'https:' && !localHttp) || base.username || base.password
    || base.pathname !== '/' || base.search || base.hash) {
    throw new Error('Workflow Repair material service requires an HTTPS origin or local HTTP origin');
  }
  const materialBaseUrl = `${base.origin}/api/workflow-repairs/material-download`;
  return {
    integrationVersion: 'workflow-proposal-plan/v1',
    materialDeliveryVersion: 'repair-material-download/v1',
    async resolveBaseline(workflowId, purpose) {
      const row = await new WorkflowDeployHistoryRepository(db).findActiveByWorkflowId(workflowId);
      if (!row) throw new Error('Workflow has no active deployment');
      return git.baseline({ workflowId, packId: row.pack_id, version: row.version,
        deployNumber: row.deploy_number, tagName: row.tag_name, specJson: row.spec_json }, purpose);
    },
    async dispatch(request) {
      if (request.input.materials && !request.materialAccess) throw new Error('AIS executeSnapshot 失败: Frozen material access is required');
      const materialAccess = request.materialAccess ? { ...request.materialAccess, baseUrl: materialBaseUrl } : undefined;
      const action = materialAccess
        ? (request.input.integration ? 'integrate_candidate_materials_v1' : 'generate_candidate_materials_v1')
        : (request.input.integration ? 'integrate_candidate_v1' : 'generate_candidate');
      const envelope = { schemaVersion: 'clawevolve-task/v1', taskType: 'workflow_repair',
        taskId: request.input.taskId, stepId: request.identity.stepId, attempt: request.identity.attempt,
        execution: { executor: 'ais', executionId: request.identity.executionId, action },
        input: request.input, ...(materialAccess ? { materialAccess } : {}) };
      const jobId = await ais.execute(request.actorId, { '${clawevolve_params}': JSON.stringify(envelope) }, snapshotId);
      return { jobId };
    },
    async status(request) { return ais.getJobStatusDetail(request.jobId); },
    async readResult(request) { return git.result(request); },
    async stop(request) { await ais.stopExecution(request.jobId, request.actorId); },
    diff: input => git.diff(input),
  };
}

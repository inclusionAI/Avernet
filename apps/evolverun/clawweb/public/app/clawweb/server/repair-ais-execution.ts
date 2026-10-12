/** Host-owned Workflow Repair AIS and ClawFlows Git adapter. Never trust AIS
 * lifecycle status as a business result; only the checked Git result is read. */
import { createHash, randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { parse as parseYaml } from 'yaml';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { WorkflowDeployHistoryRepository } from '@avernet/workflow/server/repositories/workflow-deploy-history-repository';
import type { RepairBaseline } from '@avernet/workflow/server/contracts/repair-batch';
import { canonicalRepairJson } from '@avernet/workflow/server/contracts/repair-batch';
import type { RepairExecutionPort, RepairExecutionReadRequest, RepairGeneratedResult } from '@avernet/workflow/server/contracts/repair-workbench';
import type { AistudioService } from '@avernet/clawinsight/server/services/aistudio-service';

const runFile = promisify(execFile);
const objectId = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const safeBranch = /^repair\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+$/;
const MAX_GIT_OUTPUT = 2 * 1024 * 1024;

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
    const temporary = `refs/workflow-repair-read/${randomUUID()}`;
    try {
      await this.git(['fetch', '--no-tags', 'origin', `+${ref}:${temporary}`], 64 * 1024);
      const commit = (await this.git(['rev-parse', `${temporary}^{commit}`])).trim();
      if (!objectId.test(commit)) throw new Error('Invalid repair Git commit');
      return commit;
    } finally {
      await this.git(['update-ref', '-d', temporary]).catch(() => undefined);
    }
  }

  async baseline(row: { workflowId: string; packId: string; version: number; deployNumber: number;
    tagName: string | null; specJson: string }): Promise<RepairBaseline> {
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
    if (canonicalRepairJson(taggedSpec) !== canonicalRepairJson(deployedSpec)) {
      throw new Error('Active deployment spec differs from the tagged ClawFlows Pack');
    }
    return { workflowId: row.workflowId, packId: row.packId, releaseRevision: row.version,
      activeDeployNumber: row.deployNumber, specDigest: createHash('sha256').update(spec).digest('hex'),
      repoId: 'clawflows', specPath, packCommit: commit, packDigest: tree };
  }

  async result(request: RepairExecutionReadRequest): Promise<RepairGeneratedResult> {
    const branch = request.input.taskBranch;
    if (!safeBranch.test(branch)) throw new Error('Invalid repair task branch');
    const head = await this.fetch(`refs/heads/${branch}`);
    const message = await this.git(['log', '-1', '--format=%B', head], 1024 * 1024);
    const prefix = 'ClawMind workflow-repair-result/v1\n';
    if (!message.startsWith(prefix)) throw new Error('Repair branch has no checked result');
    const result = JSON.parse(message.slice(prefix.length)) as Record<string, unknown>;
    const draft = result.draft as Record<string, unknown> | undefined;
    const checks = result.checks as Record<string, unknown> | undefined;
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
    if (request.input.revision === 1) {
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
      if (prior.taskId !== request.input.taskId || prior.revision !== request.input.revision - 1
        || (prior.draft as Record<string, unknown> | undefined)?.candidateCommit !== priorCandidate) {
        throw new Error('Previous repair result does not match this revision');
      }
    }
    return { status: result.status as 'succeeded' | 'blocked' | 'no_change', draft, checks };
  }

  async diff(input: { workflowId: string; taskId: string; revision: number; baseCommit: string; candidateCommit: string }) {
    if (!objectId.test(input.baseCommit) || !objectId.test(input.candidateCommit)) throw new Error('Invalid repair diff commits');
    const fields = (await this.git(['diff', '--name-status', '--no-renames', '-z', input.baseCommit, input.candidateCommit], 128 * 1024))
      .split('\0').filter(Boolean);
    if (fields.length % 2 !== 0) throw new Error('Invalid repair Git diff listing');
    const truncated = fields.length / 2 > 20;
    const files = [];
    for (let index = 0; index < Math.min(fields.length, 40); index += 2) {
      const status = fields[index]; const path = fields[index + 1];
      if (!path || path.startsWith('/') || path.split('/').some(part => !part || part === '.' || part === '..')) throw new Error('Unsafe repair diff path');
      const before = status === 'A' ? null : await this.blob(input.baseCommit, path);
      const after = status === 'D' ? null : await this.blob(input.candidateCommit, path);
      files.push({ path, status, before: before?.text ?? null, after: after?.text ?? null,
        binary: !!before?.binary || !!after?.binary,
        truncated: !!before?.truncated || !!after?.truncated });
    }
    return { baseCommit: input.baseCommit, candidateCommit: input.candidateCommit, files, truncated };
  }
}

export function createRepairAisExecution(db: IDatabase, ais: AistudioService,
  git: RepairGitStore, snapshotId: number): RepairExecutionPort {
  return {
    async resolveBaseline(workflowId) {
      const row = await new WorkflowDeployHistoryRepository(db).findActiveByWorkflowId(workflowId);
      if (!row) throw new Error('Workflow has no active deployment');
      return git.baseline({ workflowId, packId: row.pack_id, version: row.version,
        deployNumber: row.deploy_number, tagName: row.tag_name, specJson: row.spec_json });
    },
    async dispatch(request) {
      const envelope = { schemaVersion: 'clawevolve-task/v1', taskType: 'workflow_repair',
        taskId: request.input.taskId, stepId: request.identity.stepId, attempt: request.identity.attempt,
        execution: { executor: 'ais', executionId: request.identity.executionId, action: 'generate_candidate' },
        input: request.input };
      const jobId = await ais.execute(request.actorId, { '${clawevolve_params}': JSON.stringify(envelope) }, snapshotId);
      return { jobId };
    },
    async status(request) { return ais.getJobStatusDetail(request.jobId); },
    async readResult(request) { return git.result(request); },
    async stop(request) { await ais.stopExecution(request.jobId); },
    diff: input => git.diff(input),
  };
}

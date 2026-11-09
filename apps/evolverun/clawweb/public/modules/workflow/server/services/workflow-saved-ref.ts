/** ClawWeb's only mutable workflow snapshot is wf/<id>. No legacy ref reads,
 * fallback, ancestry or migration; this helper never writes remote refs. */
import { createHash } from 'node:crypto';
import { open, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { initClawflowsReadRepo, type ClawFlowsGitConfig } from './clawflows-git-client.js';
import { executeGit } from '@avernet/clawweb-shared/server/services/git-execution';
import { packVersionGitEnvironment } from './pack-version-git-env.js';
import { gitOidPattern, validPackGitId, type PreparedPackMetadata } from './pack-git-prepared.js';

export const WORKFLOW_SAVED_HEADER = 'ClawMind saved candidate v1\n';
export type ResolvedSavedWorkflowRef = Readonly<{
  commit: string | null;
  /** Fully qualified refs/heads/wf/<id>, or null when absent. */
  ref: string | null;
  wfHead: string | null;
}>;

/** Preserve only operator-owned transport settings; never log env, argv or remote errors. */
export function workflowSavedGitRunner(repoDir: string) {
  const env = packVersionGitEnvironment();
  const bytes = (args: string[], maxBuffer = 16 * 1024 * 1024) => executeGit(args, { cwd: repoDir, env, maxBuffer });
  const text = async (args: string[]) => (await bytes(args)).toString('utf8').trim();
  return { bytes, text };
}

async function observeSavedWorkflowRef(repoDir: string, workflowId: string): Promise<ResolvedSavedWorkflowRef> {
  if (!validPackGitId(workflowId)) throw new Error('Invalid workflow id');
  const ref = `refs/heads/wf/${workflowId}`;
  const { text } = workflowSavedGitRunner(repoDir);
  const advertised = await text(['ls-remote', '--refs', 'origin', ref]);
  if (!advertised) return Object.freeze({ commit: null, ref: null, wfHead: null });
  const match = advertised.match(/^([a-f0-9]{40}|[a-f0-9]{64})\t([^\n]+)$/);
  if (!match || match[2] !== ref) throw new Error('Invalid workflow ref');
  const wfHead = match[1];
  return Object.freeze({ commit: wfHead, ref, wfHead });
}

async function fetchObservedWorkflowRef(repoDir: string, observed: ResolvedSavedWorkflowRef): Promise<ResolvedSavedWorkflowRef> {
  const { wfHead } = observed;
  if (wfHead === null) return observed;
  const { text } = workflowSavedGitRunner(repoDir);
  try { await text(['cat-file', '-e', `${wfHead}^{commit}`]); }
  catch { await text(['fetch', '--no-tags', 'origin', wfHead]); }
  if (await text(['rev-parse', '--verify', `${wfHead}^{commit}`]) !== wfHead) throw new Error('Invalid workflow head');
  return observed;
}

export async function resolveSavedWorkflowRef(repoDir: string, workflowId: string): Promise<ResolvedSavedWorkflowRef> {
  return fetchObservedWorkflowRef(repoDir, await observeSavedWorkflowRef(repoDir, workflowId));
}

/** ls-remote stdout is empty for an unborn remote. Read only the transport's
 * object-format capability, never a different ref/tree, to prepare its first wf.
 * The private packet transcript is bounded on read, removed before returning, and
 * never included in diagnostics. Only a complete valid advertisement can imply
 * the protocol-defined SHA-1 default when object-format is omitted.
 */
async function probeRemoteObjectFormat(repoDir: string, workflowId: string): Promise<'sha1' | 'sha256'> {
  const tracePath = path.join(repoDir, '.git', 'workflow-object-format.packet');
  const trace = await open(tracePath, 'wx+', 0o600);
  try {
    await executeGit(['-c', 'protocol.version=2', 'ls-remote', '--refs', 'origin', `refs/heads/wf/${workflowId}`], {
      cwd: repoDir, env: { ...packVersionGitEnvironment(), GIT_TRACE_PACKET: tracePath }, maxBuffer: 1024 * 1024,
    });
    const bytes = Buffer.alloc(64 * 1024);
    const { bytesRead } = await trace.read(bytes, 0, bytes.length, 0);
    const lines = bytes.subarray(0, bytesRead).toString('utf8').split('\n');
    // Local/SSH use ls-remote; the smart HTTP transport uses git. Only inbound
    // capability advertisements count, not the client's outbound format choice.
    for (const identity of ['ls-remote', 'git']) {
      const packets = lines.flatMap(line => {
        const match = line.match(/\bpacket:\s+(ls-remote|git)< (.*)$/);
        return match?.[1] === identity ? [match[2]] : [];
      });
      const start = packets.indexOf('version 2'), end = packets.indexOf('0000', start + 1);
      let capabilities: string[], advertisedOidLength: number | undefined;
      if (start >= 0) {
        if (end <= start) continue;
        // HTTP may trace the version banner twice before the capability block.
        capabilities = packets.slice(packets.lastIndexOf('version 2', end - 1) + 1, end);
      } else {
        // v0/v1 put capabilities after NUL on the first advertisement. Require
        // its terminating flush as well, including for an unborn repository.
        const first = packets[0] === 'version 1' ? 1 : 0;
        const legacy = packets[first]?.match(/^([a-f0-9]{40}|[a-f0-9]{64}) [^\\]*\\0(.*)$/);
        if (!legacy || packets.indexOf('0000', first + 1) < 0) continue;
        advertisedOidLength = legacy[1].length;
        capabilities = legacy[2] ? legacy[2].split(' ') : [];
      }
      if (capabilities.some(value => !/^[a-zA-Z0-9_-]+(?:=[^\0\r\n]*)?$/.test(value))) throw new Error('Invalid capability block');
      const formats = capabilities.filter(value => value === 'object-format' || value.startsWith('object-format='));
      // Both Git protocol generations define omission as SHA-1, but only after
      // a real complete advertisement; empty/damaged traces cannot select it.
      const format = formats.length === 0 ? 'sha1' : formats.length === 1 && formats[0] === 'object-format=sha1' ? 'sha1'
        : formats.length === 1 && formats[0] === 'object-format=sha256' ? 'sha256' : null;
      if (!format || (advertisedOidLength !== undefined && advertisedOidLength !== (format === 'sha1' ? 40 : 64))) {
        throw new Error('Invalid object format');
      }
      return format;
    }
    throw new Error('Unconfirmed object format');
  } catch {
    throw new Error('Cannot determine remote Git object format');
  } finally {
    await trace.close();
    await rm(tracePath, { force: true });
  }
}

/** For a newly-created, empty save directory only. ls-remote is format-agnostic;
 * observe wf before fetching, and rebuild only our still-empty Git metadata when
 * its OID or (when wf is absent) negotiated capability requires SHA-256. Never
 * reinitialize a caller's existing repository or refresh the observed lease.
 */
export async function initSavedWorkflowRepo(repoDir: string, workflowId: string, config: ClawFlowsGitConfig): Promise<ResolvedSavedWorkflowRef> {
  if (!validPackGitId(workflowId) || (await readdir(repoDir)).length !== 0) throw new Error('Workflow save requires an empty temporary directory');
  await initClawflowsReadRepo(repoDir, config);
  const observed = await observeSavedWorkflowRef(repoDir, workflowId);
  const format = observed.wfHead === null ? await probeRemoteObjectFormat(repoDir, workflowId)
    : observed.wfHead.length === 64 ? 'sha256' : 'sha1';
  if (format === 'sha256') {
    await rm(path.join(repoDir, '.git'), { recursive: true });
    await initClawflowsReadRepo(repoDir, config, 'sha256');
  }
  return fetchObservedWorkflowRef(repoDir, observed);
}

/** Metadata is truthful only for root Packs; never disguise a nested tree. */
export async function workflowSavedCommitMessage(repoDir: string, workflowId: string, tree: string,
  pack: Pick<PreparedPackMetadata, 'packId' | 'packPath' | 'workflowFile'>): Promise<string> {
  if (pack.packPath) return `Save workflow ${workflowId}`;
  if (!validPackGitId(workflowId) || !gitOidPattern.test(tree)) throw new Error('Invalid saved metadata');
  const { bytes } = workflowSavedGitRunner(repoDir);
  const raw = await bytes(['show', `${tree}:${pack.workflowFile}`], 1024 * 1024 + 1);
  return WORKFLOW_SAVED_HEADER + JSON.stringify({ workflowId, packId: pack.packId, specPath: pack.workflowFile,
    specDigest: createHash('sha256').update(raw).digest('hex'), packDigest: tree,
    baseDeployNumber: null, createdAt: new Date().toISOString() });
}

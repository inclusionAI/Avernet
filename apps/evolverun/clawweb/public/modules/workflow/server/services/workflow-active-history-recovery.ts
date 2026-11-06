/**
 * Read-only recovery candidate, not an authorization or persistence service.
 *
 * Trust boundary: the caller must authorize the actor, query exactly one active
 * history row for the requested workflow from the server DB, and provide the
 * expected active token. The injected host reader is trusted server code, NOT a
 * client-supplied receipt. It must independently establish that its immutable
 * release/tag objects are authorized and correspond to that exact active row
 * and token, and return the exact workflow YAML from those objects. Merely
 * resolving a tag name, matching a row hash, or reading arbitrary Git objects
 * does not establish this association. No production implementation is supplied.
 *
 * A successful result is only a candidate observed at this instant. Before any
 * future write, the caller must reauthorize and recheck unique active row, token,
 * absent draft and all other guards atomically with the write. This function does
 * not read or write the DB, inspect a remote, or own resources to clean up. The
 * host reader owns cleanup on success and failure and must not expose secrets.
 */
import { editorActiveDeploymentToken, type EditorActiveDeploymentTokenRow } from './editor-active-deployment-token.js';
import { gitOidPattern, validPackGitId } from './pack-git-prepared.js';
import { projectWorkflowSnapshotYaml } from './workflow-snapshot-projection.js';

export type ActiveHistoryRow = EditorActiveDeploymentTokenRow & Readonly<{
  workflow_id: string;
  is_active: number;
}>;

/** No caller-selected repo, remote, path, or ref is passed to the reader. */
export type ActiveHistoryIdentity = Readonly<{
  workflowId: string;
  packId: string;
  deployNumber: string;
  version: string;
  tagName: string;
  activeToken: string;
}>;

/** This is an attestation by a trusted host implementation, not a proof supplied by a client. */
export type AuthorizedReleaseSnapshot = ActiveHistoryIdentity & Readonly<{
  source: 'authorized-immutable-release';
  tagObject: string;
  releaseCommit: string;
  yaml: string;
}>;
export type AuthorizedActiveHistoryReader = (identity: ActiveHistoryIdentity) => Promise<AuthorizedReleaseSnapshot | null>;

export type ActiveHistoryRecoveryResult =
  | Readonly<{ kind: 'recovered'; activeToken: string; yaml: string; specJson: string }>
  | Readonly<{ kind: 'unavailable' }>;

const unavailable: ActiveHistoryRecoveryResult = Object.freeze({ kind: 'unavailable' });
const positiveInteger = (value: number | string): string | null => {
  if (typeof value === 'number') return Number.isSafeInteger(value) && value > 0 ? String(value) : null;
  return typeof value === 'string' && /^[1-9][0-9]{0,19}$/.test(value) ? value : null;
};

/** Fails closed with a uniform result and never returns reader errors or metadata. */
export async function recoverActiveWorkflowHistory(input: Readonly<{
  workflowId: string;
  activeRow: ActiveHistoryRow;
  expectedActiveToken: string;
}>, readAuthorizedRelease: AuthorizedActiveHistoryReader): Promise<ActiveHistoryRecoveryResult> {
  try {
    if (!input || !input.activeRow || typeof readAuthorizedRelease !== 'function') return unavailable;
    // Snapshot caller-owned values before awaiting untrusted I/O.
    const workflowId = input.workflowId;
    const row = { ...input.activeRow };
    const expected = input.expectedActiveToken;
    const deployNumber = positiveInteger(row.deploy_number);
    const version = positiveInteger(row.version);
    if (!validPackGitId(workflowId) || /^__.+__$/.test(workflowId) || row.workflow_id !== workflowId
      || row.is_active !== 1 || row.action !== 'deploy' || !validPackGitId(row.pack_id)
      || !deployNumber || !version || row.tag_name !== `deploy/${workflowId}/#${deployNumber}`
      || typeof expected !== 'string' || !/^[a-f0-9]{64}$/.test(expected)
      || editorActiveDeploymentToken(workflowId, row) !== expected) return unavailable;
    const identity: ActiveHistoryIdentity = Object.freeze({ workflowId, packId: row.pack_id,
      deployNumber, version, tagName: row.tag_name, activeToken: expected });
    const release = await readAuthorizedRelease(identity);
    if (!release || release.source !== 'authorized-immutable-release'
      || release.workflowId !== identity.workflowId || release.packId !== identity.packId
      || release.deployNumber !== identity.deployNumber || release.version !== identity.version
      || release.tagName !== identity.tagName || release.activeToken !== identity.activeToken
      || typeof release.tagObject !== 'string' || !gitOidPattern.test(release.tagObject)
      || typeof release.releaseCommit !== 'string' || !gitOidPattern.test(release.releaseCommit)
      || typeof release.yaml !== 'string' || release.yaml.includes('\uFFFD')) return unavailable;
    // Project the *whole* trusted YAML document. Never infer an envelope from
    // a `content` property, even if it resembles another workflow document.
    const yaml = release.yaml;
    const projection = projectWorkflowSnapshotYaml(workflowId, yaml);
    return { kind: 'recovered', activeToken: expected, yaml, specJson: projection.specJson };
  } catch {
    return unavailable;
  }
}

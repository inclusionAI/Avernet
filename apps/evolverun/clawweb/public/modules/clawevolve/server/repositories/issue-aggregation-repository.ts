import { WorkflowSnapshotRepository } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import { workflowSnapshotKey, type WorkflowSnapshotBinding } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import { analysisWorkflowSnapshot } from './analysis-workflow-snapshot.js';
import { issuePriority, compareIssuePriority, type IssueSort, type IssuePriority } from '../services/evolution/issue-priority.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { canonicalJson, digestCanonicalJson, validateWorkflowEvolutionAnalysisResult } from '../services/evolution/contracts.js';
import { buildAggregationModelInput, buildIssueGroups, ISSUE_AGGREGATION_INPUT_V1, ISSUE_AGGREGATION_INPUT_V2, validateIssueSummary,
  type IssueAggregationInputVersion, type IssueAggregationModelInput, type IssueAnalysis, type IssueGroup, type IssueSummary } from '../services/evolution/issue-aggregation.js';
import type { WorkflowEvolutionAnalysisRow } from './workflow-evolution-repository.js';

const SCOPE = 'issue_aggregate';
const issueProjections = new WeakMap<IDatabase, Map<string, { version: string; at: number; groups: IssueGroup[] }>>();
const issueProjectionBuilds = new WeakMap<IDatabase, Map<string, Promise<IssueGroup[]>>>();
type AggregationInputSummary = IssueAggregationModelInput['inputSummary'];
type Snapshot = { parentAnalysisId: string; input: IssueGroup; inputVersion?: IssueAggregationInputVersion; inputSummary?: AggregationInputSummary };
type SnapshotIndexRow = Pick<WorkflowEvolutionAnalysisRow, 'analysis_id' | 'status' | 'requested_at_ms'> & {
  // SQLite returns JSON text; mysql2 can return an already-decoded JSON array.
  input_key: string | [string, string];
};
type SnapshotBodyRow = Pick<WorkflowEvolutionAnalysisRow, 'analysis_id' | 'scope_json' | 'result_json'>;
export type PresentedIssueGroup = IssueGroup & { summary: IssueSummary | null; summarySources: IssueGroup['sources']; stale: boolean; aggregationStatus: string; aggregationId: string | null;
  priority?: IssuePriority; aggregationInputVersion: IssueAggregationInputVersion | null; aggregationInputSummary: AggregationInputSummary | null };

function aggregationPayload(group: IssueGroup, inputVersion: IssueAggregationInputVersion): {
  input: IssueGroup | IssueAggregationModelInput; inputSummary: AggregationInputSummary;
} {
  if (inputVersion === ISSUE_AGGREGATION_INPUT_V2) {
    const input = buildAggregationModelInput(group);
    return { input, inputSummary: input.inputSummary };
  }
  return { input: group, inputSummary: { totalSources: group.sources.length, fullSources: group.sources.length, compactSources: 0 } };
}

/** Aggregations are immutable versioned analysis snapshots, not diagnoses or suggestion states. */
export class IssueAggregationRepository {
  constructor(private readonly db: IDatabase) {}

  /** Read-only source API for Workflow repair; does not depend on aggregate model output. */
  listSources(workflowId: string, options?: { sinceMs?: number }): Promise<IssueGroup[]> {
    return this.groups(workflowId, { ...options, tolerateInvalid: true });
  }

  private async groups(workflowId: string, options: { sinceMs?: number; tolerateInvalid?: boolean; versionScope?: 'current'|'historical'|'all' } = {}): Promise<IssueGroup[]> {
    const analyses: IssueAnalysis[] = [];
    let afterId = 0;
    for (;;) {
      const rows = await this.db.query<WorkflowEvolutionAnalysisRow>(
        `SELECT id, analysis_id, flow_id, scope_json, result_json, completed_at_ms, requested_at_ms
         FROM workflow_evolution_analysis_runs WHERE workflow_id = ? AND status = 'completed'
         AND scope_type <> ?${options.sinceMs === undefined ? '' : ' AND COALESCE(completed_at_ms, requested_at_ms) >= ?'}
         AND id > ? ORDER BY id ASC LIMIT 1000`, options.sinceMs === undefined
          ? [workflowId, SCOPE, afterId] : [workflowId, SCOPE, options.sinceMs, afterId]);
      if (!rows.length) break;
      for (const row of rows) {
        try {
          const result = validateWorkflowEvolutionAnalysisResult(JSON.parse(row.result_json ?? 'null'));
          const scope = JSON.parse(row.scope_json) as { flowIds?: unknown };
          analyses.push({ analysisId: row.analysis_id, flowId: row.flow_id, flowIds: Array.isArray(scope.flowIds) ? scope.flowIds.map(String) : [], completedAtMs: row.completed_at_ms ?? row.requested_at_ms, diagnoses: result.diagnoses, analysisContext: { facts: result.facts, inferences: result.inferences, unknowns: result.unknowns, workflowSnapshot: analysisWorkflowSnapshot(row), evidenceStatus: 'evidenceStatus' in result ? result.evidenceStatus : null } });
        } catch (error) {
          if (!options.tolerateInvalid) throw error;
          console.warn('[workflow-repair] skipped incompatible analysis source', { workflowId, analysisId: row.analysis_id });
        }
      }
      afterId = rows.at(-1)!.id;
    }
    // Analyses can cover several runs. Remove trial identities from every evidence location.
    const trialRows = await this.db.query<{ record_id: string }>(
      "SELECT record_id FROM workflow_repair_release_records WHERE record_kind IN ('trial', 'trial_run') AND workflow_id = ?", [workflowId]);
    const trialIds = new Set(trialRows.map(row => row.record_id));
    for (const analysis of analyses) {
      if (analysis.flowId && trialIds.has(analysis.flowId)) analysis.flowId = null;
      analysis.flowIds = analysis.flowIds?.filter(id => !trialIds.has(id));
      analysis.diagnoses = analysis.diagnoses.map(d => ({ ...d, flowIds: d.flowIds.filter(id => !trialIds.has(id)) }))
        .filter(d => d.flowIds.length > 0);
    }
    const coveredRuns = new Set(analyses.flatMap(a => [...(a.flowIds ?? []), ...(a.flowId ? [a.flowId] : []), ...a.diagnoses.flatMap(d => d.flowIds)]));
    const legacy = await this.db.query<{
      diagnosis_id: string; flow_id: string; failure_signature: string; failure_mode: string; node_id: string | null;
      weak_node_id: string | null; error_text: string | null; gmt_modified: string | number;
    }>('SELECT diagnosis_id, flow_id, failure_signature, failure_mode, node_id, weak_node_id, error_text, gmt_modified FROM workflow_healing_diagnoses WHERE workflow_id = ?', [workflowId]);
    const legacyByRun = new Map<string, IssueAnalysis>();
    for (const row of legacy) {
      if (trialIds.has(row.flow_id) || coveredRuns.has(row.flow_id)) continue;
      const numeric = Number(row.gmt_modified);
      const time = Number.isFinite(numeric) ? (numeric < 1e12 ? numeric * 1000 : numeric) : Date.parse(String(row.gmt_modified)) || 0;
      if (options.sinceMs !== undefined && time < options.sinceMs) continue;
      const entry = legacyByRun.get(row.flow_id) ?? { analysisId: 'legacy', flowId: row.flow_id, completedAtMs: time, diagnoses: [] };
      entry.completedAtMs = Math.max(entry.completedAtMs, time);
      entry.diagnoses.push({ diagnosisId: row.diagnosis_id, flowIds: [row.flow_id], nodeId: row.weak_node_id ?? row.node_id,
        failureSignature: row.failure_signature, failureMode: row.failure_mode, severity: null,
        reasoning: row.error_text || row.failure_signature, evidenceEventIds: [] });
      legacyByRun.set(row.flow_id, entry);
    }
    const all=[...analyses,...legacyByRun.values()];
    let runRefs: Awaited<ReturnType<WorkflowSnapshotRepository['forRuns']>> | undefined;
    const sourceRef = (context: IssueAnalysis['analysisContext'], flowId: string): WorkflowSnapshotBinding => {
      const frozen = context?.workflowSnapshot as WorkflowSnapshotBinding | undefined;
      const ref = frozen?.status === 'resolved' || frozen?.status === 'unresolved' && frozen.reason === 'IDENTITY_MISMATCH'
        ? frozen : runRefs?.get(flowId) ?? { status: 'unresolved', reason: 'LEGACY_MISSING' };
      return ref.status === 'resolved' && ref.snapshot.workflowId !== workflowId ? {status:'unresolved',reason:'IDENTITY_MISMATCH'} : ref;
    };
    if(options.versionScope&&options.versionScope!=='all'){
      const snapshots=new WorkflowSnapshotRepository(this.db),active=await snapshots.active(workflowId);
      const activeKey=active?.status==='resolved'?workflowSnapshotKey(active.snapshot):null;
      const ids=[...new Set(all.flatMap(a=>[...(a.flowIds??[]),...(a.flowId?[a.flowId]:[]),...a.diagnoses.flatMap(d=>d.flowIds)]))];
      const refs = await snapshots.forRuns(ids);
      runRefs = refs;
      for(const a of all){
        const keep=(id:string)=>{
          const ref=sourceRef(a.analysisContext,id),current=!!activeKey&&ref.status==='resolved'&&workflowSnapshotKey(ref.snapshot)===activeKey;
          return options.versionScope==='current'?current:!current;
        };
        a.flowId=a.flowId&&keep(a.flowId)?a.flowId:null;a.flowIds=a.flowIds?.filter(keep);a.diagnoses=a.diagnoses.map(d=>({...d,flowIds:d.flowIds.filter(keep)})).filter(d=>d.flowIds.length>0);
      }
    }
    return buildIssueGroups(workflowId, all).map(group => {
      if (!runRefs) return group;
      const sources = group.sources.map(source => ({ ...source, analysisContext: {
        facts: [], inferences: [], unknowns: [], evidenceStatus: null, ...source.analysisContext,
        workflowSnapshot: sourceRef(source.analysisContext, source.flowId),
      } }));
      return { ...group, sources, inputDigest: digestCanonicalJson({ workflowId, signature: group.signature, sources }) };
    });
  }

  async list(workflowId: string, inputVersion: IssueAggregationInputVersion = ISSUE_AGGREGATION_INPUT_V1, signature?: string, versionScope?: 'current'|'historical'|'all'): Promise<PresentedIssueGroup[]> {
    const allGroups = versionScope ? await this.readProjection(workflowId,versionScope) : signature ? await this.readProjection(workflowId) : await this.groups(workflowId);
    return this.present(workflowId, signature ? allGroups.filter(group => group.signature === signature) : allGroups, inputVersion,!!versionScope&&versionScope!=='all');
  }

  /** Page the issue projection before loading frozen summaries, never page its suggestions. */
  async listPage(workflowId: string, query: { page: number; pageSize: number; nodeId?: string; failureMode?: string; sort?: IssueSort; severity?: string; versionScope?: 'current'|'historical'|'all' }) {
    const all = await this.readProjection(workflowId,query.versionScope);
    const runTimes = await this.issueRunTimes(workflowId, [...new Set(all.flatMap(group => group.flowIds))]);
    const now = Date.now();
    const priorities = new Map(all.map(group => [group.signature, issuePriority(group, runTimes, now)]));
    const identity = (group: IssueGroup) => [...group.sources].sort((a, b) =>
      b.completedAtMs - a.completedAtMs || a.sourceId.localeCompare(b.sourceId))[0];
    const facets = {
      nodes: [...new Set(all.map(group => identity(group)?.nodeId ?? '未知节点'))].sort(),
      modes: [...new Set(all.map(group => identity(group)?.failureMode ?? '').filter(Boolean))].sort(),
    };
    const filtered = all.filter(group => {
      const source = identity(group);
      return (!query.nodeId || (source?.nodeId ?? '未知节点') === query.nodeId)
        && (!query.failureMode || source?.failureMode === query.failureMode)
        && (!query.severity || (priorities.get(group.signature)!.severity ?? 'unknown') === query.severity);
    });
    filtered.sort((a, b) => compareIssuePriority(priorities.get(a.signature)!, priorities.get(b.signature)!, query.sort ?? 'priority') || a.signature.localeCompare(b.signature));
    const totalPages = Math.max(1, Math.ceil(filtered.length / query.pageSize));
    const page = Math.min(query.page, totalPages);
    const groups = await this.present(workflowId, filtered.slice((page - 1) * query.pageSize, page * query.pageSize), ISSUE_AGGREGATION_INPUT_V2,!!query.versionScope&&query.versionScope!=='all');
    return { groups: groups.map(group => ({ ...group, priority: priorities.get(group.signature)! })), facets, page: { page, pageSize: query.pageSize, total: filtered.length, totalPages } };
  }

  /** Only observed issue runs supply timestamps; unrelated executions are never read. */
  private async issueRunTimes(workflowId: string, flowIds: string[]): Promise<Map<string, number>> {
    const times = new Map<string, number>();
    for (let start = 0; start < flowIds.length; start += 1000) {
      const ids = flowIds.slice(start, start + 1000);
      const rows = await this.db.query<{ flow_id: string; started_at: number }>(
        `SELECT flow_id, started_at FROM flow_runs WHERE workflow_id = ? AND flow_id IN (${ids.map(() => '?').join(',')})`, [workflowId, ...ids]);
      for (const row of rows) {
        const value = Number(row.started_at);
        if (Number.isFinite(value) && value > 0) times.set(row.flow_id, value < 1e12 ? value * 1000 : value);
      }
    }
    return times;
  }

  private async readProjection(workflowId: string, versionScope?: 'current'|'historical'|'all'): Promise<IssueGroup[]> {
    const key = JSON.stringify([workflowId,versionScope ?? 'all']);
    const version = async () => JSON.stringify(await Promise.all([
      this.db.query(`SELECT COUNT(*) AS count, MAX(id) AS latest, SUM(state_version) AS revision
        FROM workflow_evolution_analysis_runs WHERE workflow_id = ?`, [workflowId]),
      this.db.query(`SELECT COUNT(*) AS count, MAX(gmt_modified) AS modified
        FROM workflow_healing_diagnoses WHERE workflow_id = ?`, [workflowId]),
      ...(versionScope ? [this.db.query('SELECT version, deploy_number, is_active FROM workflow_deploy_history WHERE workflow_id = ? ORDER BY deploy_number', [workflowId])] : []),
    ]));
    let cache = issueProjections.get(this.db);
    if (!cache) { cache = new Map(); issueProjections.set(this.db, cache); }
    const before = await version();
    const cached = cache.get(key);
    if (cached?.version === before && Date.now() - cached.at < 10_000) return cached.groups;
    cache.delete(key);
    let builds = issueProjectionBuilds.get(this.db);
    if (!builds) { builds = new Map(); issueProjectionBuilds.set(this.db, builds); }
    const buildKey = key + before;
    let pending = builds.get(buildKey);
    if (!pending) {
      pending = this.groups(workflowId, { tolerateInvalid: true, versionScope });
      builds.set(buildKey,pending);
      void pending.finally(() => builds!.delete(buildKey)).catch(() => undefined);
    }
    const groups = await pending;
    // Read-only acceleration; preparation always rebuilds its authoritative inputs.
    // Do not pin a mixed snapshot if evidence changed while constructing it.
    if (before === await version() && Buffer.byteLength(JSON.stringify(groups), 'utf8') <= 8 * 1024 * 1024) {
      if (cache.size >= 4) cache.delete(cache.keys().next().value!);
      cache.set(key, { version: before, at: Date.now(), groups });
    }
    return groups;
  }

  private async present(workflowId: string, groups: IssueGroup[], inputVersion: IssueAggregationInputVersion, restrictToSources = false): Promise<PresentedIssueGroup[]> {
    if (!groups.length) return [];
    // Do not transfer every historical frozen input/result just to locate the displayed revision.
    // Multi-path JSON_EXTRACT works in SQLite and MySQL/OceanBase without dialect-specific unquoting.
    const rows = await this.db.query<SnapshotIndexRow>(
      `SELECT analysis_id, status, requested_at_ms,
         JSON_EXTRACT(scope_json, '$.input.signature', '$.input.inputDigest') AS input_key
       FROM workflow_evolution_analysis_runs WHERE workflow_id = ? AND scope_type = ? ORDER BY id DESC`, [workflowId, SCOPE]);
    const selected = new Map(groups.map(group => [group.signature, {
      digest: group.inputDigest, current: undefined as SnapshotIndexRow | undefined, completed: undefined as SnapshotIndexRow | undefined,
    }]));
    for (const row of rows) {
      const key: unknown = typeof row.input_key === 'string' ? JSON.parse(row.input_key) : row.input_key;
      if (!Array.isArray(key) || key.length !== 2 || key.some(value => typeof value !== 'string')) throw new Error('invalid aggregation snapshot key');
      const entry = selected.get(key[0]);
      if (!entry) continue;
      if (!entry.current && key[1] === entry.digest) entry.current = row;
      if (!entry.completed && row.status === 'completed') entry.completed = row;
    }
    const ids = [...new Set([...selected.values()].flatMap(entry => {
      if (entry.current?.status === 'completed') entry.completed = entry.current;
      const displayed = entry.completed ?? entry.current;
      return displayed ? [displayed.analysis_id] : [];
    }))];
    const bodies = new Map<string, { snapshot: Snapshot; result: string | null }>();
    // Bound bind parameters without imposing a limit on the number of groups/history entries.
    for (let offset = 0; offset < ids.length; offset += 100) {
      const batch = ids.slice(offset, offset + 100);
      const loaded = await this.db.query<SnapshotBodyRow>(
        `SELECT analysis_id, scope_json, result_json FROM workflow_evolution_analysis_runs
         WHERE workflow_id = ? AND scope_type = ? AND analysis_id IN (${batch.map(() => '?').join(',')})`, [workflowId, SCOPE, ...batch]);
      for (const row of loaded) bodies.set(row.analysis_id, { snapshot: JSON.parse(row.scope_json) as Snapshot, result: row.result_json });
    }
    return groups.map(group => {
      const { current, completed } = selected.get(group.signature)!;
      const displayed = completed ?? current;
      const body = displayed ? bodies.get(displayed.analysis_id) : undefined;
      if (displayed && !body) throw new Error('aggregation snapshot unavailable');
      const candidateFrozen = completed ? body!.snapshot.input : null;
      const frozen=!restrictToSources||candidateFrozen?.sources.every(source=>group.flowIds.includes(source.flowId))?candidateFrozen:null;
      const currentStatus = current?.status === 'queued' && Date.now() - current.requested_at_ms > 600_000 ? 'failed' : current?.status;
      const settled = currentStatus === 'completed' || currentStatus === 'queued';
      const tooLarge = !settled && (group.sources.length > 500
        || Buffer.byteLength(canonicalJson(aggregationPayload(group, inputVersion).input), 'utf8') > 180_000);
      return { ...group, summary: completed && frozen ? validateIssueSummary(JSON.parse(body!.result!), frozen) : null,
        summarySources: frozen?.sources ?? [],
        stale: !!completed && frozen?.inputDigest !== group.inputDigest,
        aggregationStatus: currentStatus === 'completed' || currentStatus === 'queued' ? currentStatus
          : tooLarge ? 'too_large' : currentStatus ?? 'not_generated', aggregationId: current?.analysis_id ?? null,
        aggregationInputVersion: body?.snapshot.inputVersion ?? null,
        aggregationInputSummary: body?.snapshot.inputSummary ?? null,
      };
    });
  }

  async prepare(parentAnalysisId: string, inputVersion: IssueAggregationInputVersion = ISSUE_AGGREGATION_INPUT_V1): Promise<Array<{
    id: string; inputVersion: IssueAggregationInputVersion; input: IssueGroup | IssueAggregationModelInput;
  }>> {
    const parent = (await this.db.query<WorkflowEvolutionAnalysisRow>(
      'SELECT * FROM workflow_evolution_analysis_runs WHERE analysis_id = ?', [parentAnalysisId]))[0];
    if (!parent || parent.status !== 'completed' || parent.scope_type === SCOPE || !parent.workflow_id) throw new Error('completed run analysis required');
    await this.db.exec(`UPDATE workflow_evolution_analysis_runs SET status = 'failed', error_code = 'aggregation_timeout', state_version = state_version + 1
      WHERE workflow_id = ? AND scope_type = ? AND status = 'queued' AND requested_at_ms < ?`, [parent.workflow_id, SCOPE, Date.now() - 600_000]);
    const source = analysisWorkflowSnapshot(parent);
    let versionScope: 'current' | undefined;
    if (source?.status === 'resolved') {
      const active = await new WorkflowSnapshotRepository(this.db).active(parent.workflow_id);
      // Completion of a historical/test analysis must not rebuild old model summaries.
      if (active?.status !== 'resolved' || workflowSnapshotKey(active.snapshot) !== workflowSnapshotKey(source.snapshot)) return [];
      versionScope = 'current';
    }
    const fresh = await this.groups(parent.workflow_id, { versionScope, tolerateInvalid: !!versionScope });
    const groups = await this.present(parent.workflow_id, fresh, inputVersion, !!versionScope);
    const parentResult = validateWorkflowEvolutionAnalysisResult(JSON.parse(parent.result_json!));
    const parentScope = JSON.parse(parent.scope_json) as { flowIds?: unknown };
    const declaredFlows = Array.isArray(parentScope.flowIds) ? parentScope.flowIds.map(String) : [];
    const affectedFlows = new Set([...declaredFlows, ...(parent.flow_id ? [parent.flow_id] : []), ...parentResult.diagnoses.flatMap(d => d.flowIds)]);
    const affectedSignatures = new Set(parentResult.diagnoses.map(d => d.failureSignature));
    const jobs: Array<{ id: string; inputVersion: IssueAggregationInputVersion; input: IssueGroup | IssueAggregationModelInput }> = [];
    for (const { summary: _summary, summarySources: _sources, stale: _stale, aggregationStatus, aggregationId: previousId,
      aggregationInputVersion: _inputVersion, aggregationInputSummary: _inputSummary, ...input } of groups) {
      if (!affectedSignatures.has(input.signature) && !_sources.some(source => affectedFlows.has(source.flowId))) continue;
      if (aggregationStatus === 'completed' || aggregationStatus === 'queued' || aggregationStatus === 'too_large') continue;
      const { input: modelInput, inputSummary } = aggregationPayload(input, inputVersion);
      const requestKey = digestCanonicalJson([SCOPE, input.inputDigest, previousId]);
      const id = `AG-${requestKey.slice(0, 40)}`;
      const now = Date.now();
      try {
        await this.db.exec(`INSERT INTO workflow_evolution_analysis_runs
          (analysis_id, request_key, scope_type, scope_json, workflow_id, status, analysis_version,
           requested_by, requested_at_ms, state_version, gmt_create, gmt_modified)
          VALUES (?, ?, ?, ?, ?, 'queued', 'workflow-issue-summary/v1', ?, ?, 0, ?, ?)`,
        [id, requestKey, SCOPE, canonicalJson({ parentAnalysisId, input, inputVersion, inputSummary }), parent.workflow_id, parent.requested_by, now, this.db.dialect.now(), this.db.dialect.now()]);
        jobs.push({ id, inputVersion, input: modelInput });
      } catch (error) {
        const existing = (await this.db.query<{ analysis_id: string }>('SELECT analysis_id FROM workflow_evolution_analysis_runs WHERE request_key = ?', [requestKey]))[0];
        if (!existing) throw error;
      }
    }
    return jobs;
  }

  async complete(parentAnalysisId: string, id: string, raw: unknown, failed = false): Promise<void> {
    const row = (await this.db.query<WorkflowEvolutionAnalysisRow>(
      'SELECT * FROM workflow_evolution_analysis_runs WHERE analysis_id = ? AND scope_type = ?', [id, SCOPE]))[0];
    if (!row) throw new Error('aggregation not found');
    const snapshot = JSON.parse(row.scope_json) as Snapshot;
    if (snapshot.parentAnalysisId !== parentAnalysisId) throw new Error('aggregation parent mismatch');
    const result = failed ? null : validateIssueSummary(raw, snapshot.input);
    if (row.status === 'completed' && result && row.result_digest === digestCanonicalJson(result)) return;
    if (row.status !== 'queued') throw new Error('aggregation state conflict');
    const updated = await this.db.exec(`UPDATE workflow_evolution_analysis_runs SET status = ?, result_json = ?, result_digest = ?,
      completed_at_ms = ?, error_code = ?, state_version = state_version + 1 WHERE analysis_id = ? AND status = 'queued' AND state_version = ?`,
    [failed ? 'failed' : 'completed', result ? canonicalJson(result) : null, result ? digestCanonicalJson(result) : null,
      Date.now(), failed ? 'aggregation_failed' : null, id, row.state_version]);
    if (!updated.affectedRows) throw new Error('aggregation state conflict');
  }
}

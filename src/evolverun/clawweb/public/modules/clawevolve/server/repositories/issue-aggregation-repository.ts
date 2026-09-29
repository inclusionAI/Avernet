import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { canonicalJson, digestCanonicalJson, validateWorkflowEvolutionAnalysisResult } from '../services/evolution/contracts.js';
import { buildAggregationModelInput, buildIssueGroups, ISSUE_AGGREGATION_INPUT_V1, ISSUE_AGGREGATION_INPUT_V2, validateIssueSummary,
  type IssueAggregationInputVersion, type IssueAggregationModelInput, type IssueAnalysis, type IssueGroup, type IssueSummary } from '../services/evolution/issue-aggregation.js';
import type { WorkflowEvolutionAnalysisRow } from './workflow-evolution-repository.js';

const SCOPE = 'issue_aggregate';
type AggregationInputSummary = IssueAggregationModelInput['inputSummary'];
type Snapshot = { parentAnalysisId: string; input: IssueGroup; inputVersion?: IssueAggregationInputVersion; inputSummary?: AggregationInputSummary };
export type PresentedIssueGroup = IssueGroup & { summary: IssueSummary | null; summarySources: IssueGroup['sources']; stale: boolean; aggregationStatus: string; aggregationId: string | null;
  aggregationInputVersion: IssueAggregationInputVersion | null; aggregationInputSummary: AggregationInputSummary | null };

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

  private async groups(workflowId: string, options: { sinceMs?: number; tolerateInvalid?: boolean } = {}): Promise<IssueGroup[]> {
    const analyses: IssueAnalysis[] = [];
    let afterId = 0;
    for (;;) {
      const rows = await this.db.query<WorkflowEvolutionAnalysisRow>(
        `SELECT * FROM workflow_evolution_analysis_runs WHERE workflow_id = ? AND status = 'completed'
         AND scope_type <> ?${options.sinceMs === undefined ? '' : ' AND COALESCE(completed_at_ms, requested_at_ms) >= ?'}
         AND id > ? ORDER BY id ASC LIMIT 200`, options.sinceMs === undefined
          ? [workflowId, SCOPE, afterId] : [workflowId, SCOPE, options.sinceMs, afterId]);
      if (!rows.length) break;
      for (const row of rows) {
        try {
          const result = validateWorkflowEvolutionAnalysisResult(JSON.parse(row.result_json ?? 'null'));
          const scope = JSON.parse(row.scope_json) as { flowIds?: unknown };
          analyses.push({ analysisId: row.analysis_id, flowId: row.flow_id, flowIds: Array.isArray(scope.flowIds) ? scope.flowIds.map(String) : [], completedAtMs: row.completed_at_ms ?? row.requested_at_ms, diagnoses: result.diagnoses });
        } catch (error) {
          if (!options.tolerateInvalid) throw error;
          console.warn('[workflow-repair] skipped incompatible analysis source', { workflowId, analysisId: row.analysis_id });
        }
      }
      afterId = rows.at(-1)!.id;
    }
    const coveredRuns = new Set(analyses.flatMap(a => [...(a.flowIds ?? []), ...(a.flowId ? [a.flowId] : []), ...a.diagnoses.flatMap(d => d.flowIds)]));
    const legacy = await this.db.query<{
      diagnosis_id: string; flow_id: string; failure_signature: string; failure_mode: string; node_id: string | null;
      weak_node_id: string | null; error_text: string | null; gmt_modified: string | number;
    }>('SELECT diagnosis_id, flow_id, failure_signature, failure_mode, node_id, weak_node_id, error_text, gmt_modified FROM workflow_healing_diagnoses WHERE workflow_id = ?', [workflowId]);
    const legacyByRun = new Map<string, IssueAnalysis>();
    for (const row of legacy) {
      if (coveredRuns.has(row.flow_id)) continue;
      const numeric = Number(row.gmt_modified);
      const time = Number.isFinite(numeric) ? (numeric < 1e12 ? numeric * 1000 : numeric) : Date.parse(String(row.gmt_modified)) || 0;
      if (options.sinceMs !== undefined && time < options.sinceMs) continue;
      const entry = legacyByRun.get(row.flow_id) ?? { analysisId: 'legacy', flowId: row.flow_id, completedAtMs: time, diagnoses: [] };
      entry.completedAtMs = Math.max(entry.completedAtMs, time);
      entry.diagnoses.push({ diagnosisId: row.diagnosis_id, flowIds: [row.flow_id], nodeId: row.weak_node_id ?? row.node_id,
        failureSignature: row.failure_signature, failureMode: row.failure_mode, severity: 'medium',
        reasoning: row.error_text || row.failure_signature, evidenceEventIds: [] });
      legacyByRun.set(row.flow_id, entry);
    }
    return buildIssueGroups(workflowId, [...analyses, ...legacyByRun.values()]);
  }

  async list(workflowId: string, inputVersion: IssueAggregationInputVersion = ISSUE_AGGREGATION_INPUT_V1): Promise<PresentedIssueGroup[]> {
    const [groups, rows] = await Promise.all([this.groups(workflowId), this.db.query<WorkflowEvolutionAnalysisRow>(
      `SELECT * FROM workflow_evolution_analysis_runs WHERE workflow_id = ? AND scope_type = ? ORDER BY id DESC`, [workflowId, SCOPE])]);
    return groups.map(group => {
      const matches = rows.filter(row => (JSON.parse(row.scope_json) as Snapshot).input.signature === group.signature);
      const current = matches.find(row => (JSON.parse(row.scope_json) as Snapshot).input.inputDigest === group.inputDigest);
      const completed = (current?.status === 'completed' ? current : matches.find(row => row.status === 'completed'));
      const frozen = completed ? (JSON.parse(completed.scope_json) as Snapshot).input : null;
      const completedSnapshot = completed ? JSON.parse(completed.scope_json) as Snapshot : null;
      const currentSnapshot = current ? JSON.parse(current.scope_json) as Snapshot : null;
      const requested = aggregationPayload(group, inputVersion);
      const tooLarge = Buffer.byteLength(canonicalJson(requested.input), 'utf8') > 180_000 || group.sources.length > 500;
      return { ...group, summary: completed && frozen ? validateIssueSummary(JSON.parse(completed.result_json!), frozen) : null,
        summarySources: frozen?.sources ?? [],
        stale: !!completed && frozen?.inputDigest !== group.inputDigest,
        aggregationStatus: current?.status === 'queued' && Date.now() - current.requested_at_ms > 600_000 ? 'failed'
          : current?.status ?? (tooLarge ? 'too_large' : 'not_generated'), aggregationId: current?.analysis_id ?? null,
        aggregationInputVersion: completedSnapshot?.inputVersion ?? (completed ? null : currentSnapshot?.inputVersion ?? null),
        aggregationInputSummary: completedSnapshot?.inputSummary ?? (completed ? null : currentSnapshot?.inputSummary ?? null),
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
    const groups = await this.list(parent.workflow_id, inputVersion);
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

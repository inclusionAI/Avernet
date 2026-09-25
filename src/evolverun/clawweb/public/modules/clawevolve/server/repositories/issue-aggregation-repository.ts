import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { canonicalJson, digestCanonicalJson, validateWorkflowEvolutionAnalysisResult } from '../services/evolution/contracts.js';
import { buildIssueGroups, validateIssueSummary, type IssueAnalysis, type IssueGroup, type IssueSummary } from '../services/evolution/issue-aggregation.js';
import type { WorkflowEvolutionAnalysisRow } from './workflow-evolution-repository.js';

const SCOPE = 'issue_aggregate';
type Snapshot = { parentAnalysisId: string; input: IssueGroup };
export type PresentedIssueGroup = IssueGroup & { summary: IssueSummary | null; summarySources: IssueGroup['sources']; stale: boolean; aggregationStatus: string; aggregationId: string | null };

/** Aggregations are immutable versioned analysis snapshots, not diagnoses or suggestion states. */
export class IssueAggregationRepository {
  constructor(private readonly db: IDatabase) {}

  private async groups(workflowId: string): Promise<IssueGroup[]> {
    const analyses: IssueAnalysis[] = [];
    let afterId = 0;
    for (;;) {
      const rows = await this.db.query<WorkflowEvolutionAnalysisRow>(
        `SELECT * FROM workflow_evolution_analysis_runs WHERE workflow_id = ? AND status = 'completed'
         AND scope_type <> ? AND id > ? ORDER BY id ASC LIMIT 200`, [workflowId, SCOPE, afterId]);
      if (!rows.length) break;
      for (const row of rows) {
        const result = validateWorkflowEvolutionAnalysisResult(JSON.parse(row.result_json ?? 'null'));
        const scope = JSON.parse(row.scope_json) as { flowIds?: unknown };
        analyses.push({ analysisId: row.analysis_id, flowId: row.flow_id, flowIds: Array.isArray(scope.flowIds) ? scope.flowIds.map(String) : [], completedAtMs: row.completed_at_ms ?? row.requested_at_ms, diagnoses: result.diagnoses });
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
      const entry = legacyByRun.get(row.flow_id) ?? { analysisId: 'legacy', flowId: row.flow_id, completedAtMs: time, diagnoses: [] };
      entry.completedAtMs = Math.max(entry.completedAtMs, time);
      entry.diagnoses.push({ diagnosisId: row.diagnosis_id, flowIds: [row.flow_id], nodeId: row.weak_node_id ?? row.node_id,
        failureSignature: row.failure_signature, failureMode: row.failure_mode, severity: 'medium',
        reasoning: row.error_text || row.failure_signature, evidenceEventIds: [] });
      legacyByRun.set(row.flow_id, entry);
    }
    return buildIssueGroups(workflowId, [...analyses, ...legacyByRun.values()]);
  }

  async list(workflowId: string): Promise<PresentedIssueGroup[]> {
    const [groups, rows] = await Promise.all([this.groups(workflowId), this.db.query<WorkflowEvolutionAnalysisRow>(
      `SELECT * FROM workflow_evolution_analysis_runs WHERE workflow_id = ? AND scope_type = ? ORDER BY id DESC`, [workflowId, SCOPE])]);
    return groups.map(group => {
      const matches = rows.filter(row => (JSON.parse(row.scope_json) as Snapshot).input.signature === group.signature);
      const current = matches.find(row => (JSON.parse(row.scope_json) as Snapshot).input.inputDigest === group.inputDigest);
      const completed = (current?.status === 'completed' ? current : matches.find(row => row.status === 'completed'));
      const frozen = completed ? (JSON.parse(completed.scope_json) as Snapshot).input : null;
      const tooLarge = Buffer.byteLength(canonicalJson(group), 'utf8') > 180_000 || group.sources.length > 500;
      return { ...group, summary: completed && frozen ? validateIssueSummary(JSON.parse(completed.result_json!), frozen) : null,
        summarySources: frozen?.sources ?? [],
        stale: !!completed && frozen?.inputDigest !== group.inputDigest,
        aggregationStatus: tooLarge ? 'too_large' : current?.status === 'queued' && Date.now() - current.requested_at_ms > 600_000 ? 'failed' : current?.status ?? 'not_generated', aggregationId: current?.analysis_id ?? null,
      };
    });
  }

  async prepare(parentAnalysisId: string): Promise<Array<{ id: string; input: IssueGroup }>> {
    const parent = (await this.db.query<WorkflowEvolutionAnalysisRow>(
      'SELECT * FROM workflow_evolution_analysis_runs WHERE analysis_id = ?', [parentAnalysisId]))[0];
    if (!parent || parent.status !== 'completed' || parent.scope_type === SCOPE || !parent.workflow_id) throw new Error('completed run analysis required');
    await this.db.exec(`UPDATE workflow_evolution_analysis_runs SET status = 'failed', error_code = 'aggregation_timeout', state_version = state_version + 1
      WHERE workflow_id = ? AND scope_type = ? AND status = 'queued' AND requested_at_ms < ?`, [parent.workflow_id, SCOPE, Date.now() - 600_000]);
    const groups = await this.list(parent.workflow_id);
    const parentResult = validateWorkflowEvolutionAnalysisResult(JSON.parse(parent.result_json!));
    const parentScope = JSON.parse(parent.scope_json) as { flowIds?: unknown };
    const declaredFlows = Array.isArray(parentScope.flowIds) ? parentScope.flowIds.map(String) : [];
    const affectedFlows = new Set([...declaredFlows, ...(parent.flow_id ? [parent.flow_id] : []), ...parentResult.diagnoses.flatMap(d => d.flowIds)]);
    const affectedSignatures = new Set(parentResult.diagnoses.map(d => d.failureSignature));
    const jobs: Array<{ id: string; input: IssueGroup }> = [];
    for (const { summary: _summary, summarySources: _sources, stale: _stale, aggregationStatus, aggregationId: previousId, ...input } of groups) {
      if (!affectedSignatures.has(input.signature) && !_sources.some(source => affectedFlows.has(source.flowId))) continue;
      if (aggregationStatus === 'completed' || aggregationStatus === 'queued') continue;
      // Fail explicitly rather than silently summarizing a truncated sample.
      if (Buffer.byteLength(canonicalJson(input), 'utf8') > 180_000 || input.sources.length > 500) continue;
      const requestKey = digestCanonicalJson([SCOPE, input.inputDigest, previousId]);
      const id = `AG-${requestKey.slice(0, 40)}`;
      const now = Date.now();
      try {
        await this.db.exec(`INSERT INTO workflow_evolution_analysis_runs
          (analysis_id, request_key, scope_type, scope_json, workflow_id, status, analysis_version,
           requested_by, requested_at_ms, state_version, gmt_create, gmt_modified)
          VALUES (?, ?, ?, ?, ?, 'queued', 'workflow-issue-summary/v1', ?, ?, 0, ?, ?)`,
        [id, requestKey, SCOPE, canonicalJson({ parentAnalysisId, input }), parent.workflow_id, parent.requested_by, now, this.db.dialect.now(), this.db.dialect.now()]);
        jobs.push({ id, input });
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

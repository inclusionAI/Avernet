import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import {
  canonicalJson,
  digestCanonicalJson,
  validateWorkflowEvolutionAnalysisResult,
} from "../services/evolution/contracts.js";

export type WorkflowRunEvidenceInput = {
  eventId: string;
  payloadDigest: string;
  flowId: string;
  workflowId: string;
  nodeId?: string | null;
  eventType: string;
  producer: string;
  eventSeq: number;
  occurredAtMs: number;
  payload: unknown;
};

export type WorkflowRunEvidenceRow = {
  id: number;
  event_id: string;
  payload_digest: string;
  flow_id: string;
  workflow_id: string;
  node_id: string | null;
  event_type: string;
  producer: string;
  event_seq: number;
  occurred_at_ms: number;
  payload_json: string;
};

export type WorkflowEvolutionAnalysisRow = {
  id: number;
  analysis_id: string;
  request_key: string;
  scope_type: string;
  scope_json: string;
  flow_id: string | null;
  workflow_id: string | null;
  status: string;
  evidence_status: string | null;
  evidence_snapshot_ref: string | null;
  evidence_snapshot_digest: string | null;
  evidence_manifest_json: string | null;
  task_id: string | null;
  step_id: string | null;
  analysis_version: string;
  result_json: string | null;
  result_digest: string | null;
  diagnosis_count: number;
  error_code: string | null;
  requested_by: string | null;
  requested_at_ms: number;
  completed_at_ms: number | null;
  state_version: number;
};

type EvidenceManifest = {
  schemaVersion: "workflow-evidence-manifest/v1";
  capturedAtMs: number;
  flows: Array<{
    flowId: string;
    maxId: number;
    count: number;
    producers: string[];
    terminalSeen: boolean;
    sequenceContiguous: boolean;
    droppedBeforeTerminal: number;
    status: "complete" | "partial" | "missing";
  }>;
};

export type CreateWorkflowAnalysisInput = {
  analysisId: string;
  requestKey: string;
  scopeType: "single_run" | "run_set" | "workflow_window" | "global_window";
  scope: Record<string, unknown>;
  flowId?: string | null;
  workflowId?: string | null;
  analysisVersion: string;
  requestedBy?: string | null;
  requestedAtMs: number;
  taskId?: string | null;
  stepId?: string | null;
};

export type WorkflowEvolutionHistory = {
  diagnoses: Array<{
    analysisId: string;
    flowIds: string[];
    failureSignature: string;
    failureMode: string;
    reasoning: string;
    completedAtMs: number | null;
  }>;
  suggestions: Array<{
    suggestionId: string;
    failureSignature: string;
    status: string;
    fixSpec: string;
    proposalDigest: string | null;
    proposal: Record<string, unknown> | null;
    impactRunIds: string[];
  }>;
};

export class WorkflowEvidenceDigestConflictError extends Error {}
export class WorkflowAnalysisStateConflictError extends Error {}

export class WorkflowEvolutionRepository {
  constructor(private readonly db: IDatabase) {}

  async appendEvidenceBatch(events: WorkflowRunEvidenceInput[]): Promise<Array<{ eventId: string; status: "inserted" | "duplicate" }>> {
    if (events.length < 1 || events.length > 200) throw new Error("evidence batch size is invalid");
    return this.db.transaction(async (tx) => {
      const receipts: Array<{ eventId: string; status: "inserted" | "duplicate" }> = [];
      for (const event of events) {
        const existing = (await tx.query<{ payload_digest: string }>(
          "SELECT payload_digest FROM workflow_run_evidence_events WHERE event_id = ? LIMIT 1",
          [event.eventId],
        ))[0];
        if (existing) {
          if (existing.payload_digest !== event.payloadDigest) {
            throw new WorkflowEvidenceDigestConflictError(`evidence digest conflict for ${event.eventId}`);
          }
          receipts.push({ eventId: event.eventId, status: "duplicate" });
          continue;
        }
        const payloadJson = canonicalJson(event.payload);
        if (digestCanonicalJson(event.payload) !== event.payloadDigest) {
          throw new WorkflowEvidenceDigestConflictError(`payload digest does not match for ${event.eventId}`);
        }
        const now = tx.dialect.now();
        await tx.exec(
          `INSERT INTO workflow_run_evidence_events
           (event_id, payload_digest, flow_id, workflow_id, node_id, event_type, producer, event_seq, occurred_at_ms, payload_json, gmt_create, gmt_modified)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          [event.eventId, event.payloadDigest, event.flowId, event.workflowId, event.nodeId ?? null,
            event.eventType, event.producer, event.eventSeq, event.occurredAtMs, payloadJson, now, now],
        );
        receipts.push({ eventId: event.eventId, status: "inserted" });
      }
      return receipts;
    });
  }

  async listEvidence(flowId: string, options: { afterId?: number; maxId?: number; limit?: number } = {}): Promise<WorkflowRunEvidenceRow[]> {
    const clauses = ["flow_id = ?"];
    const params: unknown[] = [flowId];
    if (options.afterId != null) { clauses.push("id > ?"); params.push(options.afterId); }
    if (options.maxId != null) { clauses.push("id <= ?"); params.push(options.maxId); }
    const limit = Math.min(Math.max(options.limit ?? 1_000, 1), 5_000);
    return this.db.query<WorkflowRunEvidenceRow>(
      `SELECT * FROM workflow_run_evidence_events WHERE ${clauses.join(" AND ")} ORDER BY id ASC LIMIT ?`,
      [...params, limit],
    );
  }

  private async captureManifest(flowIds: string[], capturedAtMs: number): Promise<EvidenceManifest> {
    const flows: EvidenceManifest["flows"] = [];
    for (const flowId of flowIds) {
      const aggregate = (await this.db.query<{
        max_id: number | null;
        total: number;
        terminal_count: number;
        min_seq: number | null;
        max_seq: number | null;
        distinct_seq: number;
      }>(
        `SELECT MAX(id) AS max_id, COUNT(*) AS total,
                MIN(event_seq) AS min_seq, MAX(event_seq) AS max_seq,
                COUNT(DISTINCT event_seq) AS distinct_seq,
                SUM(CASE WHEN event_type = 'run.terminal' THEN 1 ELSE 0 END) AS terminal_count
         FROM workflow_run_evidence_events WHERE flow_id = ?`,
        [flowId],
      ))[0];
      const producers = await this.db.query<{ producer: string }>(
        "SELECT DISTINCT producer FROM workflow_run_evidence_events WHERE flow_id = ? ORDER BY producer",
        [flowId],
      );
      const count = Number(aggregate?.total ?? 0);
      const terminalSeen = Number(aggregate?.terminal_count ?? 0) > 0;
      const sequenceContiguous = count > 0
        && Number(aggregate?.min_seq ?? 0) === 1
        && Number(aggregate?.max_seq ?? 0) === count
        && Number(aggregate?.distinct_seq ?? 0) === count;
      const terminalRows = terminalSeen
        ? await this.db.query<{ payload_json: string }>(
          "SELECT payload_json FROM workflow_run_evidence_events WHERE flow_id = ? AND event_type = 'run.terminal'",
          [flowId],
        )
        : [];
      const droppedBeforeTerminal = terminalRows.reduce((max, row) => {
        try {
          const payload = JSON.parse(row.payload_json) as { droppedBeforeTerminal?: unknown };
          return Math.max(max, Math.max(0, Number(payload.droppedBeforeTerminal ?? 0) || 0));
        } catch {
          return max;
        }
      }, 0);
      const complete = terminalSeen && sequenceContiguous && droppedBeforeTerminal === 0;
      flows.push({
        flowId,
        maxId: Number(aggregate?.max_id ?? 0),
        count,
        producers: producers.map((item) => item.producer),
        terminalSeen,
        sequenceContiguous,
        droppedBeforeTerminal,
        status: count === 0 ? "missing" : complete ? "complete" : "partial",
      });
    }
    return { schemaVersion: "workflow-evidence-manifest/v1", capturedAtMs, flows };
  }

  async createAnalysisRun(input: CreateWorkflowAnalysisInput): Promise<WorkflowEvolutionAnalysisRow> {
    const existing = (await this.db.query<WorkflowEvolutionAnalysisRow>(
      "SELECT * FROM workflow_evolution_analysis_runs WHERE request_key = ? LIMIT 1",
      [input.requestKey],
    ))[0];
    if (existing) return existing;
    const scopeFlowIds = Array.isArray(input.scope.flowIds)
      ? [...new Set(input.scope.flowIds.map(String).filter(Boolean))]
      : (input.flowId ? [input.flowId] : []);
    const manifest = await this.captureManifest(scopeFlowIds, input.requestedAtMs);
    const evidenceStatus = manifest.flows.length === 0 || manifest.flows.every((flow) => flow.status === "missing")
      ? "missing"
      : manifest.flows.every((flow) => flow.status === "complete") ? "complete" : "partial";
    const manifestJson = canonicalJson(manifest);
    const snapshotDigest = digestCanonicalJson(manifest);
    const snapshotRef = manifest.flows.length === 1
      ? `db://workflow_run_evidence_events/${encodeURIComponent(manifest.flows[0].flowId)}?maxId=${manifest.flows[0].maxId}`
      : `db://workflow_run_evidence_events/snapshot/${snapshotDigest}`;
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO workflow_evolution_analysis_runs
       (analysis_id, request_key, scope_type, scope_json, flow_id, workflow_id, status,
        evidence_status, evidence_snapshot_ref, evidence_snapshot_digest, evidence_manifest_json,
        task_id, step_id, analysis_version, requested_by, requested_at_ms, state_version, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`,
      [input.analysisId, input.requestKey, input.scopeType, canonicalJson(input.scope), input.flowId ?? null,
        input.workflowId ?? null, evidenceStatus, snapshotRef, snapshotDigest, manifestJson,
        input.taskId ?? null, input.stepId ?? null, input.analysisVersion, input.requestedBy ?? null,
        input.requestedAtMs, now, now],
    );
    const row = await this.findAnalysisRun(input.analysisId);
    if (!row) throw new Error("analysis run insert failed");
    return row;
  }

  async findAnalysisRun(analysisId: string): Promise<WorkflowEvolutionAnalysisRow | null> {
    return (await this.db.query<WorkflowEvolutionAnalysisRow>(
      "SELECT * FROM workflow_evolution_analysis_runs WHERE analysis_id = ? LIMIT 1",
      [analysisId],
    ))[0] ?? null;
  }

  async findAnalysisRunForFlow(analysisId: string, flowId: string): Promise<WorkflowEvolutionAnalysisRow | null> {
    const analysis = await this.findAnalysisRun(analysisId);
    if (!analysis) return null;
    if (analysis.flow_id === flowId) return analysis;
    try {
      const scope = JSON.parse(analysis.scope_json) as { flowIds?: unknown };
      return Array.isArray(scope.flowIds) && scope.flowIds.map(String).includes(flowId)
        ? analysis
        : null;
    } catch {
      return null;
    }
  }

  async listEvidenceByEventIds(eventIds: string[]): Promise<WorkflowRunEvidenceRow[]> {
    const uniqueIds = [...new Set(eventIds.map((eventId) => eventId.trim()).filter(Boolean))].slice(0, 1_000);
    if (uniqueIds.length === 0) return [];
    const rows = await this.db.query<WorkflowRunEvidenceRow>(
      `SELECT * FROM workflow_run_evidence_events WHERE event_id IN (${uniqueIds.map(() => "?").join(", ")})`,
      uniqueIds,
    );
    const byId = new Map(rows.map((row) => [row.event_id, row]));
    return uniqueIds.flatMap((eventId) => {
      const row = byId.get(eventId);
      return row ? [row] : [];
    });
  }

  async findLatestAnalysisRunByFlow(flowId: string): Promise<WorkflowEvolutionAnalysisRow | null> {
    return (await this.db.query<WorkflowEvolutionAnalysisRow>(
      "SELECT * FROM workflow_evolution_analysis_runs WHERE flow_id = ? ORDER BY id DESC LIMIT 1",
      [flowId],
    ))[0] ?? null;
  }

  async getAnalysisInput(analysisId: string): Promise<{
    analysis: WorkflowEvolutionAnalysisRow;
    evidence: WorkflowRunEvidenceRow[];
    history: WorkflowEvolutionHistory;
  } | null> {
    const analysis = await this.findAnalysisRun(analysisId);
    if (!analysis) return null;
    const manifest = JSON.parse(analysis.evidence_manifest_json ?? "null") as EvidenceManifest | null;
    const pages = await Promise.all((manifest?.flows ?? []).map(async (flow) => {
      const rows: WorkflowRunEvidenceRow[] = [];
      let afterId = 0;
      while (afterId < flow.maxId) {
        const page = await this.listEvidence(flow.flowId, { afterId, maxId: flow.maxId, limit: 1_000 });
        if (page.length === 0) break;
        rows.push(...page);
        afterId = page.at(-1)!.id;
      }
      return rows;
    }));
    const history: WorkflowEvolutionHistory = { diagnoses: [], suggestions: [] };
    if (analysis.workflow_id) {
      const prior = await this.db.query<WorkflowEvolutionAnalysisRow>(
        `SELECT * FROM workflow_evolution_analysis_runs
         WHERE workflow_id = ? AND analysis_id <> ? AND status = 'completed' AND result_json IS NOT NULL
         ORDER BY id DESC LIMIT 20`,
        [analysis.workflow_id, analysis.analysis_id],
      );
      for (const item of prior) {
        if (history.diagnoses.length >= 100) break;
        try {
          const parsed = validateWorkflowEvolutionAnalysisResult(JSON.parse(item.result_json!));
          for (const diagnosis of parsed.diagnoses) {
            if (history.diagnoses.length >= 100) break;
            history.diagnoses.push({
              analysisId: item.analysis_id,
              flowIds: diagnosis.flowIds,
              failureSignature: diagnosis.failureSignature,
              failureMode: diagnosis.failureMode,
              reasoning: diagnosis.reasoning,
              completedAtMs: item.completed_at_ms,
            });
          }
        } catch { /* ignore invalid historical results without weakening the current frozen input */ }
      }
      const suggestions = await this.db.query<{
        id: number; failure_signature: string; status: string; fix_spec: string | null;
        proposal_digest: string | null; proposal_json: string | null; impact_run_ids: string | null;
      }>(
        `SELECT id, failure_signature, status, fix_spec, proposal_digest, proposal_json, impact_run_ids
         FROM workflow_healing_suggestions WHERE workflow_id = ? ORDER BY gmt_modified DESC LIMIT 20`,
        [analysis.workflow_id],
      );
      for (const suggestion of suggestions) {
        let proposal: Record<string, unknown> | null = null;
        let impactRunIds: string[] = [];
        try {
          const parsed = JSON.parse(suggestion.proposal_json ?? "null") as unknown;
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) proposal = parsed as Record<string, unknown>;
        } catch { /* expose malformed legacy proposal as absent */ }
        try {
          const parsed = JSON.parse(suggestion.impact_run_ids ?? "[]") as unknown;
          if (Array.isArray(parsed)) impactRunIds = parsed.map(String);
        } catch { /* expose malformed legacy run ids as empty */ }
        history.suggestions.push({
          suggestionId: String(suggestion.id),
          failureSignature: suggestion.failure_signature,
          status: suggestion.status,
          fixSpec: suggestion.fix_spec ?? suggestion.failure_signature,
          proposalDigest: suggestion.proposal_digest,
          proposal,
          impactRunIds,
        });
      }
    }
    return { analysis, evidence: pages.flat().sort((a, b) => a.id - b.id), history };
  }

  async markAnalyzing(analysisId: string, expectedVersion: number): Promise<WorkflowEvolutionAnalysisRow> {
    const result = await this.db.exec(
      `UPDATE workflow_evolution_analysis_runs
       SET status = 'analyzing', state_version = state_version + 1, gmt_modified = ?
       WHERE analysis_id = ? AND status IN ('queued', 'collecting') AND state_version = ?`,
      [this.db.dialect.now(), analysisId, expectedVersion],
    );
    if ((result.affectedRows ?? 0) !== 1) throw new WorkflowAnalysisStateConflictError("analysis state conflict");
    return (await this.findAnalysisRun(analysisId))!;
  }

  async completeAnalysisRun(analysisId: string, rawResult: unknown, completedAtMs: number): Promise<WorkflowEvolutionAnalysisRow> {
    const result = validateWorkflowEvolutionAnalysisResult(rawResult);
    if (result.analysisId !== analysisId) throw new Error("analysis result identity mismatch");
    const frozenInput = await this.getAnalysisInput(analysisId);
    if (!frozenInput) throw new Error("analysis run not found");
    const allowedEventIds = new Set(frozenInput.evidence.map((event) => event.event_id));
    const manifest = JSON.parse(frozenInput.analysis.evidence_manifest_json ?? "null") as EvidenceManifest | null;
    const allowedFlowIds = new Set((manifest?.flows ?? []).map((flow) => flow.flowId));
    for (const diagnosis of result.diagnoses) {
      const unknownEventId = diagnosis.evidenceEventIds.find((eventId) => !allowedEventIds.has(eventId));
      if (unknownEventId) throw new Error(`diagnosis references evidence outside snapshot: ${unknownEventId}`);
      const unknownFlowId = allowedFlowIds.size > 0
        ? diagnosis.flowIds.find((flowId) => !allowedFlowIds.has(flowId))
        : undefined;
      if (unknownFlowId) throw new Error(`diagnosis references flow outside scope: ${unknownFlowId}`);
      if (diagnosis.proposal && frozenInput.analysis.workflow_id
        && diagnosis.proposal.workflowId !== frozenInput.analysis.workflow_id) {
        throw new Error("proposal workflow does not match analysis scope");
      }
    }
    const resultJson = canonicalJson(result);
    const resultDigest = digestCanonicalJson(result);
    await this.db.transaction(async (tx) => {
      const updated = await tx.exec(
        `UPDATE workflow_evolution_analysis_runs
         SET status = 'completed', result_json = ?, result_digest = ?, diagnosis_count = ?, error_code = NULL,
             completed_at_ms = ?, state_version = state_version + 1, gmt_modified = ?
         WHERE analysis_id = ? AND status IN ('queued', 'collecting', 'analyzing')`,
        [resultJson, resultDigest, result.diagnoses.length, completedAtMs, tx.dialect.now(), analysisId],
      );
      if ((updated.affectedRows ?? 0) !== 1) throw new WorkflowAnalysisStateConflictError("analysis completion state conflict");
      const analysis = (await tx.query<WorkflowEvolutionAnalysisRow>(
        "SELECT * FROM workflow_evolution_analysis_runs WHERE analysis_id = ? LIMIT 1",
        [analysisId],
      ))[0]!;
      for (const diagnosis of result.diagnoses) {
        if (!diagnosis.proposal) continue;
        const existing = (await tx.query<{
          id: number;
          source_diagnosis_ids: string | null;
          impact_run_ids: string | null;
          proposal_digest: string | null;
          apply_task_id: string | null;
          status: string;
          verification_status: string | null;
          recurrence_count: number;
          last_recurrence_at: number | string | null;
          verification_checked_at: number | string | null;
        }>(
          `SELECT id, source_diagnosis_ids, impact_run_ids, proposal_digest, apply_task_id, status,
                  verification_status, recurrence_count, last_recurrence_at, verification_checked_at
           FROM workflow_healing_suggestions
           WHERE workflow_id = ? AND failure_signature = ? ORDER BY id DESC LIMIT 1`,
          [diagnosis.proposal.workflowId, diagnosis.failureSignature],
        ))[0];
        const proposalJson = canonicalJson(diagnosis.proposal);
        const proposalDigest = digestCanonicalJson(diagnosis.proposal);
        const merge = (raw: string | null, items: string[]) => {
          let current: string[] = [];
          try { const parsed = JSON.parse(raw ?? "[]"); if (Array.isArray(parsed)) current = parsed.map(String); } catch { /* reset invalid legacy JSON */ }
          return canonicalJson([...new Set([...current, ...items])]);
        };
        if (existing) {
          const proposalChanged = existing.proposal_digest !== proposalDigest;
          const recurrenceDetected = !proposalChanged && ["applied", "applied_unverified", "verified"].includes(existing.status);
          const observedAt = Math.floor(completedAtMs / 1000);
          await tx.exec(
            `UPDATE workflow_healing_suggestions
             SET node_id = ?, weak_node_id = ?, failure_mode = ?, fix_kind = 'workflow_patch', fix_spec = ?,
                 source_diagnosis_ids = ?, impact_run_ids = ?, proposal_json = ?, proposal_digest = ?,
                 status = ?, apply_task_id = ?,
                 verification_status = ?, recurrence_count = ?, last_recurrence_at = ?, verification_checked_at = ?,
                 applied_at = CASE WHEN ? = 1 THEN NULL ELSE applied_at END,
                 updated_by = ?, gmt_modified = ?
             WHERE id = ?`,
            [diagnosis.nodeId, diagnosis.nodeId, diagnosis.failureMode, diagnosis.proposal.summary,
              merge(existing.source_diagnosis_ids, [diagnosis.diagnosisId]), merge(existing.impact_run_ids, diagnosis.flowIds),
              proposalJson, proposalDigest, proposalChanged ? "pending" : recurrenceDetected ? "applied_unverified" : existing.status,
              proposalChanged ? null : existing.apply_task_id,
              proposalChanged ? "not_started" : recurrenceDetected ? "recurrence_detected" : existing.verification_status,
              Number(existing.recurrence_count ?? 0) + (recurrenceDetected ? 1 : 0),
              recurrenceDetected ? observedAt : existing.last_recurrence_at,
              proposalChanged ? null : recurrenceDetected ? observedAt : existing.verification_checked_at,
              proposalChanged ? 1 : 0,
              analysis.requested_by, tx.dialect.now(), existing.id],
          );
        } else {
          const now = tx.dialect.now();
          await tx.exec(
            `INSERT INTO workflow_healing_suggestions
             (workflow_id, node_id, weak_node_id, failure_signature, failure_mode, fix_kind, fix_spec,
              source_diagnosis_ids, impact_run_ids, status, action_log, created_by, updated_by,
              proposal_json, proposal_digest, apply_task_id, gmt_create, gmt_modified)
             VALUES (?, ?, ?, ?, ?, 'workflow_patch', ?, ?, ?, 'pending', '[]', ?, ?, ?, ?, NULL, ?, ?)`,
            [diagnosis.proposal.workflowId, diagnosis.nodeId, diagnosis.nodeId, diagnosis.failureSignature,
              diagnosis.failureMode, diagnosis.proposal.summary, canonicalJson([diagnosis.diagnosisId]),
              canonicalJson(diagnosis.flowIds), analysis.requested_by, analysis.requested_by,
              proposalJson, proposalDigest, now, now],
          );
        }
      }
    });
    return (await this.findAnalysisRun(analysisId))!;
  }

  async failAnalysisRun(analysisId: string, errorCode: string, completedAtMs: number): Promise<WorkflowEvolutionAnalysisRow> {
    const updated = await this.db.exec(
      `UPDATE workflow_evolution_analysis_runs
       SET status = 'failed', error_code = ?, completed_at_ms = ?, state_version = state_version + 1, gmt_modified = ?
       WHERE analysis_id = ? AND status IN ('queued', 'collecting', 'analyzing')`,
      [errorCode.slice(0, 64), completedAtMs, this.db.dialect.now(), analysisId],
    );
    if ((updated.affectedRows ?? 0) !== 1) throw new WorkflowAnalysisStateConflictError("analysis failure state conflict");
    return (await this.findAnalysisRun(analysisId))!;
  }

  async listProjectedDiagnoses(options: { workflowId?: string; flowId?: string; analysisId?: string; query?: string; limit?: number; offset?: number } = {}): Promise<{ rows: Array<Record<string, unknown>>; total: number }> {
    const clauses = ["status = 'completed'", "result_json IS NOT NULL"];
    const params: unknown[] = [];
    if (options.workflowId) { clauses.push("workflow_id = ?"); params.push(options.workflowId); }
    if (options.flowId) { clauses.push("flow_id = ?"); params.push(options.flowId); }
    if (options.analysisId) { clauses.push("analysis_id = ?"); params.push(options.analysisId); }
    const analyses = await this.db.query<WorkflowEvolutionAnalysisRow>(
      `SELECT * FROM workflow_evolution_analysis_runs WHERE ${clauses.join(" AND ")} ORDER BY id DESC LIMIT 500`,
      params,
    );
    const projected = analyses.flatMap((analysis) => {
      try {
        const parsed = validateWorkflowEvolutionAnalysisResult(JSON.parse(analysis.result_json!));
        return parsed.diagnoses.map((diagnosis) => ({
          id: `${analysis.analysis_id}:${diagnosis.diagnosisId}`,
          diagnosis_id: diagnosis.diagnosisId,
          analysis_id: analysis.analysis_id,
          flow_id: diagnosis.flowIds[0] ?? analysis.flow_id,
          flow_ids: diagnosis.flowIds,
          workflow_id: analysis.workflow_id,
          run_id: diagnosis.flowIds[0] ?? analysis.flow_id,
          node_id: diagnosis.nodeId,
          failure_signature: diagnosis.failureSignature,
          failure_mode: diagnosis.failureMode,
          weak_node_id: diagnosis.nodeId,
          suggested_fix_kind: diagnosis.proposal ? "workflow_patch" : null,
          reasoning: diagnosis.reasoning,
          evidence_event_ids: diagnosis.evidenceEventIds,
          error_text: null,
          gmt_create: analysis.completed_at_ms,
          gmt_modified: analysis.completed_at_ms,
        }));
      } catch {
        return [];
      }
    });
    const filtered = options.query
      ? projected.filter((item) => JSON.stringify(item).toLowerCase().includes(options.query!.toLowerCase()))
      : projected;
    const offset = Math.max(options.offset ?? 0, 0);
    const limit = Math.min(Math.max(options.limit ?? 50, 1), 200);
    return { rows: filtered.slice(offset, offset + limit), total: filtered.length };
  }
}

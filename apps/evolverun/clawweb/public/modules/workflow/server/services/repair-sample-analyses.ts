import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { validateWorkflowEvolutionAnalysisResult } from '@avernet/clawevolve/server/services/evolution/contracts';

type SampleAnalysis = { analysisComplete: boolean; signatures: string[] };
type AnalysisRow = { id: number; completed_at_ms: number | null; flow_id: string | null; scope_json: string; result_json: string };

function samplePredicate(db: IDatabase, ids: string[]) {
  const binds = ids.map(() => '?').join(',');
  const scope = "CASE WHEN JSON_VALID(scope_json) THEN scope_json ELSE '{}' END";
  const result = "CASE WHEN JSON_VALID(result_json) THEN result_json ELSE '{}' END";
  if (db.dialect.driver === 'better-sqlite3') return {
    sql: `(flow_id IN (${binds})
      OR EXISTS (SELECT 1 FROM json_each(${scope}, '$.flowIds') f WHERE f.value IN (${binds}))
      OR EXISTS (SELECT 1 FROM json_each(${result}, '$.diagnoses') d,
        json_each(CASE WHEN JSON_VALID(d.value) THEN d.value ELSE '{}' END, '$.flowIds') f WHERE f.value IN (${binds})))`,
    params: [...ids, ...ids, ...ids],
  };
  // MySQL/OceanBase: scalar containment also searches nested arrays returned by
  // JSON_EXTRACT(..., '$.diagnoses[*].flowIds'). Parameters remain JSON strings.
  return {
    sql: `(flow_id IN (${binds}) OR ${ids.map(() => `JSON_CONTAINS(JSON_EXTRACT(${scope}, '$.flowIds'), JSON_QUOTE(?))
      OR JSON_CONTAINS(JSON_EXTRACT(${result}, '$.diagnoses[*].flowIds'), JSON_QUOTE(?))`).join(' OR ')})`,
    params: [...ids, ...ids.flatMap(id => [id, id])],
  };
}

/** Latest applicable analysis wins per sampled run; unrelated traffic cannot evict evidence. */
export async function fillRepairSampleAnalyses(db: IDatabase, workflowId: string, nodeId: string,
  samples: ReadonlyMap<string, SampleAnalysis>): Promise<void> {
  const ids = [...samples.keys()];
  for (let offset = 0; offset < ids.length; offset += 100) {
    const batch = new Set(ids.slice(offset, offset + 100));
    const predicate = samplePredicate(db, [...batch]);
    const visited = new Set<string>();
    let before: { time: number; id: number } | null = null;
    while (visited.size < batch.size) {
      const cursor = before ? 'AND (COALESCE(completed_at_ms, 0) < ? OR (COALESCE(completed_at_ms, 0) = ? AND id < ?))' : '';
      const rows: AnalysisRow[] = await db.query<AnalysisRow>(
        `SELECT id, completed_at_ms, flow_id, scope_json, result_json FROM workflow_evolution_analysis_runs
         WHERE workflow_id = ? AND status = 'completed' AND scope_type <> 'issue_aggregate' AND ${predicate.sql} ${cursor}
         ORDER BY COALESCE(completed_at_ms, 0) DESC, id DESC LIMIT 200`,
        [workflowId, ...predicate.params, ...(before ? [before.time, before.time, before.id] : [])]);
      if (!rows.length) break;
      for (const row of rows) {
        const declared: string[] = row.flow_id ? [row.flow_id] : [];
        try {
          const scope = JSON.parse(row.scope_json);
          if (Array.isArray(scope.flowIds)) declared.push(...scope.flowIds.filter((id: unknown): id is string => typeof id === 'string'));
          const analysis = validateWorkflowEvolutionAnalysisResult(JSON.parse(row.result_json));
          if (scope.nodeId && scope.nodeId !== nodeId || scope.nodeIds?.length && !scope.nodeIds.includes(nodeId)) continue;
          for (const id of [...declared, ...analysis.diagnoses.flatMap(value => value.flowIds)]) {
            // An analysis may span batches; only this batch has been read newest-first.
            if (!batch.has(id) || visited.has(id)) continue;
            visited.add(id);
            const sample = samples.get(id)!;
            sample.analysisComplete = analysis.unknowns.length === 0;
            sample.signatures = analysis.diagnoses.filter(value => value.flowIds.includes(id) && value.nodeId === nodeId).map(value => value.failureSignature);
          }
        } catch {
          // Invalid latest evidence cannot be replaced by an older clean analysis.
          for (const id of declared.length ? declared : batch) if (batch.has(id)) visited.add(id);
        }
      }
      const last = rows.at(-1)!;
      before = { time: Number(last.completed_at_ms ?? 0), id: Number(last.id) };
      if (rows.length < 200) break;
    }
  }
}

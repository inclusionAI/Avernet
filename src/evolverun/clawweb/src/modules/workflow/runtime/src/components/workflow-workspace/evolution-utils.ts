import type { EvolveRunDiagnosis } from '../../api/client'

export type DiagnosisCluster = {
  key: string
  workflowId: string
  signature: string
  node: string
  mode: string
  diagnoses: EvolveRunDiagnosis[]
  instances: Array<{
    analysisId: string
    diagnosisId: string
    flowId: string
    occurredAtMs: number
    diagnosis: EvolveRunDiagnosis
  }>
  runIds: string[]
  latest: EvolveRunDiagnosis
}

export function timeValue(value: number | string): number {
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value
  const numeric = Number(value)
  if (Number.isFinite(numeric)) return numeric < 1e12 ? numeric * 1000 : numeric
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export function aggregateDiagnoses(diagnoses: EvolveRunDiagnosis[], runId?: string): DiagnosisCluster[] {
  const grouped = new Map<string, EvolveRunDiagnosis[]>()
  for (const diagnosis of diagnoses) {
    const diagnosisFlowIds = diagnosis.flow_ids?.length ? diagnosis.flow_ids : [diagnosis.flow_id]
    if (runId && !diagnosisFlowIds.includes(runId)) continue
    const key = `${diagnosis.workflow_id}\u0000${diagnosis.failure_signature}`
    const list = grouped.get(key) ?? []
    list.push(diagnosis)
    grouped.set(key, list)
  }
  return [...grouped.entries()].map(([key, items]) => {
    const sorted = items.slice().sort((a, b) => timeValue(b.gmt_create) - timeValue(a.gmt_create))
    const latest = sorted[0]
    const instances = sorted.flatMap((diagnosis) => {
      const flowIds = diagnosis.flow_ids?.length ? diagnosis.flow_ids : [diagnosis.flow_id]
      return flowIds.map((flowId) => ({
        analysisId: diagnosis.analysis_id ?? 'legacy',
        diagnosisId: diagnosis.diagnosis_id,
        flowId,
        occurredAtMs: timeValue(diagnosis.gmt_create),
        diagnosis,
      }))
    })
    return {
      key,
      workflowId: latest.workflow_id,
      signature: latest.failure_signature,
      node: latest.weak_node_id ?? latest.node_id ?? '未知节点',
      mode: latest.failure_mode,
      diagnoses: sorted,
      instances,
      runIds: [...new Set(instances.map((item) => item.flowId))],
      latest,
    }
  }).sort((a, b) => timeValue(b.latest.gmt_create) - timeValue(a.latest.gmt_create))
}

type PatchOperation = Record<string, unknown> & { nodeId?: unknown; path?: unknown; op?: unknown }

export function diffWorkflowPatchOperations(previous: Record<string, unknown> | null, current: Record<string, unknown> | null): {
  added: PatchOperation[]
  changed: PatchOperation[]
  removed: PatchOperation[]
} {
  const operations = (value: Record<string, unknown> | null): PatchOperation[] => (
    Array.isArray(value?.operations)
      ? value.operations.filter((item): item is PatchOperation => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
      : []
  )
  const key = (item: PatchOperation) => `${String(item.nodeId ?? '')}\u0000${String(item.path ?? '')}`
  const before = new Map(operations(previous).map((item) => [key(item), item]))
  const after = new Map(operations(current).map((item) => [key(item), item]))
  return {
    added: [...after].filter(([operationKey]) => !before.has(operationKey)).map(([, item]) => item),
    changed: [...after].filter(([operationKey, item]) => before.has(operationKey) && JSON.stringify(before.get(operationKey)) !== JSON.stringify(item)).map(([, item]) => item),
    removed: [...before].filter(([operationKey]) => !after.has(operationKey)).map(([, item]) => item),
  }
}

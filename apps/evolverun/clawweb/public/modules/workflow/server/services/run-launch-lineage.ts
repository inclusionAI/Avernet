import { FanoutReadError, type RunReader, type RunReadInspection, type RunSubworkflowsReader } from '../contracts/run-subworkflows.js';
import type { FlowRunLineage } from './flow-run-lineage.js';

// Match the stored lineage contract without normalizing or repairing an ID.
function validLineageId(value: unknown): value is string {
  if (typeof value !== 'string' || !value.trim()) return false;
  const characters = Array.from(value);
  return characters.length <= 255 && !characters.some(char => char.length === 1
    && char.charCodeAt(0) >= 0xD800 && char.charCodeAt(0) <= 0xDFFF);
}

/** Ordinary detail only. A readable child is not authority to reveal an ancestor. */
export async function readVisibleRunLineage(
  reader: RunSubworkflowsReader, actor: RunReader, inspection: RunReadInspection,
): Promise<Partial<FlowRunLineage>> {
  if (inspection.managed || inspection.restricted || inspection.sensitiveChild) return {};
  const lineage: Partial<FlowRunLineage> = {};
  const visible = new Map<string, boolean>();
  for (const field of ['parent_flow_id', 'root_flow_id'] as const) {
    if (!Object.hasOwn(inspection.run, field)) continue;
    const id = inspection.run[field];
    if (id === null) { lineage[field] = null; continue; }
    if (!validLineageId(id)) continue;
    if (!visible.has(id)) {
      try {
        const ancestor = await reader.inspectRun(actor, id);
        // SQL collation or a reader must not substitute a different identity.
        visible.set(id, ancestor.run.flow_id === id);
      } catch (error) {
        if (!(error instanceof FanoutReadError) || !['forbidden', 'not_found'].includes(error.code)) throw error;
        visible.set(id, false);
      }
    }
    if (visible.get(id)) lineage[field] = id;
  }
  return lineage;
}

import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { packManifest, packWorkflowBaseline } from './packCanvas'
import { normalizationChanges } from './yamlDraft'
import type { Snapshot } from './packVersionSession'

export type BaselineComparison = 'unavailable' | 'same' | 'different' | 'uncomparable'

/** Compare the frozen Git snapshot with a DB baseline, not the editable working file. */
export function compareGitDatabase(base: Snapshot | undefined, id: string | null, database: WorkflowSpec | null | undefined): BaselineComparison {
  if (!base || !id || !database || database.id !== id || base.workflowId !== id) return 'unavailable'
  try {
    const location = packManifest(base.versionFiles, id)
    const file = base.versionFiles.find(item => item.path === location.path)
    if (location.packId !== base.packId || !file) return 'unavailable'
    new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(file.contentBase64), c => c.charCodeAt(0)))
  } catch { return 'unavailable' }
  try {
    const original = packWorkflowBaseline(base.versionFiles, id, base.packId)
    return !original.spec ? 'uncomparable' : normalizationChanges(original.spec, database).length ? 'different' : 'same'
  } catch { return 'uncomparable' }
}

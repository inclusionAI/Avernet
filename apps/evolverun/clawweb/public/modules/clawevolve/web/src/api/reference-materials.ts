import { fetchJson } from '@avernet/clawweb-shared/web/api/client'

export type MaterialFile = { path: string; size: number; sha256: string; content_type: string }
export type MaterialOptions = {
  available: boolean; maxFileBytes: number; maxFiles: number; maxTotalBytes: number
  maxArchiveBytes: number; maxPathBytes: number; maxPathComponentBytes: number; maxDepth: number
}
export type MaterialUpload = { materialId: string; expiresAt: number; files: MaterialFile[]; totalBytes: number }
export type MaterialView = { files: MaterialFile[]; totalBytes: number; download: { method: 'GET'; url: string } }

export const referenceMaterialsApi = {
  options: () => fetchJson<MaterialOptions>('/api/evolve/reference-materials/options'),
  upload: (body: FormData) => fetchJson<MaterialUpload>('/api/evolve/reference-materials/uploads', { method: 'POST', body }),
  view: (taskId: string) => fetchJson<MaterialView>(`/api/evolve/tasks/${encodeURIComponent(taskId)}/reference-materials`),
}

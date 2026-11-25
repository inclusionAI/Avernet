import { useEffect, useRef, useState } from 'react'
import { referenceMaterialsApi, type MaterialOptions, type MaterialUpload } from '../api/reference-materials'

export type SelectedMaterial = { path: string; file: File }
export function validateSelection(files: SelectedMaterial[], options: MaterialOptions): void {
  if (files.length > options.maxFiles) throw new Error(`文件数量不能超过 ${options.maxFiles} 个`)
  const paths = new Set<string>()
  let total = 0
  for (const item of files) {
    const parts = item.path.split('/')
    if (!item.path || new TextEncoder().encode(item.path).length > options.maxPathBytes
      || parts.length > options.maxDepth + 1 || /[\\\u0000-\u001f\u007f]/.test(item.path)
      || /^[A-Za-z]:/.test(item.path) || parts.some(part => !part || part === '.' || part === '..' || new TextEncoder().encode(part).length > options.maxPathComponentBytes)) throw new Error(`文件路径不合法：${item.path}`)
    if (paths.has(item.path) || [...paths].some(path => path.startsWith(`${item.path}/`) || item.path.startsWith(`${path}/`))) throw new Error(`文件路径重复或冲突：${item.path}`)
    if (item.file.size > options.maxFileBytes) throw new Error(`单文件超过大小限制：${item.path}`)
    paths.add(item.path); total += item.file.size
  }
  if (total > options.maxTotalBytes) throw new Error('材料总大小超过限制')
}

export function useTaskReferenceMaterials(enabled: boolean) {
  const [opened, setOpened] = useState(false)
  const [options, setOptions] = useState<MaterialOptions | null>(null)
  const [files, setFiles] = useState<SelectedMaterial[]>([])
  const [uploaded, setUploaded] = useState<MaterialUpload | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const generation = useRef(0)
  const inFlight = useRef(false)
  useEffect(() => {
    let active = true
    generation.current += 1
    if (!enabled) { setOpened(false); setFiles([]); setUploaded(null); setError(''); setBusy(false); return }
    if (!opened) return
    setError('')
    void referenceMaterialsApi.options().then(value => { if (active) setOptions(value) })
      .catch(() => { if (active) { setOptions(null); setError('上传能力检查失败，请重试') } })
    return () => { active = false; generation.current += 1 }
  }, [enabled, opened, attempt])
  useEffect(() => {
    if (!uploaded) return
    const timeout = window.setTimeout(() => {
      setUploaded(null); setError('材料上传引用已过期，请重新上传')
    }, Math.max(0, uploaded.expiresAt - Date.now()))
    return () => window.clearTimeout(timeout)
  }, [uploaded])
  function add(selected: FileList | File[]) {
    if (!options || busy) return
    try {
      const added = Array.from(selected).map(file => ({ file, path: (file.webkitRelativePath || file.name).normalize('NFC') }))
      if (!added.length) throw new Error('所选目录为空，请选择包含文件的目录')
      const next = [...files, ...added].sort((a, b) => a.path.localeCompare(b.path))
      validateSelection(next, options)
      setFiles(next); setUploaded(null); setError('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '选择材料失败') }
  }
  function remove(path: string) {
    if (busy) return
    setFiles(current => current.filter(item => item.path !== path && !item.path.startsWith(`${path}/`)))
    setUploaded(null); setError('')
  }
  async function upload() {
    if (!options || !files.length || inFlight.current) return
    inFlight.current = true; setBusy(true); setError('')
    const current = generation.current
    try {
      validateSelection(files, options)
      // Load ZIP support only for users who upload materials.
      const { default: JSZip } = await import('jszip')
      const zip = new JSZip()
      for (const item of files) zip.file(item.path, item.file.arrayBuffer().then(buffer => new Uint8Array(buffer)), { createFolders: false })
      const bytes = await zip.generateAsync({ type: 'uint8array', compression: 'STORE' })
      if (bytes.length > options.maxArchiveBytes) throw new Error('材料包超过上传限制')
      const form = new FormData()
      form.set('package', new Blob([bytes as Uint8Array<ArrayBuffer>], { type: 'application/zip' }), 'reference-materials.zip')
      const result = await referenceMaterialsApi.upload(form)
      if (current === generation.current) setUploaded(result)
    } catch (reason) {
      if (current === generation.current) { setUploaded(null); setError(reason instanceof Error ? reason.message : '材料上传失败') }
    } finally {
      inFlight.current = false
      if (current === generation.current) setBusy(false)
    }
  }
  return { opened, open: () => setOpened(true), options, files, busy, error, uploaded, add, remove, upload,
    retryOptions: () => setAttempt(value => value + 1),
    blocked: enabled && (busy || (files.length > 0 && !uploaded)),
    uploadId: enabled ? uploaded?.materialId : undefined,
  }
}
export type TaskReferenceMaterialsState = ReturnType<typeof useTaskReferenceMaterials>

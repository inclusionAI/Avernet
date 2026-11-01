import { useEditorStore } from './store'
import { hasAnyPackVersionRisk, hasAnyPackVersionUpload } from './packVersionSession'

/** Explicit discard only; ordinary selection and mutation never silently reset a draft. */
export function confirmDiscardEditor(): boolean {
  if (hasAnyPackVersionUpload()) { window.alert('版本文件正在上传，请等待完成后再离开。'); return false }
  if (hasAnyPackVersionRisk() && !window.confirm('版本文件有未保存更改或保存状态不确定；离开后仅在本页面会话中保留。确定继续？')) return false
  const state = useEditorStore.getState()
  const pending = state.pendingEdits ?? {}
  if (!state.isDirty && !state.isLayoutDirty && Object.keys(pending).length === 0) return true
  if (!window.confirm('编辑器中有未保存或未应用的修改。确定放弃这些修改并离开吗？')) return false
  for (const key of Object.keys(pending)) state.setPendingEdit(key, null)
  state.reset({ force: true })
  return true
}

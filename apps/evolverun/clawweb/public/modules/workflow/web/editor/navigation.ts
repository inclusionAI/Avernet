import { useEditorStore } from './store'

/** Explicit discard only; ordinary selection and mutation never silently reset a draft. */
export function confirmDiscardEditor(): boolean {
  const state = useEditorStore.getState()
  const pending = state.pendingEdits ?? {}
  if (!state.isDirty && Object.keys(pending).length === 0) return true
  if (!window.confirm('编辑器中有未保存或未应用的修改。确定放弃这些修改并离开吗？')) return false
  for (const key of Object.keys(pending)) state.setPendingEdit(key, null)
  state.reset({ force: true })
  return true
}

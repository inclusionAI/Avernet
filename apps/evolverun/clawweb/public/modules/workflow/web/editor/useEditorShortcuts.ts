import { useEffect } from 'react'
import { editorIsReadOnly, useEditorStore } from './store'

/** UI affordances and browser shortcuts share the same write boundary. */
export function useEditorShortcuts({ canEdit, canSave, save, undo, redo, modalOpen }: {
  canEdit: boolean; canSave: boolean; save: () => void; undo: () => void; redo: () => void; modalOpen: boolean
}) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        if (canEdit && canSave && !editorIsReadOnly(useEditorStore.getState())) save()
      }
      if (modalOpen) return
      const target = event.target as HTMLElement | null
      if (target instanceof Element && target.closest('input, textarea, select, [contenteditable=true]')) return
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (!canEdit || editorIsReadOnly(useEditorStore.getState())) return
        if (event.shiftKey) redo?.(); else undo?.()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [canEdit, canSave, save, undo, redo, modalOpen])
}

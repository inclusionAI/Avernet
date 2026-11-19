import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import { useEffect, useRef, useState } from 'react';

/** Local visual undo is fenced by the current applied draft, not persisted remotely. */
export function useEditorVisualHistory(spec: EditorSpec, onChange: (next: EditorSpec) => void, blocked: boolean) {
  const [, redraw] = useState(0);
  const signature = JSON.stringify(spec);
  const history = useRef<{ signature: string; past: EditorSpec[]; future: EditorSpec[] }>({
    signature, past: [], future: [],
  });
  const matches = history.current.signature === signature;
  useEffect(() => {
    if (history.current.signature !== signature) history.current = { signature, past: [], future: [] };
  }, [signature]);
  const change = (next: EditorSpec) => {
    if (blocked || JSON.stringify(next) === signature) return;
    const past = matches ? history.current.past : [];
    onChange(next);
    history.current = { signature: JSON.stringify(next), past: [...past, spec].slice(-60), future: [] };
    redraw((value) => value + 1);
  };
  const travel = (direction: 'past' | 'future') => {
    if (blocked || !matches) return;
    const state = history.current;
    const list = state[direction];
    const next = list[list.length - 1];
    if (!next) return;
    onChange(next);
    history.current = direction === 'past'
      ? { signature: JSON.stringify(next), past: list.slice(0, -1), future: [...state.future, spec] }
      : { signature: JSON.stringify(next), past: [...state.past, spec], future: list.slice(0, -1) };
    redraw((value) => value + 1);
  };
  return {
    change,
    undo: () => travel('past'),
    redo: () => travel('future'),
    canUndo: !blocked && matches && history.current.past.length > 0,
    canRedo: !blocked && matches && history.current.future.length > 0,
  };
}

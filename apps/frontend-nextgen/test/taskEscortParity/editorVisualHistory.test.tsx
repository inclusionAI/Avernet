/** @jest-environment jsdom */
import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import { useEditorVisualHistory } from '@/hooks/taskEscortParity/useEditorVisualHistory';
import { act, renderHook } from '@testing-library/react';
import { useState } from 'react';
const initial: EditorSpec = { id: 'wf', title: 'Original', extension: { keep: true }, nodes: [] };
it('undoes and redoes applied visual snapshots without losing extensions or writing remotely', () => {
  const { result } = renderHook(() => {
    const [spec, setSpec] = useState(initial);
    return { spec, ...useEditorVisualHistory(spec, setSpec, false) };
  });
  act(() => result.current.change({ ...initial, title: 'Edited' }));
  expect(result.current.spec.title).toBe('Edited');
  expect(result.current.canUndo).toBe(true);
  act(() => result.current.undo());
  expect(result.current.spec).toEqual(initial);
  expect(result.current.canRedo).toBe(true);
  act(() => result.current.redo());
  expect(result.current.spec.title).toBe('Edited');
  expect(result.current.spec.extension).toEqual({ keep: true });
  act(() => result.current.undo());
  act(() => result.current.change({ ...initial, title: 'Different' }));
  expect(result.current.canRedo).toBe(false);
});
it('fences history after an external YAML/AI replacement and when editing is blocked', () => {
  const onChange = jest.fn();
  const { result, rerender } = renderHook(({ spec, blocked }) => useEditorVisualHistory(spec, onChange, blocked), {
    initialProps: { spec: initial, blocked: false },
  });
  const edited = { ...initial, title: 'Edited' };
  act(() => result.current.change(edited));
  rerender({ spec: edited, blocked: false });
  expect(result.current.canUndo).toBe(true);
  rerender({ spec: { ...initial, title: 'AI replacement' }, blocked: false });
  act(() => result.current.undo());
  expect(onChange).toHaveBeenCalledTimes(1);
  expect(result.current.canUndo).toBe(false);
  rerender({ spec: edited, blocked: true });
  act(() => result.current.change(initial));
  act(() => result.current.undo());
  expect(onChange).toHaveBeenCalledTimes(1);
});

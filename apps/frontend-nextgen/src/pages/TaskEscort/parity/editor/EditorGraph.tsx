import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import { ReactFlowProvider } from '@xyflow/react';
import VisualCanvas from './canvas/VisualCanvas';
export interface EditorGraphProps {
  spec: EditorSpec;
  selectedId: string | null;
  select: (id: string) => void;
  onChange?: (spec: EditorSpec) => void;
  disabled?: boolean;
  onUndo?: () => void;
  onRedo?: () => void;
  canUndo?: boolean;
  canRedo?: boolean;
}
export function EditorGraph(props: EditorGraphProps) {
  return (
    <ReactFlowProvider key={props.spec.id}>
      <VisualCanvas {...props} />
    </ReactFlowProvider>
  );
}

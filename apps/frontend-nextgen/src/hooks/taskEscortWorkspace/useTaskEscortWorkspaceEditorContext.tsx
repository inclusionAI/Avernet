import type { TaskEscortWorkspaceMode } from '@/domain/taskEscortWorkspace';
import type { TaskEscortEditorSession } from '@/services/taskEscortWorkspace/editorSession';
import { createEditorSession } from '@/services/taskEscortWorkspace/editorSession';
import React, { createContext, useContext, useMemo } from 'react';

const EditorSessionContext = createContext<TaskEscortEditorSession | null>(null);
export interface TaskEscortEditorProviderProps {
  children: React.ReactNode;
  workspaceKey: string;
  mode: TaskEscortWorkspaceMode;
  resetKey?: number | string;
}
/** Mount around ALL seven views; resetKey is the controller's resetVersion. Identity/mode changes clear memory. */
export function TaskEscortEditorProvider({
  children,
  workspaceKey,
  mode,
  resetKey = 0,
}: TaskEscortEditorProviderProps) {
  const session = useMemo(
    () => ({ workspaceKey, resetKey, service: createEditorSession(mode) }),
    [workspaceKey, mode, resetKey],
  );
  return <EditorSessionContext.Provider value={session.service}>{children}</EditorSessionContext.Provider>;
}
export function useTaskEscortWorkspaceEditorSession() {
  const session = useContext(EditorSessionContext);
  if (!session) throw new Error('TaskEscort editor requires TaskEscortEditorProvider around the workspace');
  return session;
}

/** Open workflow specs are extensible. Never normalize away unknown executor or node fields. */
export interface EditorNode extends Record<string, unknown> {
  id: string;
  title?: string;
  executor: Record<string, unknown> & { type: string };
  dependsOn?: string[];
}
export interface EditorSpec extends Record<string, unknown> {
  id: string;
  title: string;
  version?: string | number;
  nodes: EditorNode[];
}
export interface EditorAccess {
  workflowId: string;
  canView: boolean;
  canEdit: boolean;
}
export interface EditorRelease extends Record<string, unknown> {
  deployNumber: number;
  version: number;
  action: string;
  isActive?: boolean;
  gmtCreate?: number;
  note?: string | null;
}
export interface EditorHistory {
  workflowId: string;
  history: EditorRelease[];
  active: EditorRelease | null;
}
export interface EditorSnapshot extends EditorRelease {
  specJson: string;
}
export interface EditorDiff {
  workflowId: string;
  from: EditorSnapshot;
  to: EditorSnapshot;
}
export interface EditorScriptFile {
  path: string;
  content: string;
  mode: '100644' | '100755';
}
export interface EditorPack {
  workflowId: string;
  packId: string;
  baseRef: string;
  baseCommit: string;
  files: EditorScriptFile[];
}

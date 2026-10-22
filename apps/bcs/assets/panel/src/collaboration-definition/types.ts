export interface CollaborationDefinitionSnapshot {
  schemaVersion: 1;
  documentId: string;
  snapshotId: string;
  parentSnapshotId?: string;
  title?: string;
  definitionYaml: string;
}

export interface CollaborationDefinitionDiagnostic {
  code: string;
  path: string;
  message: string;
  hint?: string;
}

export interface CollaborationDefinitionSummary {
  participants: number;
  nodes: number;
  initial_nodes: string[];
  final_output_node?: string;
}

export interface CollaborationDefinitionParticipant {
  binding: string;
  display_name?: string;
  description?: string;
  required: boolean;
  assigned: boolean;
}

export interface CollaborationDefinitionAssignee {
  type: 'bot_binding' | 'runtime_actor';
  binding?: string;
  actor?: string;
}

export interface CollaborationDefinitionNodeExecution {
  definition_node_id: string;
  loop_id: string;
  iteration: number;
  max_iterations: number;
  loop_path?: Array<{
    loop_id: string;
    iteration: number;
    max_iterations: number;
  }>;
}

export interface CollaborationDefinitionGraphNode {
  node_id: string;
  display_name: string;
  kind: string;
  assignee?: CollaborationDefinitionAssignee;
  final_output: boolean;
  judge: boolean;
  execution?: CollaborationDefinitionNodeExecution;
}

export interface CollaborationDefinitionLoopRoute {
  kind: 'continue' | 'break' | 'exhausted';
  logical_outcome: string;
}

export interface CollaborationDefinitionGraphEdge {
  display_name?: string;
  source: string;
  target: string;
  outcome: string;
  loop_route?: CollaborationDefinitionLoopRoute;
}

export interface CollaborationDefinitionLoop {
  continue_display_name?: string;
  display_name: string;
  max_iterations: number;
  entry_node_id: string;
  result_node_id: string;
  body_node_ids: string[];
  continue_outcomes: string[];
  break_outcomes: string[];
  exhausted_outcome: string;
}

export interface CollaborationDefinitionGraph {
  graph_mode: string;
  execution_graph_mode?: 'acyclic';
  nodes: CollaborationDefinitionGraphNode[];
  edges: CollaborationDefinitionGraphEdge[];
  loops?: Record<string, CollaborationDefinitionLoop>;
}

export interface CollaborationDefinitionValidationResult {
  valid: boolean;
  errors: CollaborationDefinitionDiagnostic[];
  warnings: CollaborationDefinitionDiagnostic[];
  summary: CollaborationDefinitionSummary;
  participants: CollaborationDefinitionParticipant[];
  graph?: CollaborationDefinitionGraph;
}

export interface CollaborationDefinitionComponentProps {
  apiBaseUrl?: string;
  baseUrl?: string;
  data?: Record<string, any>;
  schemaVersion?: unknown;
  draftId?: unknown;
  revision?: unknown;
  readToken?: unknown;
  documentId?: unknown;
  snapshotId?: unknown;
  parentSnapshotId?: unknown;
  title?: unknown;
  definitionYaml?: unknown;
  snapshot?: unknown;
  sourceMessageId?: unknown;
  tabId?: unknown;
  params?: Record<string, any>;
  payload?: Record<string, any>;
}

export interface BcsPanelTab {
  id: string;
  title: string;
  type: string;
  params?: Record<string, any>;
  payload?: Record<string, any>;
  closable?: boolean;
}

export interface BcsPanelBridge {
  openPanelTab(tab: BcsPanelTab): void;
  closePanelTab(id: string): void;
  focusPanelTab?(id: string): void;
}

declare global {
  interface Window {
    aixBridge?: BcsPanelBridge;
    aixGetLibraryCdn?: (libraryName: string) => string | undefined;
    aixLibraryCdnMap?: Map<string, string>;
  }
}

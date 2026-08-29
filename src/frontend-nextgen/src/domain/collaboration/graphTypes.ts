/** 图模式取值（与 open-claw 后端对齐）。 */
export type CollaborationGraphMode = 'acyclic' | 'cyclic' | 'event_driven' | 'hierarchical';

/** 图节点 kind 取值。 */
export type CollaborationGraphNodeKind =
  | 'bot_task'
  | 'group_chat'
  | 'human_input'
  | 'tool_action'
  | 'sub_state_machine';

/** 图执行者分配信息。 */
export interface CollaborationDefinitionGraphAssignee {
  type: 'bot_binding' | 'runtime_actor';
  binding?: string;
  actor?: string;
}

/** 校验返回的图节点。 */
export interface CollaborationDefinitionGraphNode {
  node_id: string;
  kind: CollaborationGraphNodeKind;
  display_name: string;
  assignee?: CollaborationDefinitionGraphAssignee;
  final_output: boolean;
  judge: boolean;
}

/** 校验返回的图边。 */
export interface CollaborationDefinitionGraphEdge {
  source: string;
  target: string;
  outcome: string;
}

/** 校验返回的图预览。 */
export interface CollaborationDefinitionGraphPreview {
  graph_mode: CollaborationGraphMode;
  nodes: CollaborationDefinitionGraphNode[];
  edges: CollaborationDefinitionGraphEdge[];
}

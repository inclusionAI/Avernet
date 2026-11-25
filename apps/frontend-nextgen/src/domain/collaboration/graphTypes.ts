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
  execution?: {
    definition_node_id: string;
    loop_id: string;
    iteration: number;
    max_iterations: number;
    loop_path?: Array<{
      loop_id: string;
      iteration: number;
      max_iterations: number;
    }>;
  };
}

export interface CollaborationDefinitionLoopRoute {
  kind: 'continue' | 'break' | 'exhausted';
  logical_outcome: string;
}

/** 校验返回的图边。 */
export interface CollaborationDefinitionGraphEdge {
  source: string;
  target: string;
  outcome: string;
  display_name?: string;
  loop_route?: CollaborationDefinitionLoopRoute;
}

export interface StateMachineLoopGraphView {
  display_name: string;
  continue_display_name?: string;
  max_iterations: number;
  entry_node_id: string;
  result_node_id: string;
  body_node_ids: string[];
  continue_outcomes: string[];
  break_outcomes: string[];
  exhausted_outcome: string;
}

/** 校验返回的图预览。 */
export interface CollaborationDefinitionGraphPreview {
  graph_mode: CollaborationGraphMode;
  /** 服务端编译的执行图；预览节点 ID 不标识未来的 Run。 */
  execution_graph_mode?: 'acyclic';
  loops?: Record<string, StateMachineLoopGraphView>;
  nodes: CollaborationDefinitionGraphNode[];
  edges: CollaborationDefinitionGraphEdge[];
}

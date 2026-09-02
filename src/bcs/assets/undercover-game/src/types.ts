import type { CSSProperties } from 'react';

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type UndercoverPhase = 'lobby' | 'speaking' | 'voting' | 'reveal' | 'complete' | string;
export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'aborted' | string;
export type NodeStatus = 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'retry_scheduled' | 'skipped' | string;
export type NodeSubStatus = 'awaiting_response' | 'judging' | string;

export interface HostActor {
  actorId: string;
  displayName: string;
  subtitle?: string;
  avatar?: string;
}

export interface PlayerActor {
  actorId: string;
  displayName: string;
  isHuman?: boolean;
  alive?: boolean;
  eliminated?: boolean;
  voted?: boolean;
  state?: PlayerState;
  avatar?: string;
  publicFields?: Record<string, string | number | boolean>;
}

export type PlayerState =
  | 'waiting'
  | 'active_speech'
  | 'completed_speech'
  | 'waiting_for_vote'
  | 'voted'
  | 'eliminated'
  | 'retrying'
  | 'error';

export interface VoteCandidate {
  actorId: string;
  displayName: string;
  eligible: boolean;
  eliminated?: boolean;
}

export interface PublicDisplayFlags {
  showTimer?: boolean;
  showPublicReveal?: boolean;
  showVoteResults?: boolean;
  showHostOutput?: boolean;
}

export interface UndercoverGamePanelParams {
  runId: string;
  groupId: string;
  sessionId: string;
  gameSessionId?: string;
  phase: UndercoverPhase;
  round: number;
  deadlineAt?: number;
  host: HostActor;
  seatOrder: string[];
  players: PlayerActor[];
  nodeActorMap: Record<string, string>;
  voteCandidates?: VoteCandidate[];
  apiBaseUrl?: string;
  currentViewerActorId?: string;
  display?: PublicDisplayFlags;
  pollingInterval?: number;
  autoRefresh?: boolean;
  maxResponseBytes?: number;
}

export interface UndercoverGamePanelProps extends Partial<UndercoverGamePanelParams> {
  data?: Partial<UndercoverGamePanelParams>;
  className?: string;
  style?: CSSProperties;
  onInteraction?: (payload: UndercoverPanelInteraction) => void;
}

export interface UndercoverPanelInteraction {
  type: 'refresh' | 'select-actor' | 'select-bubble' | 'submit-human-input' | 'retry';
  actorId?: string;
  nodeId?: string;
  runId?: string;
}

export interface StateMachineRun {
  run_id: string;
  status: RunStatus;
  group_id?: string;
  session_id?: string;
  created_at?: number;
  updated_at?: number;
  completed_at?: number;
  [key: string]: unknown;
}

export interface StateMachineNode {
  node_id: string;
  display_name?: string;
  kind?: string;
  status?: NodeStatus;
  sub_status?: NodeSubStatus;
  attempt?: number;
  assignee_bot_id?: string;
  started_at?: number;
  completed_at?: number;
  error?: string;
  [key: string]: unknown;
}

export interface StateMachineRunGraph {
  run: StateMachineRun;
  definition?: { id?: string; version?: number; name?: string; initial_nodes?: string[] };
  nodes: StateMachineNode[];
  edges?: Array<{ source: string; target: string; outcome?: string }>;
}

export interface PendingHumanNode {
  node_id: string;
  display_name?: string;
  instruction?: string;
  response_ref?: string;
  timeout_deadline_ms?: number;
  [key: string]: unknown;
}

export interface StateMachineNodeDetailResponse {
  node: StateMachineNode & {
    run_id?: string;
    node_timeout_ms?: number;
    max_attempts?: number;
    delivery_request_id?: string;
    bot_delivery_run_id?: string;
    artifact_text?: string;
  };
  sub_status?: NodeSubStatus;
  judge_outputs?: Array<{ node_id: string; attempt?: number; created_at?: number; decision?: JsonValue }>;
}

export interface SessionMessage {
  id?: string;
  message_id?: string;
  created_at?: number;
  timestamp?: number;
  sequence?: number;
  sender?: string;
  sender_id?: string;
  role?: string;
  content?: unknown;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PublicOutputEvent {
  identity: string;
  runId: string;
  nodeId: string;
  attempt: number;
  actorId: string;
  text: string;
  pending: boolean;
  sequence?: number;
  timestamp?: number;
  messageId?: string;
}

export interface ActorViewModel {
  actor: PlayerActor | HostActor;
  kind: 'player' | 'host';
  seatIndex?: number;
  state: PlayerState | 'host';
  node?: StateMachineNode;
  latestOutput?: PublicOutputEvent;
  outputHistory: PublicOutputEvent[];
}

export interface UndercoverGameViewModel {
  params: UndercoverGamePanelParams;
  run?: StateMachineRun;
  status: RunStatus;
  terminal: boolean;
  phase: string;
  round: number;
  actors: ActorViewModel[];
  pendingHumanNode?: PendingHumanNode;
  pendingHumanActorId?: string;
  publicEvents: PublicOutputEvent[];
  lastUpdatedAt?: number;
}

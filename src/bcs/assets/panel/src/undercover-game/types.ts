import type { CSSProperties } from 'react';

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type UndercoverPhase = 'lobby' | 'speaking' | 'voting' | 'reveal' | 'complete' | string;
export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'aborted' | string;
export type NodeStatus = 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'retry_scheduled' | 'skipped' | string;
export type NodeSubStatus = 'awaiting_response' | 'judging' | string;

export interface HostActor { actorId: string; displayName: string; subtitle?: string; avatar?: string }
export interface PlayerActor { actorId: string; displayName: string; seatNumber: number; isHuman?: boolean; alive?: boolean; eliminated?: boolean; voted?: boolean; state?: PlayerState; avatar?: string; publicFields?: Record<string, string | number | boolean> }
export type PlayerState = 'waiting' | 'action_required' | 'active_speech' | 'completed_speech' | 'waiting_for_vote' | 'voted' | 'eliminated' | 'retrying' | 'error';
export interface CurrentAction { actorId: string; type: 'speech' | 'vote' | string; nodeId: string; deadlineAt?: number }
export interface VoteCandidate { actorId: string; displayName: string; seatNumber: number; eligible: boolean; eliminated?: boolean }
export interface PublicDisplayFlags { showTimer?: boolean; showPublicReveal?: boolean; showVoteResults?: boolean; showHostOutput?: boolean }
export interface PublicRules { speechMaxChars?: number; voteMaxChars?: number; forbidOwnWord?: boolean; bluntness?: number }
export interface PublicHistorySpeech { actorId: string; seatNumber: number; displayName: string; text: string }
export interface PublicHistoryRound { round: number; stage?: 'regular' | 'pk'; speeches: PublicHistorySpeech[] }

export interface PublicVoteRound {
  round: number; stage: 'regular' | 'pk';
  votes: Array<{ seatNumber: number; displayName: string; text: string }>;
  counts: Array<{ seatNumber: number; votes: number }>;
}

export interface UndercoverGamePanelParams {
  runId: string; groupId: string; sessionId: string; gameSessionId?: string; phase: UndercoverPhase; round: number; attempt: number;
  openingAnnouncement?: string; deadlineAt?: number; host: HostActor; seatOrder: string[]; turnOrder: string[]; players: PlayerActor[]; nodeActorMap: Record<string, string>;
  publicHistory: PublicHistoryRound[]; pkCandidates?: string[]; voteHistory?: PublicVoteRound[]; rules: PublicRules; voteCandidates?: VoteCandidate[]; apiBaseUrl?: string; currentViewerActorId?: string;
  currentAction?: CurrentAction; display?: PublicDisplayFlags; pollingInterval?: number; autoRefresh?: boolean; maxResponseBytes?: number;
  resultFile?: string;
}
export interface PanelAction { type: 'send_message'; content: string }
export interface UndercoverGamePanelProps extends Partial<UndercoverGamePanelParams> {
  data?: Partial<UndercoverGamePanelParams>; className?: string; style?: CSSProperties;
  onInteraction?: (payload: UndercoverPanelInteraction) => void;
  onAction?: (action: PanelAction) => unknown | Promise<unknown>;
}
export interface UndercoverPanelInteraction { type: 'refresh' | 'select-actor' | 'select-bubble' | 'submit-human-input' | 'retry' | 'recovery'; actorId?: string; nodeId?: string; runId?: string; action?: 'speech' | 'vote' | 'abstain' }
export interface StateMachineRun { run_id: string; status: RunStatus; group_id?: string; session_id?: string; created_at?: number; updated_at?: number; completed_at?: number; [key: string]: unknown }
export interface StateMachineNode { node_id: string; display_name?: string; kind?: string; status?: NodeStatus; sub_status?: NodeSubStatus; attempt?: number; assignee_bot_id?: string; started_at?: number; completed_at?: number; error?: string; [key: string]: unknown }
export interface StateMachineRunGraph { run: StateMachineRun; definition?: { id?: string; version?: number; name?: string; initial_nodes?: string[] }; nodes: StateMachineNode[]; edges?: Array<{ source: string; target: string; outcome?: string }> }
export interface UpstreamArtifact { node_id: string; text: string }
export interface PendingHumanNode { node_id: string; display_name?: string; instruction?: string; response_ref?: string; timeout_deadline_ms?: number; upstream_artifacts?: UpstreamArtifact[]; attempt?: number; [key: string]: unknown }
export interface StateMachineNodeDetailResponse { node: StateMachineNode & { run_id?: string; node_timeout_ms?: number; max_attempts?: number; delivery_request_id?: string; bot_delivery_run_id?: string; artifact_text?: string }; sub_status?: NodeSubStatus; judge_outputs?: Array<{ node_id: string; attempt?: number; created_at?: number; decision?: JsonValue }> }
export interface SessionMessage { id?: string; message_id?: string; created_at?: number; timestamp?: number; sequence?: number; sender?: string; sender_id?: string; role?: string; content?: unknown; metadata?: Record<string, unknown>; pending?: boolean; [key: string]: unknown }
export interface PublicOutputEvent { identity: string; runId: string; nodeId: string; attempt: number; actorId: string; text: string; pending: boolean; sequence?: number; timestamp?: number; messageId?: string; source?: 'session-message' | 'node-detail' }
export interface ActorViewModel { actor: PlayerActor | HostActor; kind: 'player' | 'host'; seatIndex?: number; state: PlayerState | 'host'; node?: StateMachineNode; latestOutput?: PublicOutputEvent; outputHistory: PublicOutputEvent[]; publicHistory: PublicHistorySpeech[] }
export interface UndercoverGameViewModel { params: UndercoverGamePanelParams; run?: StateMachineRun; status: RunStatus; terminal: boolean; phase: string; round: number; actors: ActorViewModel[]; pendingHumanNode?: PendingHumanNode; pendingHumanActorId?: string; publicEvents: PublicOutputEvent[]; completedTurns: number; totalTurns: number; lastUpdatedAt?: number }

export interface SpeechPrivateContext { version: 1; action: 'speech'; round: number; seatNumber: number; word?: string; maxChars: number; forbidOwnWord: boolean; bluntness?: number }
export interface VotePrivateContext { version: 1; action: 'vote'; round: number; seatNumber: number; word?: string; allowAbstain: boolean }
export type PrivateActionContext = SpeechPrivateContext | VotePrivateContext;
export interface PrivateContextParseResult { context?: PrivateActionContext; error?: string; present: boolean }
export interface SpeechValidationResult { valid: boolean; error?: 'empty' | 'too_long' | 'contains_own_word'; count: number }

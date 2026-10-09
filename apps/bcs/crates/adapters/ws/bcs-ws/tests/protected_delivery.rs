//! Task 16 RED suite: WS queueing, run fallback/replay, and the real
//! pre-send re-authorization (plan Task 16, spec §14.5/§17.2).
//!
//! Every fixture drives the REAL registry / run channels / dispatcher and a
//! recording application hook. Staging uses Notify/Barrier ordering — the
//! "blocked socket writer" is the not-yet-drained real queue, never a sleep.

mod common;

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{HumanMessageView, MessageAudience, MessageViewScope, MessageVisibilityDomain};
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::application::v1::delivery_authorization::DeliveryAction;
use bcs_service_api::application::v1::{
    ActorKind, AuthorizeGroupSessionConnection, AuthorizedGroupSessionConnection,
    GroupSessionConnectionBinding, GroupSessionConnectionError, GroupSessionConnectionService,
    IssueGroupSessionConnectionToken, IssuedGroupSessionConnectionToken, ParticipantRole,
    SessionParticipant, VerifyGroupSessionConnectionToken,
};
use bcs_service_api::port::ParticipantViewBindingPort;
use bcs_service_api::{
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryTarget,
    InteractionFrontendEvent, InteractionRequestedOutcome, InteractionService,
    InteractionServiceError, InteractionStatus, ProviderInteractionRequestedCommand,
    ProviderInteractionResolvedCommand, ResolveInteractionCommand, ResolveInteractionResult,
    RunFallbackDelivery, ServiceResult,
};
use bcs_ws::web::{
    WebClientConnectionState, WebDispatchState, WorkbenchConnectionAuth, dispatch_client_frame,
};
use bcs_test_support::{
    NoopCollaborationRuntimeService, NoopMessageFlowService, NoopWorkbenchSessionService,
};
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::protected_delivery::{ProtectedChannelRef, WorkbenchOutbound};
use bcs_ws::web::{WorkbenchConnectionRegistry, WorkbenchFrontendDelivery};
use common::{
    BindingFacts, ProtectedSocket, RecordingDeliveryAuthorization, chat_public,
    drain_into_socket,
};
use bcs_service_api::FrontendDeliveryPort;
use tokio::sync::mpsc;

fn registry_with_hook() -> (Arc<WorkbenchConnectionRegistry>, Arc<RecordingDeliveryAuthorization>) {
    let registry = Arc::new(WorkbenchConnectionRegistry::new());
    let hook = RecordingDeliveryAuthorization::new();
    registry.set_delivery_authorization(hook.clone());
    (registry, hook)
}

/// Brief Step 1, block 1 (verbatim variables): a frame legally enqueued for
/// two views of one User, the X view revoked "from another instance" while
/// the socket writer is blocked, then the writer released.
#[tokio::test]
async fn revoked_view_drops_queued_frame_before_send_and_keeps_neighbor_binding() {
    let (registry, hook) = registry_with_hook();
    let gate = registry.protected_delivery();
    let session = "session-task16-xy";

    let mut x = ProtectedSocket::connect(
        &registry,
        &hook,
        session,
        "user-u",
        "bot-view-x",
        BindingFacts::full(),
    )
    .await;
    let mut y = ProtectedSocket::connect(
        &registry,
        &hook,
        session,
        "user-u",
        "bot-view-y",
        BindingFacts::full(),
    )
    .await;

    // Writer deliberately not draining: stage the backlog first.
    let (chat, audience) = chat_public();
    assert_eq!(
        registry
            .broadcast_visible_to_actors(
                session,
                "task16-e0x",
                &["bot-view-x".to_string()],
                None,
                chat,
                audience.as_ref(),
            )
            .await,
        1
    );
    assert_eq!(
        registry
            .broadcast_visible_excluding(session, "task16-e1", chat, audience.as_ref(), None)
            .await,
        2,
        "both Deliver decisions enqueue at position 1 while the binding is still valid"
    );

    // Another instance commits the X revoke while the writer stays blocked.
    hook.revoke("user-u", "bot-view-x", session);
    let reads_before_drain_x = hook.authorize_reads_for("user-u", "bot-view-x", session);

    // Drain phase 1: the queued e0x frame re-authorizes before the send,
    // observes the committed revoke, drops the frame and invalidates X.
    drain_into_socket(&mut x.rx, gate.clone(), x.socket_tx.clone(), 1).await;
    let x_binding_invalidated = !gate.binding_active(x.binding_id);
    assert!(x_binding_invalidated);
    let reads_after_close_x = hook.authorize_reads_for("user-u", "bot-view-x", session);
    assert_eq!(reads_after_close_x, reads_before_drain_x + 1);

    // Drain phase 2: X's remaining queued protected frame (e1's copy) is
    // dropped as dead-binding backlog with no further authority read.
    drain_into_socket(&mut x.rx, gate.clone(), x.socket_tx.clone(), 1).await;
    let authority_reads_after_binding_closed = hook
        .authorize_reads_for("user-u", "bot-view-x", session)
        - reads_after_close_x;
    assert_eq!(authority_reads_after_binding_closed, 0);

    // The neighbor binding still drains normally.
    drain_into_socket(&mut y.rx, gate.clone(), y.socket_tx.clone(), 1).await;

    let mut x_socket = x.socket;
    let mut y_socket = y.socket;
    let x_socket_protected_frames_after_revoke = socket_count(&mut x_socket).len();
    let y_socket_frames = socket_count(&mut y_socket);
    let y_socket_protected_frames_after_revoke = y_socket_frames.len();
    assert_eq!(x_socket_protected_frames_after_revoke, 0);
    assert_eq!(y_socket_protected_frames_after_revoke, 1);
    assert!(y_socket_frames[0].contains("task16-e1"));
    let y_binding_invalidated = !gate.binding_active(y.binding_id);
    assert!(!y_binding_invalidated);

    // A denied binding must not be re-sent through the run fallback lane:
    // the fallback itself re-authorizes the binding's true identity context.
    let run_channels = Arc::new(RunChannelManager::new());
    run_channels
        .register_workbench_with_view(
            "run-x".to_string(),
            session.to_string(),
            x.tx.clone(),
            None,
            Some("bot-view-x".to_string()),
            None,
            Some(ProtectedChannelRef {
                user_id: "user-u".to_string(),
                view_actor_id: "bot-view-x".to_string(),
                tenant: Some("tenant-test".to_string()),
                env: "env-test".to_string(),
                resource_kind: bcs_service_api::application::v1::delivery_authorization::DeliveryResourceKind::Session,
                resource_id: session.to_string(),
                binding_id: x.binding_id,
                gate: gate.clone(),
            }),
        )
        .await;
    let delivery = WorkbenchFrontendDelivery::new(registry.clone(), run_channels.clone());
    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: "session-task16-unbound".to_string(),
            },
            event_json: "task16-fallback".to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: Some(RunFallbackDelivery {
                run_id: "run-x".to_string(),
                session_id: session.to_string(),
                event_json: "task16-fallback".to_string(),
            }),
            exclude_conn_id: None,
            visibility_domain: chat,
            audience: audience.clone(),
        })
        .await
        .unwrap();
    assert_eq!(result.delivered, 0);
    let fallback_frames_for_denied_x = socket_count(&mut x_socket).len();
    assert_eq!(fallback_frames_for_denied_x, 0);

    // Cost shape (spec §17.2): enqueue batches of e0x/e1, the per-frame
    // dequeue rechecks, and TWO redispatch reads for the fallback attempt
    // (the run hop and the session hop are separate re-dispatches, each
    // independently re-authorizing) — all bounded, none reused.
    assert_eq!(hook.batch_sizes(), vec![1, 2, 1, 1, 1, 1]);
}

/// Brief Step 1, block 2 (verbatim variables): a valid Participant binding
/// only skips the frames it may not see and keeps delivering.
#[tokio::test]
async fn skip_message_drops_only_hidden_frame_and_binding_survives() {
    let (registry, hook) = registry_with_hook();
    let gate = registry.protected_delivery();
    let session = "session-task16-p";
    let mut p = ProtectedSocket::connect(
        &registry,
        &hook,
        session,
        "user-p",
        "bot-view-p",
        BindingFacts::participant(),
    )
    .await;

    let (chat, audience) = chat_public();
    // FullOnly artifact outside the Participant scope: SkipMessage, not a
    // delivery failure, not an invalidation.
    assert_eq!(
        registry
            .broadcast_visible_excluding(
                session,
                "task16-hidden-fullonly",
                MessageVisibilityDomain::ManagerWorker,
                Some(&MessageAudience::FullOnly),
                None,
            )
            .await,
        0
    );
    // A ManagerWorker artifact directed at another actor: SkipMessage as
    // well (the canonical matrix treats the Chat domain as public to any
    // participant view, so the hidden-pair fixtures use ManagerWorker).
    assert_eq!(
        registry
            .broadcast_visible_excluding(
                session,
                "task16-hidden-directed-other",
                MessageVisibilityDomain::ManagerWorker,
                Some(&MessageAudience::Directed {
                    actor_ids: vec!["someone-else".to_string()],
                }),
                None,
            )
            .await,
        0
    );
    let valid_participant_hidden_frames = socket_count(&mut p.socket).len();
    assert_eq!(valid_participant_hidden_frames, 0);
    assert!(
        gate.binding_active(p.binding_id),
        "SkipMessage must never mutate the binding"
    );

    // The next Public frame and the own-Directed frame still deliver.
    assert_eq!(
        registry
            .broadcast_visible_excluding(session, "task16-public-next", chat, audience.as_ref(), None)
            .await,
        1
    );
    assert_eq!(
        registry
            .broadcast_visible_excluding(
                session,
                "task16-directed-own",
                MessageVisibilityDomain::ManagerWorker,
                Some(&MessageAudience::Directed {
                    actor_ids: vec!["bot-view-p".to_string()],
                }),
                None,
            )
            .await,
        1
    );
    drain_into_socket(&mut p.rx, gate.clone(), p.socket_tx.clone(), 2).await;
    let valid_participant_next_public_frames = socket_count(&mut p.socket)
        .into_iter()
        .filter(|frame| frame.contains("task16-public-next"))
        .count();
    assert_eq!(valid_participant_next_public_frames, 1);
    let valid_participant_binding_invalidated = !gate.binding_active(p.binding_id);
    assert!(!valid_participant_binding_invalidated);

    // A skipped frame is not "no connection": no fallback re-send for it.
    let run_channels = Arc::new(RunChannelManager::new());
    run_channels
        .register_workbench_with_view(
            "run-p".to_string(),
            session.to_string(),
            p.tx.clone(),
            None,
            Some("bot-view-p".to_string()),
            None,
            Some(ProtectedChannelRef {
                user_id: "user-p".to_string(),
                view_actor_id: "bot-view-p".to_string(),
                tenant: Some("tenant-test".to_string()),
                env: "env-test".to_string(),
                resource_kind: bcs_service_api::application::v1::delivery_authorization::DeliveryResourceKind::Session,
                resource_id: session.to_string(),
                binding_id: p.binding_id,
                gate: gate.clone(),
            }),
        )
        .await;
    let delivery = WorkbenchFrontendDelivery::new(registry.clone(), run_channels.clone());
    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Group {
                group_id: "session-task16-p-unbound".to_string(),
            },
            event_json: "task16-skip-fallback".to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: Some(RunFallbackDelivery {
                run_id: "run-p".to_string(),
                session_id: session.to_string(),
                event_json: "task16-skip-fallback".to_string(),
            }),
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(MessageAudience::FullOnly),
        })
        .await
        .unwrap();
    assert_eq!(result.delivered, 0);
    let fallback_frames_for_skipped_message = socket_count(&mut p.socket)
        .into_iter()
        .filter(|frame| frame.contains("task16-skip-fallback"))
        .count();
    assert_eq!(fallback_frames_for_skipped_message, 0);
}

/// The adapter splits K > 128 enqueue target sets itself into ceil(K/128)
/// bounded calls; no target of a 129-context event rides an oversized batch.
#[tokio::test]
async fn enqueue_batch_splits_into_bounded_chunks_of_128() {
    let (registry, hook) = registry_with_hook();
    let gate = registry.protected_delivery();
    let (chat, audience) = chat_public();

    let mut one = Vec::new();
    let mut hundred_twenty_eight = Vec::new();
    let mut hundred_twenty_nine = Vec::new();
    for i in 0..258usize {
        let (session, bucket) = if i == 0 {
            ("session-task16-chunk1", &mut one)
        } else if i < 129 {
            ("session-task16-chunk128", &mut hundred_twenty_eight)
        } else {
            ("session-task16-chunk129", &mut hundred_twenty_nine)
        };
        bucket.push(
            ProtectedSocket::connect(
                &registry,
                &hook,
                session,
                &format!("user-{i}"),
                &format!("bot-view-{i}"),
                BindingFacts::full(),
            )
            .await,
        );
    }

    // Same-event batches: 1, 128 and 129 distinct bound targets. The
    // adapter splits 129 into ceil(129/128) = 2 bounded chunks itself.
    assert_eq!(
        registry
            .broadcast_visible_excluding(
                "session-task16-chunk1",
                "task16-mass",
                chat,
                audience.as_ref(),
                None,
            )
            .await,
        1
    );
    assert_eq!(
        registry
            .broadcast_visible_excluding(
                "session-task16-chunk128",
                "task16-mass",
                chat,
                audience.as_ref(),
                None,
            )
            .await,
        128
    );
    assert_eq!(
        registry
            .broadcast_visible_excluding(
                "session-task16-chunk129",
                "task16-mass",
                chat,
                audience.as_ref(),
                None,
            )
            .await,
        129
    );

    assert_eq!(hook.batch_sizes(), vec![1, 128, 128, 1]);
    // The tail chunk's last target was judged on its own, never defaulted to
    // authorized: its binding still delivers through the writer recheck.
    let mut last = hundred_twenty_nine.pop().unwrap();
    drain_into_socket(&mut last.rx, gate.clone(), last.socket_tx.clone(), 1).await;
    assert_eq!(socket_count(&mut last.socket), vec!["task16-mass".to_string()]);
    // And the 128-boundary target of the big snapshot equally.
    let mut boundary = hundred_twenty_eight.pop().unwrap();
    drain_into_socket(&mut boundary.rx, gate.clone(), boundary.socket_tx.clone(), 1).await;
    assert_eq!(socket_count(&mut boundary.socket), vec!["task16-mass".to_string()]);
}

/// Frames queued for one binding are delivered contiguously and in enqueue
/// order once the writer is released.
#[tokio::test]
async fn queued_frames_stay_ordered_and_contiguous_under_backlog() {
    let (registry, hook) = registry_with_hook();
    let gate = registry.protected_delivery();
    let session = "session-task16-order";
    let mut socket = ProtectedSocket::connect(
        &registry,
        &hook,
        session,
        "user-o",
        "bot-view-o",
        BindingFacts::full(),
    )
    .await;

    let (chat, audience) = chat_public();
    for i in 0..5 {
        assert_eq!(
            registry
                .broadcast_visible_excluding(
                    session,
                    &format!("task16-order-{i}"),
                    chat,
                    audience.as_ref(),
                    None,
                )
                .await,
            1
        );
    }
    drain_into_socket(&mut socket.rx, gate.clone(), socket.socket_tx.clone(), 5).await;
    let drained: Vec<String> = socket_count(&mut socket.socket);
    let expected: Vec<String> = (0..5)
        .map(|i| format!("task16-order-{i}"))
        .collect();
    assert_eq!(drained, expected);
}

struct ReplayGroupSessionConnections {
    user_actor: String,
}

#[async_trait]
impl GroupSessionConnectionService for ReplayGroupSessionConnections {
    async fn issue_token(
        &self,
        _command: IssueGroupSessionConnectionToken,
    ) -> Result<IssuedGroupSessionConnectionToken, GroupSessionConnectionError> {
        unreachable!("token issuance is out of scope for the replay fixture")
    }

    async fn verify_token(
        &self,
        _command: VerifyGroupSessionConnectionToken,
    ) -> Result<GroupSessionConnectionBinding, GroupSessionConnectionError> {
        unreachable!("token verification is out of scope for the replay fixture")
    }

    async fn authorize_connect(
        &self,
        _command: AuthorizeGroupSessionConnection,
    ) -> Result<AuthorizedGroupSessionConnection, GroupSessionConnectionError> {
        Ok(AuthorizedGroupSessionConnection {
            participants: vec![SessionParticipant {
                actor_id: self.user_actor.clone(),
                actor_kind: ActorKind::Human,
                name: Some("Replay Human".to_string()),
                role: ParticipantRole::Observer,
                tags: Vec::new(),
                mode: bcs_service_api::ParticipantMode::Present,
                message_view_scope: MessageViewScope::Full,
                joined_at: None,
            }],
        })
    }
}

struct ReplayInteractions {
    pending: Vec<InteractionFrontendEvent>,
}

#[async_trait]
impl InteractionService for ReplayInteractions {
    async fn on_provider_requested(
        &self,
        _command: ProviderInteractionRequestedCommand,
    ) -> ServiceResult<InteractionRequestedOutcome> {
        Ok(InteractionRequestedOutcome::Duplicate)
    }

    async fn on_provider_resolved(
        &self,
        _command: ProviderInteractionResolvedCommand,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn resolve(
        &self,
        command: ResolveInteractionCommand,
    ) -> Result<ResolveInteractionResult, InteractionServiceError> {
        Ok(ResolveInteractionResult {
            accepted: true,
            interaction_id: command.interaction_id,
            status: InteractionStatus::Accepted,
            idempotency_key: command.idempotency_key,
        })
    }

    async fn list_pending(
        &self,
        _bcs_session_id: &str,
    ) -> ServiceResult<Vec<InteractionFrontendEvent>> {
        Ok(self.pending.clone())
    }

    async fn invalidate_run(
        &self,
        _bcs_run_id: &str,
        _reason: &str,
        _invalidated_at_ms: u64,
    ) -> ServiceResult<usize> {
        Ok(0)
    }

    async fn cleanup_terminal(&self, _terminal_before_ms: u64) -> ServiceResult<usize> {
        Ok(0)
    }
}

async fn replay_dispatch_state(
    registry: Arc<WorkbenchConnectionRegistry>,
    pending: Vec<InteractionFrontendEvent>,
    user_actor: &str,
) -> Arc<WebDispatchState> {
    Arc::new(WebDispatchState {
        message_flow: Arc::new(NoopMessageFlowService),
        collaboration_runtime: Arc::new(NoopCollaborationRuntimeService),
        workbench_sessions: Arc::new(NoopWorkbenchSessionService),
        interactions: Arc::new(ReplayInteractions { pending }),
        group_session_connections: Some(Arc::new(ReplayGroupSessionConnections {
            user_actor: user_actor.to_string(),
        })),
        frontend_connections: registry,
        run_channels: Arc::new(RunChannelManager::new()),
    })
}

fn session_bound_auth(group_id: &str, session_id: &str) -> WorkbenchConnectionAuth {
    WorkbenchConnectionAuth::SessionBound {
        tenant: Some("tenant-replay".to_string()),
        actor_id: "human_100001".to_string(),
        group_id: group_id.to_string(),
        session_id: session_id.to_string(),
    }
}

fn connect_frame() -> String {
    serde_json::to_string(&BcsFrame::Request(RequestFrame::new(
        "connect-replay",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-replay",
            "session_id": "group-replay:s1",
        })),
    )))
    .unwrap()
}

fn replay_event() -> InteractionFrontendEvent {
    InteractionFrontendEvent {
        group_id: "group-replay".to_string(),
        bot_id: "bot-replay".to_string(),
        bcs_run_id: "run-replay".to_string(),
        bcs_session_id: "group-replay:s1".to_string(),
        payload: serde_json::json!({"interactionId": "replay-1"}),
    }
}

/// Interaction replay is a NEW dispatch: enqueued as a Protected
/// `ReplayFrame` carrying the REAL User, and re-authorized before the send.
#[tokio::test]
async fn interaction_replay_is_reauthorized_as_protected_dispatch() {
    let (registry, hook) = registry_with_hook();
    let gate = registry.protected_delivery();
    let session = "group-replay:s1";
    hook.register("100001", "human_100001", session, BindingFacts::full());

    let state = replay_dispatch_state(registry.clone(), vec![replay_event()], "human_100001").await;
    let (tx, mut rx) = mpsc::channel::<WorkbenchOutbound>(64);
    let mut connection_state = WebClientConnectionState::default();
    dispatch_client_frame(
        &state,
        &connect_frame(),
        &tx,
        &mut connection_state,
        &session_bound_auth("group-replay", session),
    )
    .await
    .unwrap();

    let _ack = rx.recv().await.expect("connect ack");
    let replay = rx.recv().await.expect("replayed interaction queued as Protected");
    match replay {
        WorkbenchOutbound::Protected {
            context,
            binding_id,
            ..
        } => {
            assert_eq!(context.user_id, "100001", "the REAL User, not the registry actor");
            assert_eq!(context.view_actor_id, "human_100001");
            assert_eq!(context.resource_id, session);
            assert_eq!(context.action, DeliveryAction::ReplayFrame);
            assert_ne!(binding_id, 0);
            let decision = gate
                .authorize_dequeued(binding_id, &context, "replay-payload".to_string())
                .await;
            assert!(matches!(decision, bcs_ws::web::protected_delivery::DequeuedDecision::Send(_)));
        }
        other => panic!("expected a Protected replay frame, got {other:?}"),
    }

    // Revoked between connect-replay and the writer's pre-send check: the
    // replay frame is dropped, the binding invalidated, no fallback.
    let state2 = replay_dispatch_state(registry.clone(), vec![replay_event()], "human_100001").await;
    let (tx2, mut rx2) = mpsc::channel::<WorkbenchOutbound>(64);
    let mut connection_state2 = WebClientConnectionState::default();
    dispatch_client_frame(
        &state2,
        &connect_frame(),
        &tx2,
        &mut connection_state2,
        &session_bound_auth("group-replay", session),
    )
    .await
    .unwrap();
    let _ack2 = rx2.recv().await.expect("connect ack");
    let replay2 = rx2.recv().await.expect("replay still enqueues for the fresh binding");
    let (binding_id2, context2, payload2) = match replay2 {
        WorkbenchOutbound::Protected {
            payload,
            context,
            binding_id,
        } => (binding_id, context, payload),
        other => panic!("expected a Protected replay frame, got {other:?}"),
    };
    assert!(payload2.contains("replay-1"));
    hook.revoke("100001", "human_100001", session);
    let reads_before = hook.authorize_reads_for("100001", "human_100001", session);
    let decision = gate
        .authorize_dequeued(binding_id2, &context2, payload2.clone())
        .await;
    assert!(matches!(
        decision,
        bcs_ws::web::protected_delivery::DequeuedDecision::Drop
    ));
    assert!(!gate.binding_active(binding_id2));
    let authority_reads_after_binding_closed = hook
        .authorize_reads_for("100001", "human_100001", session)
        - reads_before
        - 1;
    assert_eq!(authority_reads_after_binding_closed, 0);
}

/// Run lanes carry the REAL User identity, never the registry's legacy
/// actor field, and a retired binding never re-enqueues stale frames.
#[tokio::test]
async fn run_lane_carries_real_identity_and_replaced_binding_never_receives() {
    let (registry, hook) = registry_with_hook();
    let gate = registry.protected_delivery();
    let session = "session-task16-run";
    // The registry's legacy `user_id` slot holds the SELECTED VIEW actor —
    // deliberately NOT the real operator.
    let mut socket = ProtectedSocket::connect(
        &registry,
        &hook,
        session,
        "user-real",
        "bot-view-selected",
        BindingFacts::full(),
    )
    .await;

    let run_channels = Arc::new(RunChannelManager::new());
    run_channels
        .register_workbench_with_view(
            "run-f".to_string(),
            session.to_string(),
            socket.tx.clone(),
            None,
            Some("bot-view-selected".to_string()),
            None,
            Some(ProtectedChannelRef {
                user_id: "user-real".to_string(),
                view_actor_id: "bot-view-selected".to_string(),
                tenant: Some("tenant-test".to_string()),
                env: "env-test".to_string(),
                resource_kind: bcs_service_api::application::v1::delivery_authorization::DeliveryResourceKind::Session,
                resource_id: session.to_string(),
                binding_id: socket.binding_id,
                gate: gate.clone(),
            }),
        )
        .await;

    let delivery = WorkbenchFrontendDelivery::new(registry.clone(), run_channels.clone());
    let (chat, audience) = chat_public();
    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Run {
                run_id: "run-f".to_string(),
            },
            event_json: "task16-run-event".to_string(),
            delivery_kind: FrontendDeliveryKind::RunEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: chat,
            audience,
        })
        .await
        .unwrap();
    assert_eq!(result.delivered, 1);

    let item = socket.rx.recv().await.expect("protected item queued");
    match item {
        WorkbenchOutbound::Protected {
            context,
            binding_id,
            ..
        } => {
            assert_eq!(context.user_id, "user-real");
            assert_eq!(context.view_actor_id, "bot-view-selected");
            assert_eq!(binding_id, socket.binding_id);
        }
        other => panic!("expected a Protected run frame, got {other:?}"),
    }

    // Replacement: the old binding retires; a further run dispatch to the
    // same channel re-authorization check drops the frame before enqueue —
    // nothing is queued for the replaced connection to "receive".
    registry.unsubscribe(session, socket.conn_id).await;
    assert!(!gate.binding_active(socket.binding_id));
    let result = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Run {
                run_id: "run-f".to_string(),
            },
            event_json: "task16-run-stale".to_string(),
            delivery_kind: FrontendDeliveryKind::RunEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::Chat,
            audience:Some(MessageAudience::Public),
        })
        .await
        .unwrap();
    assert_eq!(result.delivered, 0);
    assert_eq!(socket_count(&mut socket.socket).len(), 0);
}

/// begin_scope_change keeps its existing close semantics and retires the
/// protected binding: the close event still goes out, the protected backlog
/// is dropped, a new connection after the change is accepted.
#[tokio::test]
async fn scope_change_retires_binding_and_replaces_connection() {
    let (registry, hook) = registry_with_hook();
    let gate = registry.protected_delivery();
    let session = "session-task16-scope";
    let (tx, mut rx) = mpsc::channel::<WorkbenchOutbound>(64);
    let (socket_tx, mut socket) = mpsc::channel::<String>(64);
    let binding = bcs_ws::web::protected_delivery::ProtectedDeliveryBinding {
        tenant: None,
        env: "env-test".to_string(),
        user_id: "user-s".to_string(),
        resource_kind: bcs_service_api::application::v1::delivery_authorization::DeliveryResourceKind::Session,
        resource_id: session.to_string(),
        view_actor_id: "human_user-s".to_string(),
    };
    let (conn_id, binding_id) = registry
        .subscribe_bound(
            session.to_string(),
            tx,
            Some("human_user-s".to_string()),
            Some(HumanMessageView {
                actor_id: "human_user-s".to_string(),
                scope: MessageViewScope::Participant,
                allow_legacy_unclassified_chat: true,
            }),
            Some(binding),
            tokio_util::sync::CancellationToken::new(),
        )
        .await
        .unwrap();
    let _ = conn_id;
    hook.register("user-s", "human_user-s", session, BindingFacts::full());

    let (chat, audience) = chat_public();
    assert_eq!(
        registry
            .broadcast_visible_excluding(session, "task16-sc-e1", chat, audience.as_ref(), None)
            .await,
        1
    );

    let lease = registry
        .begin_scope_change(session, "human_user-s")
        .await
        .unwrap();
    assert!(registry.scope_change_in_progress(session, "human_user-s").await);
    // A concurrent protected subscribe for the same actor must fail under the barrier.
    let (tx2, _rx2) = mpsc::channel::<WorkbenchOutbound>(8);
    let conflicting = bcs_ws::web::protected_delivery::ProtectedDeliveryBinding {
        tenant: None,
        env: "env-test".to_string(),
        user_id: "user-s".to_string(),
        resource_kind: bcs_service_api::application::v1::delivery_authorization::DeliveryResourceKind::Session,
        resource_id: session.to_string(),
        view_actor_id: "human_user-s".to_string(),
    };
    assert!(registry
        .subscribe_bound(
            session.to_string(),
            tx2,
            Some("human_user-s".to_string()),
            Some(HumanMessageView {
                actor_id: "human_user-s".to_string(),
                scope: MessageViewScope::Participant,
                allow_legacy_unclassified_chat: true,
            }),
            Some(conflicting.clone()),
            tokio_util::sync::CancellationToken::new(),
        )
        .await
        .is_err());
    assert!(!gate.binding_active(binding_id));

    let reads_before = hook.authorize_reads_for("user-s", "human_user-s", session);
    drain_into_socket(&mut rx, gate.clone(), socket_tx, 2).await;
    let frames = socket_count(&mut socket);
    assert_eq!(frames.len(), 1, "only the public close event is flushed");
    assert!(frames[0].contains("view_scope_changed"));
    let authority_reads_after_binding_closed =
        hook.authorize_reads_for("user-s", "human_user-s", session) - reads_before;
    assert_eq!(authority_reads_after_binding_closed, 0);

    registry.finish_scope_change(lease).await.unwrap();
    let (tx3, _rx3) = mpsc::channel::<WorkbenchOutbound>(8);
    assert!(registry
        .subscribe_bound(
            session.to_string(),
            tx3,
            Some("human_user-s".to_string()),
            None,
            Some(conflicting),
            tokio_util::sync::CancellationToken::new(),
        )
        .await
        .is_ok());
}

/// Persistent authority-read failures fail the whole enqueue batch closed,
/// never queue a partially verified target, and stop querying per frame.
#[tokio::test]
async fn persistent_authority_failure_fails_batch_closed_and_stops_querying() {
    let (registry, hook) = registry_with_hook();
    let gate = registry.protected_delivery();
    let session = "session-task16-db";
    let mut a = ProtectedSocket::connect(
        &registry,
        &hook,
        session,
        "user-a",
        "bot-view-a",
        BindingFacts::full(),
    )
    .await;
    let mut b = ProtectedSocket::connect(
        &registry,
        &hook,
        session,
        "user-b",
        "bot-view-b",
        BindingFacts::full(),
    )
    .await;

    hook.arm_err();
    let (chat, audience) = chat_public();
    let reads_at_start = hook.total_authorize_reads();
    assert_eq!(
        registry
            .broadcast_visible_excluding(session, "task16-db-e1", chat, audience.as_ref(), None)
            .await,
        0
    );
    assert!(!gate.binding_active(a.binding_id));
    assert!(!gate.binding_active(b.binding_id));

    // Repeated pushes stay bounded: one enqueue batch read per event, no
    // per-frame retry loop for a closed binding.
    for _ in 0..3 {
        assert_eq!(
            registry
                .broadcast_visible_excluding(session, "task16-db-e2", chat, audience.as_ref(), None)
                .await,
            0
        );
    }
    let reads = hook.total_authorize_reads() - reads_at_start;
    assert_eq!(reads, 4, "one batch read per push; the backlog itself reads nothing");
    assert!(!gate.binding_active(a.binding_id));
    assert_eq!(socket_count(&mut a.socket).len(), 0);
    assert_eq!(socket_count(&mut b.socket).len(), 0);
    assert!(matches!(
        hook.batch_sizes().as_slice(),
        &[2, 2, 2, 2]
    ));
}

/// A slow in-flight authorization check must not hold the registry's
/// session lock and starve unrelated connections' dispatch.
#[tokio::test]
async fn paused_authorization_does_not_starve_other_sessions() {
    let (registry, hook) = registry_with_hook();
    let gate = registry.protected_delivery();
    let session_a = "session-task16-slow-a";
    let session_b = "session-task16-slow-b";
    let mut a = ProtectedSocket::connect(
        &registry,
        &hook,
        session_a,
        "user-sa",
        "bot-view-sa",
        BindingFacts::full(),
    )
    .await;
    let mut b = ProtectedSocket::connect(
        &registry,
        &hook,
        session_b,
        "user-sb",
        "bot-view-sb",
        BindingFacts::full(),
    )
    .await;

    let (entry, resume) = hook.arm_pause();
    let registry_a = registry.clone();
    let paused_broadcast = tokio::spawn(async move {
        let (chat, audience) = chat_public();
        registry_a
            .broadcast_visible_excluding("session-task16-slow-a", "task16-slow", chat, audience.as_ref(), None)
            .await
    });
    // Deterministic: wait until the authorize call is pinned in-flight.
    let notified = tokio::time::timeout(std::time::Duration::from_secs(30), entry.notified()).await;
    assert!(notified.is_ok());

    // Session B's full enqueue-and-drain cycle completes while A's authorize
    // call is still in flight — the lock was released before the batch read.
    let (chat, audience) = chat_public();
    assert_eq!(
        registry
            .broadcast_visible_excluding(session_b, "task16-slow-b", chat, audience.as_ref(), None)
            .await,
        1
    );
    drain_into_socket(&mut b.rx, gate.clone(), b.socket_tx.clone(), 1).await;
    assert_eq!(socket_count(&mut b.socket), vec!["task16-slow-b".to_string()]);
    assert!(matches!(paused_broadcast.is_finished(), false));

    resume.notify_one();
    assert_eq!(paused_broadcast.await.unwrap(), 1);
    drain_into_socket(&mut a.rx, gate.clone(), a.socket_tx.clone(), 1).await;
    assert_eq!(socket_count(&mut a.socket), vec!["task16-slow".to_string()]);
}

fn socket_count(socket: &mut mpsc::Receiver<String>) -> Vec<String> {
    let mut seen = Vec::new();
    while let Ok(payload) = socket.try_recv() {
        seen.push(payload);
    }
    seen
}
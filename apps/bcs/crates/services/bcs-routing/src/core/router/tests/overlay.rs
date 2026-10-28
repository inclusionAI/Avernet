use super::*;

#[tokio::test]
async fn test_dm_human_to_bot_routes_to_other_actor_with_send() {
    let router = MessageRouter::new();
    let session = create_human_bot_dm_session();
    let overlay = vec![
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
    ];

    let decision = router
        .route_dm_with_overlay(&session, "@human_alice ignored", "human_alice", &overlay)
        .await;

    assert_eq!(decision.targets.len(), 1);
    assert_eq!(decision.targets[0].bot_uuid, "bot_x");
    assert_eq!(decision.targets[0].delivery_type, DeliveryType::Send);
    assert_eq!(decision.mentions, vec!["human_alice".to_string()]);
    assert_eq!(decision.cleaned_message, "human_alice ignored");
}

#[tokio::test]
async fn test_dm_human_to_muted_or_hidden_bot_is_inject() {
    let router = MessageRouter::new();
    let session = create_human_bot_dm_session();

    for overlay in [
        vec![
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("bot_x", Some(ParticipantMode::Muted), ActorStatus::Online),
        ],
        vec![
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Hidden),
        ],
    ] {
        let decision = router
            .route_dm_with_overlay(&session, "hello", "human_alice", &overlay)
            .await;
        assert_eq!(decision.targets.len(), 1);
        assert_eq!(decision.targets[0].delivery_type, DeliveryType::Inject);
    }
}

#[tokio::test]
async fn test_dm_bot_to_human_has_no_bot_targets() {
    let router = MessageRouter::new();
    let session = create_human_bot_dm_session();
    let overlay = vec![
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
    ];

    let decision = router
        .route_dm_with_overlay(&session, "done", "bot_x", &overlay)
        .await;

    assert!(decision.targets.is_empty());
    assert!(decision.mentions.is_empty());
}

#[tokio::test]
async fn test_dm_bot_bot_routes_to_other_bot_and_excludes_sender() {
    let router = MessageRouter::new();
    let session = create_bot_bot_dm_session();
    let overlay = vec![
        ov_bot("bot_a", None, ActorStatus::Online),
        ov_bot("bot_b", None, ActorStatus::Online),
    ];

    let decision = router
        .route_dm_with_overlay(&session, "@bot_a should not self route", "bot_a", &overlay)
        .await;

    assert_eq!(decision.targets.len(), 1);
    assert_eq!(decision.targets[0].bot_uuid, "bot_b");
    assert_eq!(decision.targets[0].delivery_type, DeliveryType::Send);
    assert!(target_for(&decision, "bot_a").is_none());
}

// ---- 8-cell overlay matrix (mode × status × mention for Bot) ----

/// Cell 1: Bot auto + online + NOT mentioned + non-driver → Inject.
#[tokio::test]
async fn test_overlay_bot_auto_online_unmentioned_nondriver_inject() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "ping no mention", Some("sender"), &overlay)
        .await;

    // sender excluded; driver gets Send (default no-mention rule); bot_x Inject
    assert!(target_for(&d, "sender").is_none());
    assert_eq!(
        target_for(&d, "driver").unwrap().delivery_type,
        DeliveryType::Send
    );
    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Inject
    );
    assert!(d.mentions.is_empty());
}

/// Cell 2: Bot auto + online + @-mentioned → Send.
#[tokio::test]
async fn test_overlay_bot_auto_online_mentioned_send() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "@bot_x please", Some("sender"), &overlay)
        .await;

    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Send
    );
    assert_eq!(
        target_for(&d, "driver").unwrap().delivery_type,
        DeliveryType::Inject
    );
    assert_eq!(d.mentions, vec!["bot_x".to_string()]);
}

/// Cell 3: Bot muted + online + @-mentioned → forced Inject.
#[tokio::test]
async fn test_overlay_bot_muted_online_mentioned_forced_inject() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", Some(ParticipantMode::Muted), ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "@bot_x please", Some("sender"), &overlay)
        .await;

    // bot_x is mentioned but muted → Inject; driver is also Inject (mention branch)
    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Inject
    );
    assert_eq!(
        target_for(&d, "driver").unwrap().delivery_type,
        DeliveryType::Inject
    );
}

/// Cell 4: Bot auto + hidden + @-mentioned → forced Inject.
#[tokio::test]
async fn test_overlay_bot_auto_hidden_mentioned_forced_inject() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Hidden),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "@bot_x please", Some("sender"), &overlay)
        .await;

    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Inject
    );
}

/// Cell 5: Bot muted + hidden + @-mentioned → still Inject (both downgrade).
#[tokio::test]
async fn test_overlay_bot_muted_hidden_mentioned_inject() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", Some(ParticipantMode::Muted), ActorStatus::Hidden),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "@bot_x please", Some("sender"), &overlay)
        .await;

    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Inject
    );
}

/// Cell 6: @ALL with one Bot muted → muted Bot still gets Inject; others Send.
#[tokio::test]
async fn test_overlay_at_all_with_muted_bot_downgraded() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", Some(ParticipantMode::Muted), ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "@all heads up", Some("sender"), &overlay)
        .await;

    assert_eq!(
        target_for(&d, "driver").unwrap().delivery_type,
        DeliveryType::Send
    );
    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Inject
    );
}

/// Cell 7: driver hidden + no mention → driver gets Inject (forced),
/// other Bots remain Inject too (no mention, non-driver default).
#[tokio::test]
async fn test_overlay_driver_hidden_no_mention_driver_forced_inject() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Hidden),
        ov_bot("bot_x", None, ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "ping", Some("sender"), &overlay)
        .await;

    assert_eq!(
        target_for(&d, "driver").unwrap().delivery_type,
        DeliveryType::Inject
    );
    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Inject
    );
}

/// Cell 8: All Bots online + no mention → only driver Send (sanity baseline).
#[tokio::test]
async fn test_overlay_all_online_no_mention_baseline() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "hello", Some("sender"), &overlay)
        .await;

    assert_eq!(
        target_for(&d, "driver").unwrap().delivery_type,
        DeliveryType::Send
    );
    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Inject
    );
}

// ---- @Human path (X.3) ----

/// @Human present: mention survives in `decision.mentions`, but no Bot
/// is in `bot_mentions` so the no-mention branch fires → driver Send.
/// Human never appears in `targets` (Bot-only).
#[tokio::test]
async fn test_overlay_at_present_human_driver_still_sends() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "@human_alice 你怎么看", Some("sender"), &overlay)
        .await;

    // Human kept in mentions but absent from targets
    assert!(d.mentions.contains(&"human_alice".to_string()));
    assert!(target_for(&d, "human_alice").is_none());
    // No Bot mentioned → driver Send fallback
    assert_eq!(
        target_for(&d, "driver").unwrap().delivery_type,
        DeliveryType::Send
    );
    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Inject
    );
}

/// @Human absent: mention is dropped entirely, no Bot is mentioned →
/// no-mention branch fires (driver Send). `mentions` MUST NOT contain
/// the absent human.
#[tokio::test]
async fn test_overlay_at_absent_human_drops_mention() {
    let router = MessageRouter::new();
    // Mark human as absent in the session participants too (overlay alone
    // is the source of truth, but route_with_overlay falls back to
    // session.participants when overlay row is missing — keep both
    // consistent for clarity).
    let mut session = create_overlay_test_session();
    for p in session.participants.iter_mut() {
        if p.bot_uuid == "human_alice" {
            p.mode = Some(ParticipantMode::Absent);
        }
    }
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Absent, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(&session, "@human_alice 在吗", Some("sender"), &overlay)
        .await;

    // Absent human dropped from mentions
    assert!(!d.mentions.contains(&"human_alice".to_string()));
    // No Bot mentioned → driver Send
    assert_eq!(
        target_for(&d, "driver").unwrap().delivery_type,
        DeliveryType::Send
    );
    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Inject
    );
}

/// Mixed: @Bot + @Human present together → Bot mention takes effect (Bot
/// branch fires), Human mention preserved in `mentions` for transcript.
#[tokio::test]
async fn test_overlay_at_bot_plus_present_human_bot_branch_wins() {
    let router = MessageRouter::new();
    let session = create_overlay_test_session();
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let d = router
        .route_with_overlay(
            &session,
            "@bot_x @human_alice please coordinate",
            Some("sender"),
            &overlay,
        )
        .await;

    // bot_x → Send (mentioned Bot), driver → Inject (mention branch)
    assert_eq!(
        target_for(&d, "bot_x").unwrap().delivery_type,
        DeliveryType::Send
    );
    assert_eq!(
        target_for(&d, "driver").unwrap().delivery_type,
        DeliveryType::Inject
    );
    // Both mentions surface for transcript
    assert!(d.mentions.contains(&"bot_x".to_string()));
    assert!(d.mentions.contains(&"human_alice".to_string()));
}

// -----------------------------------------------------------
// ManagerWorker broadcast exclusion (Fix 6)
// -----------------------------------------------------------

fn create_manager_worker_session() -> Group {
    Group {
        id: "mw-session".to_string(),
        driver_bot: "manager".to_string(),
        group_strategy: GroupStrategy::ManagerWorker,
        participants: vec![
            Participant {
                bot_uuid: "manager".to_string(),
                bot_name: Some("Manager".to_string()),
                kind: None,
                role: ParticipantRole::Manager,
                actor_kind: ActorKind::Bot,
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "worker1".to_string(),
                bot_name: Some("Worker1".to_string()),
                kind: None,
                role: ParticipantRole::Worker,
                actor_kind: ActorKind::Bot,
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "worker2".to_string(),
                bot_name: Some("Worker2".to_string()),
                kind: None,
                role: ParticipantRole::Worker,
                actor_kind: ActorKind::Bot,
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
        ],
        ..create_test_session()
    }
}

#[tokio::test]
async fn manager_worker_route_excludes_workers_on_no_mention() {
    let router = MessageRouter::new();
    let session = create_manager_worker_session();

    let decision = router
        .route(&session, "Hello, workers do your jobs", None)
        .await;

    // Manager (lead) is present, workers are excluded
    assert!(target_for(&decision, "manager").is_some());
    assert!(target_for(&decision, "worker1").is_none());
    assert!(target_for(&decision, "worker2").is_none());
}

#[tokio::test]
async fn manager_worker_route_excludes_workers_even_when_mentioned() {
    let router = MessageRouter::new();
    let session = create_manager_worker_session();

    // @mention worker1 explicitly — it should still be excluded
    let decision = router
        .route(&session, "@worker1 please help", None)
        .await;

    // Manager is still present, but worker1 is excluded despite mention
    assert!(target_for(&decision, "manager").is_some());
    assert!(target_for(&decision, "worker1").is_none());
}

#[tokio::test]
async fn manager_worker_route_with_overlay_excludes_workers() {
    let router = MessageRouter::new();
    let session = create_manager_worker_session();
    let overlay = vec![
        ov_bot("manager", None, ActorStatus::Online),
        ov_bot("worker1", None, ActorStatus::Online),
        ov_bot("worker2", None, ActorStatus::Online),
    ];

    let decision = router
        .route_with_overlay(
            &session,
            "@worker1 do this task",
            Some("sender"),
            &overlay,
        )
        .await;

    // Manager is present, workers excluded from broadcast table
    assert!(target_for(&decision, "manager").is_some());
    assert!(target_for(&decision, "worker1").is_none());
    assert!(target_for(&decision, "worker2").is_none());
}

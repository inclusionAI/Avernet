use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus as Status};
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::queued_payload::read_queued_payload;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::{
    DeliveryTransitionCommand, ManagedDeliveryError, ManagedMessageDeliveryService,
};
use std::sync::Arc;

fn admit(id: &str, kind: DeliveryType) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries {
        display_message: None,
        message_id: id.into(),
        flow_kind: DeliveryFlowKind::Group,
        now_ms: 100,
        expire_at_ms: None,
        event: None,
        targets: vec![DeliveryAdmissionTarget { rejection: None,
            target_bot_id: "bot".into(),
            kind,
            max_queued: 20,
            semantic_projection_json: serde_json::json!({"version":1}),
        }],
        message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None,
            group_id: "group".into(),
            session_id: "session".into(),
            sender_id: "human".into(),
            sender_type: SenderType::Human,
            message_type: "chat".into(),
            client_msg_id: Some(id.into()),
            owner_bot_id: None,
            created_at: 100,
            run_id: String::new(),
            content: serde_json::json!({"text":id, "attachments":[{
                "attachment_id":id, "type":"file", "file_name":"f.txt", "url":"https://example.invalid/file"
            }]}),
        },
    }
}

fn transition(
    row: &bcs_domain::message_delivery::PersistedMessageDelivery,
    event: Event,
) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand {
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        event,
        now_ms: 200,
        request_id: None,
        actor_id: Some("human".into()),
        reply: None,
        transport_context_json: None,
        deadline_at_ms: None,
    }
}

#[tokio::test]
async fn send_start_rechecks_cross_session_capacity_under_mutation_lock() -> Result<(), Box<dyn std::error::Error>> {
    use bcs_config_api::message_delivery::{DeliveryPolicyRecord, BotDeliveryMode};
    let repo = Arc::new(MemoryMessageRepo::new());
    let mut initial = DeliveryPolicyRecord::default();
    initial.policy.flow_enabled.group = true; initial.policy.defaults.mode = BotDeliveryMode::Enforce; initial.policy.defaults.max_running = 1;
    let policy = Arc::new(bcs_message_flow::delivery_policy::LiveDeliveryPolicy::new(repo.clone(), initial));
    let service = ManagedMessageDelivery::new(repo).with_policy(policy);
    let mut a = admit("race-a", DeliveryType::Send); a.message.session_id = "a".into();
    let mut b = admit("race-b", DeliveryType::Send); b.message.session_id = "b".into();
    let a = service.admit(a).await?.deliveries.remove(0); let b = service.admit(b).await?.deliveries.remove(0);
    let mut ca = transition(&a, Event::StartSend); ca.transport_context_json = Some(serde_json::json!({"policy_version":0}));
    let mut cb = transition(&b, Event::StartSend); cb.transport_context_json = Some(serde_json::json!({"policy_version":0}));
    let (a,b) = tokio::join!(service.transition(ca), service.transition(cb));
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    assert_eq!(service.active_count("bot").await?, 1);
    Ok(())
}

#[tokio::test]
async fn instrumentation_counts_only_committed_nonduplicate_events() -> Result<(), Box<dyn std::error::Error>> {
    #[derive(Default)] struct Hook(std::sync::Mutex<Vec<&'static str>>);
    impl bcs_service_api::application::message_delivery::DeliveryInstrumentation for Hook {
        fn operation(&self, _: &'static str, _: f64, _: usize, _: bool) {}
        fn event(&self, name: &'static str, _: &bcs_domain::message_delivery::PersistedMessageDelivery) { self.0.lock().unwrap().push(name); }
    }
    let hook = Arc::new(Hook::default());
    let service = ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())).with_instrumentation(hook.clone());
    let queued = service.admit(admit("metric", DeliveryType::Send)).await?.deliveries.remove(0);
    assert!(service.admit(admit("metric", DeliveryType::Send)).await?.duplicate);
    let running = service.transition(transition(&queued, Event::StartSend)).await?;
    assert!(service.transition(transition(&queued, Event::StartSend)).await.is_err());
    let done = service.transition(transition(&running, Event::Completed)).await?;
    service.transition(transition(&done, Event::Completed)).await?;
    assert_eq!(*hook.0.lock().unwrap(), vec!["admitted", "started", "terminal"]);
    Ok(())
}

#[tokio::test]
async fn safe_retry_preserves_identity_and_context_but_rotates_the_attempt()
-> Result<(), Box<dyn std::error::Error>> {
    let service =
        ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())).with_retry_backoff(123);
    service
        .admit(admit("context", DeliveryType::Inject))
        .await?;
    let queued = service
        .admit(admit("send", DeliveryType::Send))
        .await?
        .deliveries
        .remove(0);
    let first = service
        .transition(transition(&queued, Event::StartSend))
        .await?;
    let mut retry = transition(&first, Event::DefinitelyNotSent { retry: true });
    retry.request_id = first.request_id.clone();
    let waiting = service.transition(retry).await?;
    assert_eq!(waiting.available_at_ms, 323);
    assert!(!waiting.state.may_have_been_sent);
    let context = service
        .snapshot(None)
        .await?
        .into_iter()
        .find(|row| row.source_message_id == "context")
        .unwrap();
    assert_eq!(
        context.bound_to_delivery_id.as_deref(),
        Some(queued.delivery_id.as_str())
    );
    assert_eq!(context.state.status, Status::Bound);
    let mut start = transition(&waiting, Event::StartSend);
    start.now_ms = 323;
    let second = service.transition(start).await?;
    assert_eq!(second.run_id, first.run_id);
    assert_eq!(second.idempotency_key, first.idempotency_key);
    assert_ne!(second.request_id, first.request_id);
    assert_eq!(second.attempt_no, 2);
    Ok(())
}

#[tokio::test]
async fn notifications_follow_commits_and_do_not_replay_duplicate_admission() {
    let service = ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new()));
    let mut changes = service.subscribe();
    let admission = service
        .admit(admit("notification", DeliveryType::Send))
        .await
        .unwrap();
    let row = &admission.deliveries[0];
    assert_eq!(changes.try_recv().unwrap()[0].delivery_id, row.delivery_id);
    assert!(
        service
            .admit(admit("notification", DeliveryType::Send))
            .await
            .unwrap()
            .duplicate
    );
    assert!(changes.try_recv().is_err());
    service
        .update_wait_reasons(
            vec![(
                row.delivery_id.clone(),
                bcs_domain::message_delivery::DeliveryWaitReason::BotOffline,
            )],
            201,
        )
        .await
        .unwrap();
    let changed = changes.try_recv().unwrap().remove(0);
    assert_eq!(changed.state.state_version, row.state.state_version + 1);
    assert_eq!(
        changed.wait_reason,
        Some(bcs_domain::message_delivery::DeliveryWaitReason::BotOffline)
    );
    service
        .update_wait_reasons(
            vec![(
                row.delivery_id.clone(),
                bcs_domain::message_delivery::DeliveryWaitReason::BotOffline,
            )],
            202,
        )
        .await
        .unwrap();
    assert!(changes.try_recv().is_err());
    // Stale updates and disabled admission produce no success notification.
    assert!(
        service
            .transition(transition(row, Event::CancelRequested))
            .await
            .is_err()
    );
    assert!(changes.try_recv().is_err());
    service.set_admission_available(false);
    assert!(
        service
            .admit(admit("disabled", DeliveryType::Send))
            .await
            .is_err()
    );
    assert!(changes.try_recv().is_err());
    let cancelled = service
        .transition(transition(&changed, Event::CancelRequested))
        .await
        .unwrap();
    assert_eq!(cancelled.state.status, Status::Cancelled);
    assert_eq!(
        changes.try_recv().unwrap()[0].state.status,
        Status::Cancelled
    );
}

#[tokio::test]
async fn ack_persists_engine_identity_and_rejects_wrong_bot_and_alias_rebinding() {
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = ManagedMessageDelivery::new(repo);
    let row = service
        .admit(admit("ack", DeliveryType::Send))
        .await
        .unwrap()
        .deliveries
        .remove(0);
    let mut start = transition(&row, Event::StartSend);
    start.transport_context_json =
        Some(serde_json::json!({"version":1, "downstream_session_key":"original"}));
    let started = service.transition(start).await.unwrap();
    let request_id = started.request_id.as_deref().unwrap();
    assert!(
        service
            .accept_run(request_id, "other-bot", Some("engine-1"), 300)
            .await
            .is_err()
    );
    assert!(
        service
            .accept_run("unknown-request", "bot", Some("engine-1"), 300)
            .await
            .unwrap()
            .is_none()
    );
    let accepted = service
        .accept_run(request_id, "bot", Some("engine-1"), 300)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(accepted.state.status, Status::Running);
    assert_eq!(accepted.run_id, started.run_id);
    assert_eq!(
        accepted.transport_context_json.as_ref().unwrap()["downstream_run_id"],
        "engine-1"
    );
    let duplicate = service
        .accept_run(request_id, "bot", Some("engine-1"), 301)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(duplicate.state.state_version, accepted.state.state_version);
    assert!(
        service
            .accept_run(request_id, "bot", Some("engine-2"), 302)
            .await
            .is_err()
    );
    let cancelling = service
        .transition(transition(&accepted, Event::CancelRequested))
        .await
        .unwrap();
    let late = service
        .accept_run(request_id, "bot", Some("engine-1"), 303)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(late.state.status, Status::Cancelling);
    assert_eq!(late.state.state_version, cancelling.state.state_version);
    service.recover(400).await.unwrap();
    let recovered = service.snapshot(None).await.unwrap().remove(0);
    assert_eq!(recovered.state.status, Status::CancelUnknown);
    assert_eq!(
        recovered.transport_context_json.unwrap()["downstream_run_id"],
        "engine-1"
    );
}

#[tokio::test]
async fn cancellation_rebinds_to_existing_successor_and_invalidates_prepared_payload()
-> Result<(), Box<dyn std::error::Error>> {
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = ManagedMessageDelivery::new(repo.clone());
    service
        .admit(admit("context", DeliveryType::Inject))
        .await?;
    let first = service
        .admit(admit("first", DeliveryType::Send))
        .await?
        .deliveries
        .remove(0);
    let successor = service
        .admit(admit("successor", DeliveryType::Send))
        .await?
        .deliveries
        .remove(0);
    service
        .transition(transition(&first, Event::CancelRequested))
        .await?;
    let rows = service.snapshot(None).await?;
    let context = rows
        .iter()
        .find(|r| r.source_message_id == "context")
        .ok_or("missing context")?;
    let successor = rows
        .iter()
        .find(|r| r.delivery_id == successor.delivery_id)
        .ok_or("missing successor")?;
    assert_eq!(
        context.bound_to_delivery_id.as_ref(),
        Some(&successor.delivery_id)
    );
    assert_eq!(successor.state.state_version, 2);
    let payload =
        read_queued_payload(repo.as_ref(), successor, std::slice::from_ref(context)).await?;
    assert!(payload.text.contains("context"));
    assert!(payload.text.contains("successor"));
    assert!(!payload.text.contains("first"));
    assert_eq!(
        payload.attachments.len(),
        1,
        "Inject files must not gain Send visibility"
    );
    assert_eq!(payload.attachments[0].attachment_id, "successor");
    assert_eq!(payload.state_version, 2);
    Ok(())
}

#[tokio::test]
async fn bound_context_cannot_be_withdrawn_after_send_start()
-> Result<(), Box<dyn std::error::Error>> {
    let service = ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new()));
    service
        .admit(admit("context", DeliveryType::Inject))
        .await?;
    let first = service
        .admit(admit("first", DeliveryType::Send))
        .await?
        .deliveries
        .remove(0);
    let started = service
        .transition(transition(&first, Event::StartSend))
        .await?;
    let rows = service.snapshot(None).await?;
    let context = rows
        .iter()
        .find(|r| r.source_message_id == "context")
        .ok_or("context missing")?;
    assert!(matches!(
        service
            .transition(transition(context, Event::CancelRequested))
            .await,
        Err(ManagedDeliveryError::Conflict)
    ));
    service.recover(300).await?;
    let rows = service.snapshot(None).await?;
    let recovered = rows
        .iter()
        .find(|r| r.delivery_id == started.delivery_id)
        .ok_or("run missing")?;
    assert_eq!(recovered.state.status, Status::Unknown);
    service
        .transition(transition(recovered, Event::Completed))
        .await?;
    let rows = service.snapshot(None).await?;
    assert_eq!(
        rows.iter()
            .find(|r| r.source_message_id == "context")
            .ok_or("context missing")?
            .state
            .status,
        Status::Consumed
    );
    Ok(())
}

#[tokio::test]
async fn pending_and_bound_context_cancellation_do_not_start_a_run()
-> Result<(), Box<dyn std::error::Error>> {
    let service = ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new()));
    let pending = service
        .admit(admit("pending", DeliveryType::Inject))
        .await?
        .deliveries
        .remove(0);
    let cancelled = service
        .transition(transition(&pending, Event::CancelRequested))
        .await?;
    assert_eq!(cancelled.state.status, Status::Cancelled);
    assert!(cancelled.run_id.is_none());
    service.admit(admit("bound", DeliveryType::Inject)).await?;
    service.admit(admit("carrier", DeliveryType::Send)).await?;
    let rows = service.snapshot(None).await?;
    let bound = rows
        .iter()
        .find(|r| r.source_message_id == "bound")
        .ok_or("missing bound")?;
    service
        .transition(transition(bound, Event::CancelRequested))
        .await?;
    let rows = service.snapshot(None).await?;
    assert_eq!(
        rows.iter()
            .find(|r| r.source_message_id == "carrier")
            .ok_or("missing carrier")?
            .state
            .state_version,
        2
    );
    Ok(())
}

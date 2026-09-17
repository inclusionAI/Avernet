use std::sync::{Arc, atomic::{AtomicBool, Ordering}};

use async_trait::async_trait;
use bcs_channel::BcsChannelService;
use bcs_channel_api::{ChannelProvider, ChannelProviderRegistry, ChannelProviderResult};
use bcs_channel_store::{MemoryChannelBindingRepo, MemoryConversationSessionRepo, MemoryHumanInputRequestRepo, MemoryImParticipantRepo};
use bcs_domain::{BindingStatus, BindingTarget, ChannelBinding, HumanInputNotificationMode, HumanInputRequestStatus, Visibility};
use bcs_service_api::{HumanInputReadyEvent, PendingHumanNodeView, ServiceError, ServiceResult, SessionChannelDeliveryOutcome, SessionChannelOutboundPort};
use bcs_service_api::port::channel_delivery::{ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent};
use bcs_service_api::port::repo::{ChannelBindingRepoPort, HumanInputRequestRepoPort};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{NoopBotRegistryCoreService, NoopCollaborationRuntimeService, NoopGroupCoreService, NoopMessageFlowService, NoopSystemMessageService};
use tokio::sync::{Mutex, Notify};

#[derive(Default)]
struct Provider {
    events: Mutex<Vec<ChannelOutboundEvent>>,
    fail: AtomicBool,
    clock: Arc<std::sync::atomic::AtomicU64>,
    stale_notification: Arc<AtomicBool>,
    stale_node: Arc<Mutex<Option<String>>>,
    pause_during_send: AtomicBool,
    pause_before_send: AtomicBool,
    entered: Notify,
    release: Notify,
    fail_persistence: Mutex<Option<std::path::PathBuf>>,
}

#[async_trait]
impl ChannelDeliveryPort for Provider {
    async fn is_available(&self, _: &ChannelBindingRef) -> bool {
        if self.pause_before_send.load(Ordering::SeqCst) {
            self.entered.notify_one();
            self.release.notified().await;
        }
        true
    }
    async fn deliver_event(&self, event: ChannelOutboundEvent) -> ServiceResult<ChannelDeliveryResult> {
        self.events.lock().await.push(event);
        if self.pause_during_send.load(Ordering::SeqCst) {
            self.entered.notify_one(); self.release.notified().await;
        }
        if let Some(file) = self.fail_persistence.lock().await.take() {
            // The request is durable, but its following activation write fails.
            tokio::fs::remove_file(&file).await.unwrap();
            tokio::fs::create_dir(&file).await.unwrap();
        }
        let failed = self.fail.load(Ordering::SeqCst);
        Ok(ChannelDeliveryResult { delivered: !failed, provider_message_ref: Some("saved-provider-ref".into()),
            error: failed.then(|| ServiceError::InternalError("provider retries exhausted".into())) })
    }
}

struct RegisteredProvider(Arc<Provider>);
#[async_trait]
impl ChannelProvider for RegisteredProvider {
    fn channel_type(&self) -> &'static str { "test-im" }
    fn validate_config(&self, _: &serde_json::Value) -> ChannelProviderResult<()> { Ok(()) }
    fn redact_config(&self, config: &serde_json::Value) -> serde_json::Value { config.clone() }
    fn resolve_direct_recipient(&self, actor: &str) -> ChannelProviderResult<Option<String>> {
        Ok((actor == "human_1").then(|| "im-user-1".into()))
    }
    fn delivery(&self) -> Arc<dyn ChannelDeliveryPort> { self.0.clone() }
    fn http_ingress(&self) -> Option<Arc<dyn bcs_channel_api::ChannelHttpIngressPort>> { None }
    fn stream_lifecycle(&self, _: Arc<dyn bcs_channel_api::ChannelInboundSink>) -> Option<Arc<dyn bcs_service_api::lifecycle::ServiceLifecycle>> { None }
}


use bcs_service_api::application::collaboration_runtime::*;
use bcs_domain::{CollaborationDefinition, StateMachineDeliveryCorrelation};
use bcs_service_api::{CollaborationRuntimeService, SessionHistoryResult};
struct NotificationRuntime(Arc<AtomicBool>, Arc<Mutex<Option<String>>>);
#[async_trait]
impl CollaborationRuntimeService for NotificationRuntime {
    async fn human_input_notification_is_current(&self, _: &str, _: &str, node: &str, _: u64) -> Result<bool, CollaborationRuntimeError> { Ok(!self.0.load(Ordering::SeqCst) && self.1.lock().await.as_deref() != Some(node)) }
    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.start_state_machine_run(cmd).await }
    async fn get_state_machine_run(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> { NoopCollaborationRuntimeService.get_state_machine_run(run_id).await }
    async fn get_state_machine_session_history(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> { NoopCollaborationRuntimeService.get_state_machine_session_history(session_id, limit, before).await }
    async fn cancel_state_machine_run(
        &self,
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> { NoopCollaborationRuntimeService.cancel_state_machine_run(cmd).await }
    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> { NoopCollaborationRuntimeService.lookup_delivery_correlation(run_id).await }
    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> { NoopCollaborationRuntimeService.register_delivery_alias(delivery_request_id, bot_delivery_run_id).await }
    async fn handle_bot_terminal_event(
        &self,
        cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.handle_bot_terminal_event(cmd).await }
    async fn upsert_definition(
        &self,
        definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> { NoopCollaborationRuntimeService.upsert_definition(definition).await }
    async fn configure_group_runtime(
        &self,
        cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.configure_group_runtime(cmd).await }
}

async fn service(repo: Arc<MemoryHumanInputRequestRepo>, provider: Arc<Provider>) -> BcsChannelService {
    let bindings = Arc::new(MemoryChannelBindingRepo::new("test"));
    bindings.create(ChannelBinding {
        id: "binding-loop".into(), channel_type: "test-im".into(), account_ref: "account-loop".into(),
        target: BindingTarget::Group { group_id: "group-loop".into() }, group_chat_scope: None,
        outbound_visibility: Visibility::FullTranscript, env: "test".into(), status: BindingStatus::Active,
        created_by: None, config: serde_json::json!({}),
    }).await.unwrap();
    let clock = provider.clock.clone();
    BcsChannelService::new(bindings, Arc::new(MemoryConversationSessionRepo::new()),
        Arc::new(MemoryImParticipantRepo::new()), repo, Arc::new(MemorySessionRepo::new()),
        Arc::new(NoopMessageFlowService), Arc::new(NoopSystemMessageService), Arc::new(NotificationRuntime(provider.stale_notification.clone(), provider.stale_node.clone())),
        Arc::new(NoopGroupCoreService), Arc::new(NoopBotRegistryCoreService),
        Arc::new(ChannelProviderRegistry::new(vec![Arc::new(RegisteredProvider(provider))]).unwrap()),
        "test", Arc::new(move || { let now = clock.load(Ordering::SeqCst); if now == 0 { 42 } else { now } }), Arc::new(|| "unused-id".into()))
}

fn ready(first: bool, mode: HumanInputNotificationMode) -> HumanInputReadyEvent {
    // Shared with API/Graph/panel/CLI: identities and previous results are opaque.
    let fixture: serde_json::Value = serde_json::from_str(include_str!("../../../../../tests/fixtures/fixed_loop_api.json")).unwrap();
    let pending: PendingHumanNodeView = serde_json::from_value(fixture[if first { "pending_first" } else { "pending_later" }][0].clone()).unwrap();
    HumanInputReadyEvent {
        event_id: format!("request-{}", pending.node_id), group_id: "group-loop".into(),
        session_id: "session-loop".into(), run_id: "run-loop".into(), node_id: pending.node_id,
        display_name: pending.display_name, instruction: pending.instruction, assignee_actor_id: "human_1".into(),
        channel_type: "test-im".into(), notification_mode: mode, fixed_group_conversation_id: Some("shared-conversation".into()),
        response_ref: pending.response_ref, upstream_artifacts: pending.upstream_artifacts,
        judge_outcomes: pending.judge_outcomes, timeout_deadline_ms: Some(1000), loop_context: pending.loop_context,
    }
}

#[tokio::test]
async fn first_and_later_loop_notifications_match_shared_context_goldens() {
    for (first, mode, golden) in [
        (true, HumanInputNotificationMode::DirectAssignee, include_str!("fixtures/direct-first.txt")),
        (false, HumanInputNotificationMode::DirectAssignee, include_str!("fixtures/direct-later.txt")),
        (true, HumanInputNotificationMode::FixedGroup, include_str!("fixtures/group-first.txt")),
        (false, HumanInputNotificationMode::FixedGroup, include_str!("fixtures/group-later.txt")),
    ] {
        let repo = Arc::new(MemoryHumanInputRequestRepo::new());
        let provider = Arc::new(Provider::default());
        let service = service(repo.clone(), provider.clone()).await;
        let mut event = ready(first, mode);
        if let Some(previous) = event.loop_context.as_ref().unwrap().previous_result.as_ref() {
            // An accidentally duplicated artifact must not repeat the prior result.
            event.upstream_artifacts.push(bcs_service_api::JudgeArtifact {
                node_id: previous.execution_node_id.clone(), text: previous.output.clone(),
            });
        }
        let context = serde_json::to_value(&event.loop_context).unwrap();
        service.publish_human_input_ready(event.clone()).await.unwrap();
        assert_eq!(serde_json::to_value(&event.loop_context).unwrap(), context);
        let saved = repo.get(&event.event_id).await.unwrap().unwrap();
        assert_eq!(saved.notification_text, golden.trim_end());
        let events = provider.events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].text.as_deref(), Some(saved.notification_text.as_str()));
        assert_eq!(events[0].run_id, format!("human-input-{}", event.event_id));
        assert_eq!(events[0].raw_payload["node_id"], event.node_id);
        if mode == HumanInputNotificationMode::FixedGroup {
            assert_eq!(events[0].im_conversation_id, "shared-conversation");
            assert_eq!(events[0].im_conversation_type, "2");
            assert!(events[0].im_user_id.is_none());
            assert!(!saved.notification_text.contains("result-2"));
            assert!(!saved.notification_text.contains("historical-work-2"));
            assert!(events[0].raw_payload.get("loop_context").is_none());
        } else {
            assert_eq!(events[0].im_user_id.as_deref(), Some("im-user-1"));
            assert_eq!(saved.notification_text.matches("result-2").count(), usize::from(!first));
        }
    }
}

#[tokio::test]
async fn invalid_loop_context_never_enqueues_or_delivers_and_v1_omits_loop_section() {
    let repo = Arc::new(MemoryHumanInputRequestRepo::new());
    let provider = Arc::new(Provider::default());
    let service = service(repo.clone(), provider.clone()).await;
    for case in 0..5 {
        let mut event = ready(false, HumanInputNotificationMode::DirectAssignee);
        let context = event.loop_context.as_mut().unwrap();
        match case {
            0 => context.previous_result = None,
            1 => context.iteration = 1,
            2 => context.iteration = 0,
            3 => context.max_iterations = 2,
            _ => context.previous_result.as_mut().unwrap().iteration = 1,
        }
        assert!(service.publish_human_input_ready(event.clone()).await.is_err());
        assert!(repo.get(&event.event_id).await.unwrap().is_none());
    }
    assert!(provider.events.lock().await.is_empty());
    let mut v1 = ready(true, HumanInputNotificationMode::DirectAssignee);
    v1.loop_context = None;
    service.publish_human_input_ready(v1.clone()).await.unwrap();
    let saved = repo.get(&v1.event_id).await.unwrap().unwrap();
    assert_eq!(saved.notification_text, "【待你处理】Review\n\nReview saved result\n\n可识别结果：again / done\n\n等待截止时间（Unix ms）：1000\n\n请直接回复本会话。");
}

#[tokio::test]
async fn restart_before_send_and_event_retry_reuse_saved_text_and_identity() {
    let dir = tempfile::tempdir().unwrap();
    let repo = Arc::new(MemoryHumanInputRequestRepo::with_data_dir(dir.path().into()));
    let provider = Arc::new(Provider::default());
    provider.pause_before_send.store(true, Ordering::SeqCst);
    let first_service = Arc::new(service(repo.clone(), provider.clone()).await);
    let event = ready(false, HumanInputNotificationMode::DirectAssignee);
    let sending = {
        let service = first_service.clone();
        let event = event.clone();
        tokio::spawn(async move { service.publish_human_input_ready(event).await })
    };
    tokio::time::timeout(std::time::Duration::from_secs(3), provider.entered.notified()).await.unwrap();
    let saved = repo.get(&event.event_id).await.unwrap().unwrap();
    assert_eq!(saved.status, HumanInputRequestStatus::NotificationPending);
    assert!(provider.events.lock().await.is_empty());
    sending.abort();
    assert!(sending.await.unwrap_err().is_cancelled());
    drop(first_service);
    drop(repo);

    let reloaded = Arc::new(MemoryHumanInputRequestRepo::with_data_dir(dir.path().into()));
    reloaded.load_from_disk().await.unwrap();
    let provider = Arc::new(Provider::default());
    let restarted = service(reloaded.clone(), provider.clone()).await;
    let mut changed = event.clone();
    changed.instruction = "changed current instruction".into();
    changed.loop_context = None;
    changed.fixed_group_conversation_id = Some("changed-current-destination".into());
    restarted.publish_human_input_ready(changed.clone()).await.unwrap();
    restarted.publish_human_input_ready(changed.clone()).await.unwrap();
    let active = reloaded.get(&event.event_id).await.unwrap().unwrap();
    assert_eq!(active.status, HumanInputRequestStatus::Active);
    assert_eq!(active.notification_text, saved.notification_text);
    assert_eq!(active.delivery_attempts, 1);
    assert_eq!(reloaded.list_by_run(&event.run_id).await.unwrap().len(), 1);
    let events = provider.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].text.as_deref(), Some(saved.notification_text.as_str()));
    assert_eq!(events[0].im_user_id, saved.im_user_id);
    drop(events);
    changed.node_id = "another-iteration".into();
    assert!(restarted.publish_human_input_ready(changed).await.is_err());
    assert_eq!(provider.events.lock().await.len(), 1);
    reloaded.mark_responded(&event.event_id, 45).await.unwrap();
    assert_eq!(restarted.publish_human_input_ready(event).await.unwrap(), SessionChannelDeliveryOutcome::NotApplicable);
    assert_eq!(provider.events.lock().await.len(), 1);
}

#[tokio::test]
async fn queued_loop_notification_survives_restart_and_preserves_group_redaction() {
    let dir = tempfile::tempdir().unwrap();
    let repo = Arc::new(MemoryHumanInputRequestRepo::with_data_dir(dir.path().into()));
    let provider = Arc::new(Provider::default());
    let first_service = service(repo.clone(), provider.clone()).await;
    let first = ready(true, HumanInputNotificationMode::FixedGroup);
    let mut later = ready(false, HumanInputNotificationMode::FixedGroup);
    first_service.publish_human_input_ready(first.clone()).await.unwrap();
    first_service.publish_human_input_ready(later.clone()).await.unwrap();
    // A repeated queued event must not create a second summary or request.
    first_service.publish_human_input_ready(later.clone()).await.unwrap();
    assert_eq!(provider.events.lock().await.len(), 2);
    let saved = repo.get(&later.event_id).await.unwrap().unwrap();
    assert_eq!(saved.status, HumanInputRequestStatus::Queued);
    drop(first_service);
    drop(repo);
    let reloaded = Arc::new(MemoryHumanInputRequestRepo::with_data_dir(dir.path().into()));
    reloaded.load_from_disk().await.unwrap();
    reloaded.mark_responded(&first.event_id, 43).await.unwrap();
    let provider = Arc::new(Provider::default());
    let restarted = service(reloaded.clone(), provider.clone()).await;
    later.instruction = "changed after restart".into();
    later.loop_context.as_mut().unwrap().previous_result.as_mut().unwrap().output = "new-private-result".into();
    restarted.publish_human_input_ready(later.clone()).await.unwrap();
    let active = reloaded.get(&later.event_id).await.unwrap().unwrap();
    assert_eq!(active.status, HumanInputRequestStatus::Active);
    assert_eq!(active.notification_text, saved.notification_text);
    assert_eq!(provider.events.lock().await[0].text.as_deref(), Some(saved.notification_text.as_str()));
    assert_eq!(reloaded.list_by_run(&later.run_id).await.unwrap().len(), 2);
}

#[tokio::test]
async fn provider_failure_preserves_loop_text_and_does_not_create_another_request() {
    let repo = Arc::new(MemoryHumanInputRequestRepo::new());
    let provider = Arc::new(Provider::default());
    provider.fail.store(true, Ordering::SeqCst);
    let service = service(repo.clone(), provider.clone()).await;
    let event = ready(false, HumanInputNotificationMode::DirectAssignee);
    assert!(service.publish_human_input_ready(event.clone()).await.is_err());
    let saved = repo.get(&event.event_id).await.unwrap().unwrap();
    assert_eq!(saved.status, HumanInputRequestStatus::DeliveryFailed);
    assert_eq!(saved.notification_text, include_str!("fixtures/direct-later.txt").trim_end());
    assert!(saved.last_delivery_error.unwrap().contains("provider retries exhausted"));
    assert!(service.publish_human_input_ready(event.clone()).await.is_err());
    assert_eq!(provider.events.lock().await.len(), 1);
    assert_eq!(repo.list_by_run(&event.run_id).await.unwrap().len(), 1);
}

#[tokio::test]
async fn queue_recovery_propagates_delivery_status_write_failure() {
    let dir = tempfile::tempdir().unwrap();
    let repo = Arc::new(MemoryHumanInputRequestRepo::with_data_dir(dir.path().into()));
    let initial = service(repo.clone(), Arc::new(Provider::default())).await;
    let first = ready(true, HumanInputNotificationMode::DirectAssignee);
    let later = ready(false, HumanInputNotificationMode::DirectAssignee);
    initial.publish_human_input_ready(first.clone()).await.unwrap();
    initial.publish_human_input_ready(later.clone()).await.unwrap();
    repo.mark_responded(&first.event_id, 43).await.unwrap();
    let provider = Arc::new(Provider::default());
    provider.fail.store(true, Ordering::SeqCst);
    *provider.fail_persistence.lock().await = Some(dir.path().join("human_input_requests.json"));
    let restarted = service(repo, provider.clone()).await;
    let error = restarted.publish_human_input_ready(later).await.unwrap_err();
    assert!(error.to_string().contains("directory"), "{error}");
    assert_eq!(provider.events.lock().await.len(), 1);
}

#[tokio::test]
async fn expired_notification_is_not_sent_or_extended_by_event_replay() {
    let repo = Arc::new(MemoryHumanInputRequestRepo::new());
    let provider = Arc::new(Provider::default());
    let service = service(repo.clone(), provider.clone()).await;
    let mut event = ready(false, HumanInputNotificationMode::DirectAssignee);
    event.timeout_deadline_ms = Some(41);
    assert_eq!(service.publish_human_input_ready(event.clone()).await.unwrap(), SessionChannelDeliveryOutcome::NotApplicable);
    event.timeout_deadline_ms = Some(1000);
    assert_eq!(service.publish_human_input_ready(event.clone()).await.unwrap(), SessionChannelDeliveryOutcome::NotApplicable);
    let saved = repo.get(&event.event_id).await.unwrap().unwrap();
    assert_eq!(saved.status, HumanInputRequestStatus::Expired);
    assert_eq!(saved.deadline_ms, 41);
    assert!(provider.events.lock().await.is_empty());
}

#[tokio::test]
async fn terminal_im_preparation_freezes_deduplicated_targets_and_text_without_sending() {
    use bcs_service_api::{StateMachineTerminalEvent, StateMachineTerminalStatus};
    let repo = Arc::new(MemoryHumanInputRequestRepo::new()); let provider = Arc::new(Provider::default());
    let channel = service(repo.clone(), provider.clone()).await;
    for (first, mode) in [(true, HumanInputNotificationMode::DirectAssignee), (false, HumanInputNotificationMode::FixedGroup)] {
        channel.publish_human_input_ready(ready(first, mode)).await.unwrap();
    }
    provider.events.lock().await.clear();
    let event = StateMachineTerminalEvent { group_id: "group-loop".into(), session_id: "session-loop".into(), run_id: "run-loop".into(),
        workflow_name: "原流程".into(), status: StateMachineTerminalStatus::Completed, output: Some("原输出".into()) };
    let notifications = channel.prepare_state_machine_terminal(&event).await.unwrap();
    assert_eq!(notifications.len(), 2); assert!(provider.events.lock().await.is_empty());
    assert_eq!(notifications, channel.prepare_state_machine_terminal(&event).await.unwrap());
    let fresh = service(repo, provider.clone()).await;
    let mut changed_event = event.clone(); changed_event.output = Some("新输出，不能重新渲染".into());
    for n in &notifications {
        fresh.validate_terminal_notification(n).await.unwrap();
        assert_eq!(fresh.deliver_terminal_notification(&changed_event, n).await.unwrap().as_deref(), Some("saved-provider-ref"));
    }
    let events = provider.events.lock().await;
    assert_eq!(events.len(), 2);
    assert!(events.iter().all(|e| e.text.as_deref() == Some("【协同已完成】原流程\n\n原输出") && e.run_id == "state-machine-terminal-run-loop"));
    assert!(events.iter().any(|e| e.im_conversation_id == "shared-conversation"));
    assert!(events.iter().any(|e| e.im_user_id.as_deref() == Some("im-user-1")));
    drop(events);
    let mut moved = notifications[0].clone(); moved.account_ref = "another-account".into();
    assert!(fresh.validate_terminal_notification(&moved).await.is_err());
    assert_eq!(provider.events.lock().await.len(), 2);
}

#[tokio::test]
async fn unknown_send_survives_restart_and_competitor_cannot_ack_or_send_again() {
    let dir = tempfile::tempdir().unwrap();
    let repo = Arc::new(MemoryHumanInputRequestRepo::with_data_dir(dir.path().into()));
    let provider = Arc::new(Provider::default());
    provider.pause_during_send.store(true, Ordering::SeqCst);
    let channel = Arc::new(service(repo.clone(), provider.clone()).await);
    let event = ready(false, HumanInputNotificationMode::DirectAssignee);
    let sending = { let c = channel.clone(); let e = event.clone(); tokio::spawn(async move { c.publish_human_input_ready(e).await }) };
    tokio::time::timeout(std::time::Duration::from_secs(3), provider.entered.notified()).await.unwrap();
    assert_eq!(repo.get(&event.event_id).await.unwrap().unwrap().status, HumanInputRequestStatus::Notifying);
    service(repo.clone(), provider.clone()).await.publish_human_input_ready(event.clone()).await.unwrap();
    assert_eq!(repo.get(&event.event_id).await.unwrap().unwrap().status, HumanInputRequestStatus::Notifying);
    assert_eq!(provider.events.lock().await.len(), 1);
    sending.abort(); assert!(sending.await.unwrap_err().is_cancelled());
    let reloaded = Arc::new(MemoryHumanInputRequestRepo::with_data_dir(dir.path().into()));
    reloaded.load_from_disk().await.unwrap();
    let restarted = service(reloaded.clone(), provider.clone()).await;
    restarted.publish_human_input_ready(event.clone()).await.unwrap();
    let saved = reloaded.get(&event.event_id).await.unwrap().unwrap();
    assert_eq!(saved.status, HumanInputRequestStatus::Notifying);
    assert_eq!(saved.delivery_attempts, 1); assert_eq!(saved.deadline_ms, 1000);
    assert_eq!(provider.events.lock().await.len(), 1);
}

#[tokio::test]
async fn preflight_cancellation_prevents_send_and_late_ack_cannot_reactivate() {
    for before in [true, false] {
        let repo = Arc::new(MemoryHumanInputRequestRepo::new());
        let provider = Arc::new(Provider::default());
        provider.pause_before_send.store(before, Ordering::SeqCst);
        provider.pause_during_send.store(!before, Ordering::SeqCst);
        let channel = Arc::new(service(repo.clone(), provider.clone()).await);
        let event = ready(false, HumanInputNotificationMode::DirectAssignee);
        let sending = { let c = channel.clone(); let e = event.clone(); tokio::spawn(async move { c.publish_human_input_ready(e).await }) };
        tokio::time::timeout(std::time::Duration::from_secs(3), provider.entered.notified()).await.unwrap();
        provider.stale_notification.store(true, Ordering::SeqCst);
        provider.release.notify_one(); sending.await.unwrap().unwrap();
        let saved = repo.get(&event.event_id).await.unwrap().unwrap();
        assert_eq!(saved.status, HumanInputRequestStatus::Cancelled);
        assert!(saved.provider_message_ref.is_none()); assert!(saved.active_slot_key.is_none());
        assert_eq!(provider.events.lock().await.len(), usize::from(!before));
    }
}

#[tokio::test]
async fn concurrent_pending_replays_have_one_external_invocation() {
    let repo = Arc::new(MemoryHumanInputRequestRepo::new());
    let provider = Arc::new(Provider::default());
    let a = service(repo.clone(), provider.clone()).await;
    let b = service(repo.clone(), provider.clone()).await;
    let event = ready(false, HumanInputNotificationMode::DirectAssignee);
    let (x, y) = tokio::join!(a.publish_human_input_ready(event.clone()), b.publish_human_input_ready(event.clone()));
    x.unwrap(); y.unwrap();
    assert_eq!(provider.events.lock().await.len(), 1);
    let saved = repo.get(&event.event_id).await.unwrap().unwrap();
    assert_eq!(saved.status, HumanInputRequestStatus::Active); assert_eq!(saved.delivery_attempts, 1);
}

#[tokio::test]
async fn proactive_recovery_expires_original_slot_and_promotes_saved_queue() {
    let repo = Arc::new(MemoryHumanInputRequestRepo::new());
    let provider = Arc::new(Provider::default());
    let channel = service(repo.clone(), provider.clone()).await;
    let mut first = ready(true, HumanInputNotificationMode::DirectAssignee);
    first.timeout_deadline_ms = Some(43);
    let later = ready(false, HumanInputNotificationMode::DirectAssignee);
    channel.publish_human_input_ready(first.clone()).await.unwrap();
    channel.publish_human_input_ready(later.clone()).await.unwrap();
    let before = repo.get(&later.event_id).await.unwrap().unwrap();
    assert_eq!(before.status, HumanInputRequestStatus::Queued);
    provider.events.lock().await.clear(); provider.clock.store(44, Ordering::SeqCst);
    let covered = channel.recover_human_input_requests(&later.run_id, &later.session_id).await.unwrap();
    assert_eq!(covered.len(), 2);
    assert_eq!(repo.get(&first.event_id).await.unwrap().unwrap().status, HumanInputRequestStatus::Expired);
    let active = repo.get(&later.event_id).await.unwrap().unwrap();
    assert_eq!(active.status, HumanInputRequestStatus::Active);
    assert_eq!(active.deadline_ms, before.deadline_ms); assert_eq!(active.notification_text, before.notification_text);
    channel.recover_human_input_requests(&later.run_id, &later.session_id).await.unwrap();
    assert_eq!(provider.events.lock().await.len(), 1);
}

#[tokio::test]
async fn waiting_run_releases_queue_head_owned_by_terminal_run() {
    let repo = Arc::new(MemoryHumanInputRequestRepo::new());
    let provider = Arc::new(Provider::default());
    let channel = service(repo.clone(), provider.clone()).await;
    let mut first = ready(true, HumanInputNotificationMode::DirectAssignee);
    first.run_id = "earlier-run".into();
    let later = ready(false, HumanInputNotificationMode::DirectAssignee);
    channel.publish_human_input_ready(first.clone()).await.unwrap();
    channel.publish_human_input_ready(later.clone()).await.unwrap();
    *provider.stale_node.lock().await = Some(first.node_id.clone());
    provider.events.lock().await.clear();
    channel.recover_human_input_requests(&later.run_id, &later.session_id).await.unwrap();
    assert_eq!(repo.get(&first.event_id).await.unwrap().unwrap().status, HumanInputRequestStatus::Cancelled);
    assert_eq!(repo.get(&later.event_id).await.unwrap().unwrap().status, HumanInputRequestStatus::Active);
    assert_eq!(provider.events.lock().await.len(), 1);
}

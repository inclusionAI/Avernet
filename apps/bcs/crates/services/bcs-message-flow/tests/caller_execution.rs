use std::sync::Arc;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore, managed_delivery::ManagedMessageDelivery, queued_group::QueuedGroupPreparation};
use bcs_service_api::{CallerContext, GroupCoreService, HumanActor, ManagedDeliveryPreparationService, ManagedMessageDeliveryService, MessageFlowService, WebSendCommand};
use bcs_domain::{message_delivery::MessageDeliveryStatus as Status};
use serde_json::json;
#[path = "support/session.rs"] mod session;
#[path = "support/operation_output.rs"] mod output_support;
#[path = "../../../test-support/message_flow_contract_support.rs"] mod support;

fn command(id: &str, name: &str, value: &str) -> WebSendCommand {
    WebSendCommand {
        caller_ingress: None,
        speaking_lease: None,
        caller_execution: None,
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(), session_id: Some("group-1:headers".into()), from_actor_id: "human_1".into(),
        from_name: None, message: "hello".into(), mentions: vec!["bot-driver".into()], attachments: None,
        thinking: None, idempotency_key: Some(id.into()), source_im_message_id: None, channel_sender_identity: None,
        sender_conn_id: None, provider_bypass_headers: vec![(name.into(), value.into())],
    }
}

async fn fixture() -> (support::FlowTestSupport, Arc<BcsMessageFlow>, Arc<ManagedMessageDelivery>) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.set_delivery_target("bot-driver", support::FakeRegistryService::provider_target("bot-driver")).await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.participants.retain(|p| p.bot_uuid != "bot-observer");
    group.group_strategy = bcs_domain::GroupStrategy::Chat;
    support.group.upsert(group.clone()).await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let mut flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
        .with_execution_cache(Arc::new(bcs_cache_local::InMemoryCachePlugin::new()))
        .with_message_repo(repo).with_managed_deliveries(service.clone())
        .with_bot_run_context(Arc::new(MemoryBotRunContextStore::new()))
        .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(),10),("bot-observer".into(),10)]))
        .with_session_management(Arc::new(session::StaticSessionManagement::new(session::test_session("group-1:headers","group-1",group.participants))));
    flow.queue_persistable_headers = vec!["x-routing-zone".into()];
    (support, Arc::new(flow), service)
}


use bcs_service_api::application::caller_execution::{CallerExecution, ExecutionToken};
use bcs_cache_api::CacheSetMode;
use bcs_protocol::BcsFrame;

fn caller_command(id: &str, user: &str, token: &str) -> WebSendCommand {
    let mut cmd = command(id, "x-routing-zone", "blue");
    cmd.source_im_message_id = Some(id.into());
    cmd.caller_execution = Some(CallerExecution {
        reference: uuid::Uuid::new_v4().to_string(), token: ExecutionToken::new(token.into(), "iam-token".into()),
        token_type: "iam-token".into(), user_id: user.into(), binding_id: "binding-1".into(),
        source_message_id: id.into(), target_bot_id: "bot-driver".into(),
    });
    cmd
}

#[tokio::test]
async fn caller_queue_is_scoped_secret_free_and_survives_another_consumer() {
    let (support, mut flow, service) = fixture().await;
    for (id, user, token) in [("im-a", "user-a", "credential-a"), ("im-b", "user-b", "credential-b")] {
        let cmd = caller_command(id, user, token);
        assert!(!format!("{cmd:?}").contains(token));
        flow.handle_web_send(cmd).await.unwrap();
    }
    let rows = service.snapshot(None).await.unwrap();
    assert_eq!(rows.len(), 2);
    assert!(rows.iter().all(|r| r.state.status == Status::Queued));
    let json = serde_json::to_string(&rows).unwrap();
    assert!(!json.contains("credential-a") && !json.contains("credential-b"));
    // Reconstruct a consumer with no per-process credential map.
    let (_, mut other, _) = fixture().await;
    let consumer = Arc::get_mut(&mut other).unwrap();
    consumer.execution_cache = flow.execution_cache.clone();
    consumer.message_repo = flow.message_repo.clone();
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&other), deliveries: service.clone() };
    for row in &rows {
        let prepared = preparer.prepare(row).await.unwrap();
        let BcsFrame::Request(frame) = &prepared.command.frame else { panic!("expected request") };
        let ext = &frame.params.as_ref().unwrap()["extensions"];
        let user = ext["caller_context"]["subject"]["ref"].as_str().unwrap();
        let token = ext["caller_execution_token"]["token"].as_str().unwrap();
        assert_eq!(token, if user == "user-a" { "credential-a" } else { "credential-b" });
        assert_eq!(ext["caller_execution_token"]["type"], "iam-token");
        assert!(!format!("{:?}", prepared.command).contains(token));
        assert!(!prepared.transport_context_json.to_string().contains(token));
        assert_eq!(prepared.command.provider_bypass_headers, vec![("x-routing-zone".into(), "blue".into())]);
        // Exact retries do not refresh the record or TTL.
        let reference = row.semantic_projection_json["caller_execution"]["reference"].as_str().unwrap();
        let key = format!("bcs:caller-execution:v1:{reference}");
        let cache = flow.execution_cache.as_ref().unwrap();
        let before = cache.get_value(&key).await.unwrap();
        preparer.prepare(row).await.unwrap();
        assert_eq!(cache.get_value(&key).await.unwrap(), before);
    }
    assert!(support.bot_delivery.frames().await.is_empty());
    Arc::get_mut(&mut flow).unwrap().execution_cache = None;
    assert!(flow.handle_web_send(caller_command("no-cache", "user", "secret-marker")).await.is_err());
    assert_eq!(service.snapshot(None).await.unwrap().len(), 2);
}

#[tokio::test]
async fn caller_missing_expired_or_mismatched_credentials_fail_closed() {
    let (_, flow, service) = fixture().await;
    flow.handle_web_send(caller_command("im", "user", "credential-only-in-cache")).await.unwrap();
    let row = service.snapshot(None).await.unwrap().remove(0);
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&flow), deliveries: service };
    let reference = row.semantic_projection_json["caller_execution"]["reference"].as_str().unwrap();
    let key = format!("bcs:caller-execution:v1:{reference}");
    let cache = flow.execution_cache.as_ref().unwrap();
    let original = cache.get_value(&key).await.unwrap().unwrap();
    for field in ["target_bot_id", "binding_id", "user_id", "source_message_id", "group_id", "session_id"] {
        let mut bad = row.clone();
        bad.semantic_projection_json["caller_execution"][field] = json!("another-scope");
        assert!(preparer.prepare(&bad).await.is_err(), "{field}");
    }
    let mut record: serde_json::Value = serde_json::from_slice(&original).unwrap();
    record["expires_at_ms"] = json!(0);
    cache.set_value(&key, serde_json::to_vec(&record).unwrap(), Some(std::time::Duration::from_secs(60)), CacheSetMode::Upsert).await.unwrap();
    assert!(preparer.prepare(&row).await.is_err());
    cache.delete(&key).await.unwrap();
    let error = preparer.prepare(&row).await.err().unwrap();
    assert!(!error.to_string().contains("credential-only-in-cache"));
}

#[tokio::test]
async fn caller_rejects_ws_and_legacy_http_keeps_extensions() {
    let (support, mut flow, service) = fixture().await;
    support.registry.set_delivery_target("bot-driver", bcs_service_api::BotDeliveryTarget::WebSocket { bot_id: "bot-driver".into() }).await;
    assert!(flow.handle_web_send(caller_command("ws", "user", "credential")).await.is_err());
    assert!(service.snapshot(None).await.unwrap().is_empty());
    support.registry.set_delivery_target("bot-driver", support::FakeRegistryService::provider_target("bot-driver")).await;
    // Legacy nonqueued delivery must have exactly the same caller contract.
    Arc::get_mut(&mut flow).unwrap().group_delivery_limits.clear();
    flow.handle_web_send(caller_command("legacy", "user", "legacy-credential")).await.unwrap();
    let frames = support.bot_delivery.frames().await;
    assert!(serde_json::to_string(&frames[0]).unwrap().contains("legacy-credential"));
    let mut plain = command("plain", "x-routing-zone", "blue");
    plain.caller_execution = None;
    flow.handle_web_send(plain).await.unwrap();
    let frames = support.bot_delivery.frames().await;
    assert!(!serde_json::to_string(frames.last().unwrap()).unwrap().contains("caller_execution_token"));
}

#[derive(Default)]
struct ObservedCache {
    inner: bcs_cache_local::InMemoryCachePlugin,
    writes: std::sync::atomic::AtomicUsize,
    reads: std::sync::atomic::AtomicUsize,
    fail_writes: std::sync::atomic::AtomicBool,
    fail_reads: std::sync::atomic::AtomicBool,
}

#[async_trait::async_trait]
impl bcs_cache_api::CachePlugin for ObservedCache {
    async fn get_value(&self, key: &str) -> bcs_cache_api::CacheResult<Option<Vec<u8>>> {
        self.reads.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        if self.fail_reads.load(std::sync::atomic::Ordering::SeqCst) {
            return Err(bcs_cache_api::CacheError::Backend("sensitive-backend-diagnostic".into()));
        }
        self.inner.get_value(key).await
    }
    async fn set_value(&self, key: &str, value: Vec<u8>, ttl: Option<std::time::Duration>, mode: CacheSetMode) -> bcs_cache_api::CacheResult<bool> {
        self.writes.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        assert_eq!(ttl, Some(std::time::Duration::from_secs(300)));
        assert_eq!(mode, CacheSetMode::InsertOnly);
        if self.fail_writes.load(std::sync::atomic::Ordering::SeqCst) {
            return Err(bcs_cache_api::CacheError::Backend("sensitive-backend-diagnostic".into()));
        }
        self.inner.set_value(key, value, ttl, mode).await
    }
    async fn delete(&self, key: &str) -> bcs_cache_api::CacheResult<bool> { self.inner.delete(key).await }
    async fn expire(&self, key: &str, ttl: std::time::Duration) -> bcs_cache_api::CacheResult<bool> { self.inner.expire(key, ttl).await }
    async fn ttl(&self, key: &str) -> bcs_cache_api::CacheResult<bcs_cache_api::CacheTtl> { self.inner.ttl(key).await }
    async fn hash_get(&self, key: &str, field: &str) -> bcs_cache_api::CacheResult<Option<Vec<u8>>> { self.inner.hash_get(key, field).await }
    async fn hash_get_all(&self, key: &str) -> bcs_cache_api::CacheResult<std::collections::BTreeMap<String, Vec<u8>>> { self.inner.hash_get_all(key).await }
    async fn hash_set(&self, key: &str, field: &str, value: Vec<u8>) -> bcs_cache_api::CacheResult<()> { self.inner.hash_set(key, field, value).await }
    async fn hash_set_many(&self, key: &str, fields: std::collections::BTreeMap<String, Vec<u8>>) -> bcs_cache_api::CacheResult<()> { self.inner.hash_set_many(key, fields).await }
    async fn hash_delete(&self, key: &str, field: &str) -> bcs_cache_api::CacheResult<bool> { self.inner.hash_delete(key, field).await }
}

#[tokio::test]
async fn caller_cache_cost_is_bounded_and_cache_errors_are_sanitized() {
    use std::sync::atomic::Ordering::SeqCst;
    let (support, mut flow, service) = fixture().await;
    let cache = Arc::new(ObservedCache::default());
    Arc::get_mut(&mut flow).unwrap().execution_cache = Some(cache.clone());
    cache.fail_writes.store(true, SeqCst);
    let error = flow.handle_web_send(caller_command("failed-write", "user", "credential")).await.unwrap_err();
    assert!(!error.to_string().contains("sensitive-backend-diagnostic"));
    assert!(service.snapshot(None).await.unwrap().is_empty());
    assert!(support.bot_delivery.frames().await.is_empty());
    cache.fail_writes.store(false, SeqCst);
    flow.handle_web_send(caller_command("accepted", "user", "credential")).await.unwrap();
    assert_eq!(cache.writes.load(SeqCst), 2);
    assert_eq!(cache.reads.load(SeqCst), 0);
    let row = service.snapshot(None).await.unwrap().remove(0);
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&flow), deliveries: service };
    let prepared = preparer.prepare(&row).await.unwrap();
    assert_eq!(cache.reads.load(SeqCst), 1);
    assert_eq!(cache.writes.load(SeqCst), 2);
    let mut started = row.clone();
    let mut transport = prepared.transport_context_json.clone();
    transport.as_object_mut().unwrap().remove("context_selection");
    transport["caller_expires_at_ms"] = json!(0);
    started.transport_context_json = Some(transport);
    let error = preparer.before_send(&started, &prepared.command).await.unwrap_err();
    assert!(error.to_string().contains("expired"));
    assert_eq!(cache.reads.load(SeqCst), 1, "final expiry check must not reread cache");
    cache.fail_reads.store(true, SeqCst);
    let error = preparer.prepare(&row).await.err().unwrap();
    assert!(!error.to_string().contains("sensitive-backend-diagnostic"));
}

#[tokio::test]
async fn caller_rejects_other_bot_and_duplicate_does_not_replace_original_identity() {
    let (support, flow, service) = fixture().await;
    flow.handle_web_send(caller_command("duplicate", "original-user", "original-credential")).await.unwrap();
    let duplicate = flow.handle_web_send(caller_command("duplicate", "other-user", "replacement-credential")).await.unwrap();
    assert!(duplicate.queue_admission.unwrap().duplicate);
    let rows = service.snapshot(None).await.unwrap();
    assert_eq!(rows.len(), 1);
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&flow), deliveries: service.clone() };
    let prepared = preparer.prepare(&rows[0]).await.unwrap();
    let wire = serde_json::to_string(&prepared.command.frame).unwrap();
    assert!(wire.contains("original-credential"));
    assert!(!wire.contains("replacement-credential"));
    let mut request = caller_command("fanout", "user", "credential");
    request.caller_execution.as_mut().unwrap().target_bot_id = "another-bot".into();
    assert!(flow.handle_web_send(request).await.is_err());
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn caller_legacy_cache_failure_releases_unsent_output_runs() {
    use std::sync::atomic::Ordering::SeqCst;
    let (support, mut flow, service) = fixture().await;
    let cache = Arc::new(ObservedCache::default());
    cache.fail_reads.store(true, SeqCst);
    let output = Arc::new(output_support::RecordingOutputAdmission::default());
    let builder = Arc::get_mut(&mut flow).unwrap();
    builder.group_delivery_limits.clear();
    builder.execution_cache = Some(cache.clone());
    builder.output_admission = Some(output.clone());
    assert!(flow.handle_web_send(caller_command("legacy-failed-read", "user", "credential")).await.is_err());
    assert!(support.bot_delivery.frames().await.is_empty());
    assert!(service.snapshot(None).await.unwrap().is_empty());
    let registered = output.registered_runs.lock().unwrap();
    assert_eq!(registered.len(), 1);
    assert_eq!(registered[0].1.len(), 1);
    assert_eq!(*output.released_runs.lock().unwrap(), *registered);
    assert_eq!(cache.writes.load(SeqCst), 1);
    assert_eq!(cache.reads.load(SeqCst), 1);
}

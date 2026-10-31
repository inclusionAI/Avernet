use super::*;
use crate::{MemoryBotRunContextStore, RedisBotRunContextStore};

fn context(run: &str, deadline_ms: u64) -> BotRunContext {
    BotRunContext {
        run_id: run.into(), bot_id: "bot".into(), group_id: "group".into(),
        bcs_session_id: Some("group:session".into()), deadline_ms, terminal: false,
    }
}

fn scope() -> BotRunScope {
    BotRunScope { bot_id: "bot".into(), group_id: "group".into(), session_id: "group:session".into() }
}

#[tokio::test]
async fn memory_decorator_preserves_run_context_port_contract() {
    let tracked = TrackedBotRunContext::new(Arc::new(MemoryBotRunContextStore::new()));
    bcs_test_support::contract::port::bot_run_context_port_contract_tests(&tracked, "tracked-memory").await;
}

#[tokio::test]
async fn cache_decorator_preserves_run_context_port_contract() {
    let inner = RedisBotRunContextStore::new(
        Arc::new(bcs_cache_local::InMemoryCachePlugin::new()), "tracked:".into(), 60_000,
    );
    let tracked = TrackedBotRunContext::new(Arc::new(inner));
    bcs_test_support::contract::port::bot_run_context_port_contract_tests(&tracked, "tracked-cache").await;
}

#[tokio::test]
async fn registration_preserves_admission_and_terminal_can_still_publish() {
    let tracked = TrackedBotRunContext::new(Arc::new(MemoryBotRunContextStore::new()));
    tracked.tracker.cache_run_operation("run", "operation").await;
    tracked.put_context(context("run", u64::MAX)).await;
    assert_eq!(tracked.tracker.output_binding("run").await, Some(Some("operation".into())));
    assert!(tracked.mark_terminal("run").await);
    // Marking the ingress context may precede final frontend publication.
    assert_eq!(tracked.tracker.output_binding("run").await, Some(Some("operation".into())));
    tracked.tracker.cleanup_run("run").await;
    assert_eq!(tracked.tracker.output_binding("run").await, None);
}

#[tokio::test]
async fn direct_alias_context_is_not_an_independent_producer() {
    let tracked = TrackedBotRunContext::new(Arc::new(MemoryBotRunContextStore::new()));
    tracked.put_context(context("run", u64::MAX)).await;
    assert_eq!(tracked.tracker.output_binding("run").await, Some(None));
    assert!(tracked.tracker.register_output_alias("run", "alias", scope()).await);
    // The WS direct-A2A compatibility projection writes an alias context.
    tracked.put_alias_context(context("alias", u64::MAX)).await;
    assert_eq!(tracked.tracker.output_binding("alias").await, None);
    assert_eq!(tracked.tracker.canonical_output_run("alias", &scope()).await.unwrap(), Some("run".into()));
    tracked.tracker.cleanup_run("run").await;
    assert_eq!(tracked.tracker.canonical_output_run("alias", &scope()).await.unwrap(), None);
    assert_eq!(tracked.tracker.output_binding("alias").await, None);
    // No ingress-side repair can recreate lineage from this stale context.
    assert!(tracked.get_context("alias").await.is_some());
    // A delayed ACK projection can finish after canonical cleanup. It is still
    // not a new producer, even though the alias map has already been removed.
    tracked.put_alias_context(context("alias", u64::MAX)).await;
    assert_eq!(tracked.tracker.output_binding("alias").await, None);
}

#[tokio::test]
async fn existing_cleanup_tick_expires_only_independent_lineage() {
    let tracked = TrackedBotRunContext::new(Arc::new(MemoryBotRunContextStore::new()));
    let deadline = bcs_protocol::now_ms() + 10_000;
    tracked.put_context(context("independent", deadline)).await;
    assert!(tracked.tracker.register_output_alias("independent", "alias", scope()).await);
    tracked.tracker.cache_run_operation("admitted", "operation").await;
    tracked.put_context(context("admitted", deadline)).await;
    tracked.put_context(context("live", u64::MAX)).await;
    tracked.cleanup_expired(deadline + 1, 60_000).await;
    assert_eq!(tracked.tracker.output_binding("independent").await, Some(None),
        "timeout/error publication keeps the existing context retention grace");
    tracked.cleanup_expired(deadline + 60_001, 60_000).await;
    assert_eq!(tracked.tracker.output_binding("independent").await, None);
    assert_eq!(tracked.tracker.canonical_output_run("alias", &scope()).await.unwrap(), None);
    assert_eq!(tracked.tracker.output_binding("live").await, Some(None));
    assert_eq!(tracked.tracker.output_binding("admitted").await, Some(Some("operation".into())));
}

#[tokio::test]
async fn restoring_terminal_context_does_not_register_a_live_producer() {
    let tracked = TrackedBotRunContext::new(Arc::new(MemoryBotRunContextStore::new()));
    let mut terminal = context("done", u64::MAX);
    terminal.terminal = true;
    tracked.put_context(terminal).await;
    assert_eq!(tracked.tracker.output_binding("done").await, None);
    tracked.put_context(context("expired", 0)).await;
    tracked.cleanup_expired(60_001, 60_000).await;
    assert_eq!(tracked.tracker.output_binding("expired").await, None);
}

#[tokio::test]
async fn paused_deadline_preserves_existing_output_lineage_without_minting_retired_producers() {
    let tracked=TrackedBotRunContext::new(Arc::new(MemoryBotRunContextStore::new()));
    let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64;
    let old=now+60_000;let next=now+600_000;let run="budget-lineage";
    tracked.put_context(context(run,old)).await;
    tracked.register_active_run(ActiveBotRunContext {canonical_run_id:run.into(),downstream_run_id:run.into(),downstream_session_key:None,scope:scope(),
        transport_owner:bcs_service_api::BotRunTransportOwner::A2a {provider_id:"provider".into(),provider_bot_ref:"remote".into()},
        provider_bypass_headers:vec![],deadline_ms:old}).await.unwrap();
    assert!(tracked.begin_provider_transport(run,old).await);
    assert!(tracked.bind_provider_transport(run,ProviderRunTransport::A2a {binding:bcs_domain::a2a::A2aBinding::HttpJson,update_mode:bcs_domain::a2a::A2aUpdateMode::Stream}).await);
    assert!(tracked.reschedule_deadline(&scope(),run,old,next).await.unwrap());
    tracked.cleanup_expired(old+1,0).await;
    assert_eq!(tracked.tracker.output_binding(run).await,Some(None));
    assert!(tracked.get_context(run).await.is_some());
    tracked.tracker.cleanup_output_run(run).await;
    assert!(!tracked.reschedule_deadline(&scope(),run,next,next+1).await.unwrap());
    assert_eq!(tracked.tracker.output_binding(run).await,None);
}

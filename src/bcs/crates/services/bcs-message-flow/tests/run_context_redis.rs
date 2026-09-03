use std::sync::Arc;

use bcs_cache_local::InMemoryCachePlugin;
use bcs_message_flow::RedisBotRunContextStore;
use bcs_service_api::{
    ActiveBotRunContext, BotRunContext, BotRunContextPort, BotRunScope, BotRunTransportOwner,
    ProviderRunTransport,
};

fn store() -> RedisBotRunContextStore {
    RedisBotRunContextStore::new(
        Arc::new(InMemoryCachePlugin::new()),
        "bcs:".to_string(),
        120_000,
    )
}

fn store_with_cache(cache: Arc<InMemoryCachePlugin>) -> RedisBotRunContextStore {
    RedisBotRunContextStore::new(cache, "bcs:".to_string(), 120_000)
}

#[tokio::test]
async fn redis_bot_run_context_passes_port_contract() {
    let store = store();
    bcs_test_support::contract::port::bot_run_context_port_contract_tests(&store, "redis-contract")
        .await;
}

fn ctx(run_id: &str) -> BotRunContext {
    BotRunContext {
        run_id: run_id.to_string(),
        bot_id: "bot".to_string(),
        group_id: "grp".to_string(),
        bcs_session_id: None,
        deadline_ms: u64::MAX,
        terminal: false,
    }
}

#[tokio::test]
async fn put_and_get_context_roundtrip() {
    let store = store();
    store.put_context(ctx("r1")).await;
    let got = store.get_context("r1").await.unwrap();
    assert_eq!(got.run_id, "r1");
    assert!(!got.terminal);
    assert!(store.get_context("missing").await.is_none());
}

#[tokio::test]
async fn terminal_claim_is_single_winner() {
    let store = store();
    store.put_context(ctx("r2")).await;
    // Two concurrent begin attempts; exactly one acquires the claim.
    let (a, b) = tokio::join!(
        store.try_begin_terminal("r2"),
        store.try_begin_terminal("r2")
    );
    assert!(a ^ b, "exactly one terminal begin should win");
    let first = store.mark_terminal("r2").await;
    let second = store.mark_terminal("r2").await;
    assert!(first, "first mark_terminal should win");
    assert!(!second, "second mark_terminal must not win");
    let got = store.get_context("r2").await.unwrap();
    assert!(got.terminal);
}

#[tokio::test]
async fn release_terminal_drops_claim() {
    let store = store();
    store.put_context(ctx("r3")).await;
    assert!(store.try_begin_terminal("r3").await);
    store.release_terminal("r3").await;
    // After release, a new claim can be acquired.
    assert!(store.try_begin_terminal("r3").await);
}

#[tokio::test]
async fn provider_transport_binds_once_and_rejects_mixed_sources() {
    let store = store();
    assert!(store.begin_provider_transport("run", 10_000).await);
    assert_eq!(
        store.get_provider_transport("run").await,
        Some(ProviderRunTransport::Negotiating)
    );
    assert!(
        store
            .bind_provider_transport("run", ProviderRunTransport::Sse)
            .await
    );
    // Cannot rebind to a conflicting source.
    assert!(
        !store
            .bind_provider_transport("run", ProviderRunTransport::Callback)
            .await
    );
    // Cannot register a duplicate run.
    assert!(!store.begin_provider_transport("run", 20_000).await);
    assert_eq!(
        store.get_provider_transport("run").await,
        Some(ProviderRunTransport::Sse)
    );
}

#[tokio::test]
async fn provider_transport_can_be_marked_terminal_and_cleared() {
    let store = store();
    assert!(store.begin_provider_transport("rt", 10_000).await);
    store.mark_provider_transport_terminal("rt").await;
    assert_eq!(
        store.get_provider_transport("rt").await,
        Some(ProviderRunTransport::Terminal)
    );
    store.clear_provider_transport("rt").await;
    assert!(store.get_provider_transport("rt").await.is_none());
}

#[tokio::test]
async fn cleanup_expired_is_ttl_driven_noop() {
    let store = store();
    store.put_context(ctx("r4")).await;
    assert_eq!(store.cleanup_expired(999_999_999_999, 1).await, 0);
    // Context still present until its own TTL elapses.
    assert!(store.get_context("r4").await.is_some());
}

#[tokio::test]
async fn active_scope_index_is_visible_across_instances_and_cleans_aliases() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let writer = store_with_cache(cache.clone());
    let reader = store_with_cache(cache);
    let mut context = ctx("bcs-run");
    context.bcs_session_id = Some("session-1".to_string());
    writer.put_context(context).await;
    let scope = BotRunScope {
        group_id: "grp".to_string(),
        session_id: "session-1".to_string(),
        bot_id: "bot".to_string(),
    };
    writer
        .register_active_run(ActiveBotRunContext {
            canonical_run_id: "bcs-run".to_string(),
            downstream_run_id: "plugin-run".to_string(),
            downstream_session_key: Some("wire-session-1".to_string()),
            scope: scope.clone(),
            transport_owner: BotRunTransportOwner::WebSocket,
            deadline_ms: u64::MAX,
        })
        .await
        .unwrap();

    assert_eq!(reader.list_active_runs(&scope).await.unwrap().len(), 1);
    assert_eq!(
        reader
            .find_active_run("plugin-run")
            .await
            .unwrap()
            .unwrap()
            .canonical_run_id,
        "bcs-run"
    );
    assert!(reader.mark_terminal("bcs-run").await);
    assert!(reader.remove_active_run(&scope, "bcs-run").await.unwrap());
    assert!(writer.list_active_runs(&scope).await.unwrap().is_empty());
    assert!(
        writer
            .find_active_run("plugin-run")
            .await
            .unwrap()
            .is_none()
    );
}

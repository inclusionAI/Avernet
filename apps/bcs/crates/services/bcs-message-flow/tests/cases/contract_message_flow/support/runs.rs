use super::*;

pub(super) async fn register_active_run(
    store: &Arc<MemoryBotRunContextStore>,
    run_id: &str,
    group_id: &str,
    session_id: &str,
    bot_id: &str,
    transport_owner: BotRunTransportOwner,
) {
    register_active_run_with_downstream(
        store,
        run_id,
        run_id,
        group_id,
        session_id,
        bot_id,
        transport_owner,
    )
    .await;
}

pub(super) async fn register_active_run_with_downstream(
    store: &Arc<MemoryBotRunContextStore>,
    canonical_run_id: &str,
    downstream_run_id: &str,
    group_id: &str,
    session_id: &str,
    bot_id: &str,
    transport_owner: BotRunTransportOwner,
) {
    store
        .put_context(BotRunContext {
            run_id: canonical_run_id.to_string(),
            bot_id: bot_id.to_string(),
            group_id: group_id.to_string(),
            bcs_session_id: Some(session_id.to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;
    store
        .register_active_run(ActiveBotRunContext {
            canonical_run_id: canonical_run_id.to_string(),
            downstream_run_id: downstream_run_id.to_string(),
            downstream_session_key: Some(session_id.to_string()),
            scope: BotRunScope {
                group_id: group_id.to_string(),
                session_id: session_id.to_string(),
                bot_id: bot_id.to_string(),
            },
            transport_owner,
            provider_bypass_headers: Vec::new(),
            deadline_ms: u64::MAX,
        })
        .await
        .unwrap();
}

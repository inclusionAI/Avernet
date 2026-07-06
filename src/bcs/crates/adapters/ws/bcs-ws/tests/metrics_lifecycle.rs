#[test]
fn ws_handlers_use_hooks_without_metrics_context_in_dispatch_state() {
    let bot_dispatcher = include_str!("../src/bot/dispatcher.rs");
    let web_dispatcher = include_str!("../src/web/dispatcher.rs");
    let bot_handler = include_str!("../src/bot/handler.rs");
    let web_handler = include_str!("../src/web/handler.rs");

    assert!(bot_handler.contains("metrics_hook"));
    assert!(web_handler.contains("metrics_hook"));
    assert!(web_dispatcher.contains("WebDispatchOutcome"));
    assert!(!web_handler.contains("is_client_connect_frame"));
    assert!(!bot_dispatcher.contains("metrics_hook"));
    assert!(!web_dispatcher.contains("metrics_hook"));
    assert!(!bot_dispatcher.contains("metrics::counter!"));
    assert!(!bot_handler.contains("metrics::counter!"));
    assert!(!web_dispatcher.contains("metrics::counter!"));
    assert!(!web_handler.contains("metrics::counter!"));
}

#[test]
fn ws_handlers_record_lifecycle_events_and_idle_timeout_reason() {
    let bot_handler = include_str!("../src/bot/handler.rs");
    let web_handler = include_str!("../src/web/handler.rs");

    for source in [bot_handler, web_handler] {
        assert!(source.contains(".accepted("));
        assert!(source.contains(".registered("));
        assert!(source.contains(".error("));
        assert!(source.contains(".closed("));
        assert!(source.contains("connected_at.elapsed()"));
    }

    assert!(web_handler.contains("close_reason = WsCloseReason::IdleTimeout"));
}

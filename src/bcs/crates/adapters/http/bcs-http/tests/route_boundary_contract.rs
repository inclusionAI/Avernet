use std::fs;
use std::path::Path;

const ROUTES: &[&str] = &[
    "src/routes/actors.rs",
    "src/routes/bot_chat.rs",
    "src/routes/bots.rs",
    "src/routes/discover.rs",
    "src/routes/friends.rs",
    "src/routes/group_messages.rs",
    "src/routes/group_requests.rs",
    "src/routes/groups.rs",
    "src/routes/health.rs",
    "src/routes/manifest.rs",
    "src/routes/messages.rs",
    "src/routes/onboard.rs",
];

#[test]
fn selected_routes_are_still_present() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    for route in ROUTES {
        assert!(root.join(route).exists(), "missing route file: {route}");
    }
}

#[test]
fn bot_events_route_does_not_own_admin_terminal_callbacks() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let body = fs::read_to_string(root.join("src/routes/bot_events.rs"))
        .expect("src/routes/bot_events.rs");

    assert!(
        !body.contains("notify_terminal_callback")
            && !body.contains("admin_invocation_terminal"),
        "bot events must reach admin terminal observers through message flow"
    );
}

#[test]
fn selected_routes_do_not_import_concrete_service_crates() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let forbidden = [
        "use bcs_bot::",
        "use bcs_group::",
        "use bcs_message_flow::",
        "use bcs_routing::",
        "use bcs_friend::",
    ];

    for route in ROUTES {
        let body = fs::read_to_string(root.join(route)).expect(route);
        for needle in forbidden {
            assert!(
                !body.contains(needle),
                "{route} imports concrete service crate via {needle}"
            );
        }
    }
}

#[test]
fn caller_module_stays_internal_to_routes() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let body = fs::read_to_string(root.join("src/routes/mod.rs")).expect("src/routes/mod.rs");

    assert!(
        body.lines().any(|line| line.trim() == "mod caller;"),
        "routes::caller should be declared as an internal module"
    );
    assert!(
        !body.lines().any(|line| line.trim() == "pub mod caller;"),
        "routes::caller should not be exposed as public API"
    );
}

#[test]
fn selected_routes_reuse_shared_caller_helpers() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let forbidden = ["fn bot_token_from_headers", "fn validate_container_header"];

    for route in ROUTES {
        let body = fs::read_to_string(root.join(route)).expect(route);
        for needle in forbidden {
            assert!(
                !body.contains(needle),
                "{route} duplicates caller helper {needle}"
            );
        }
    }
}

#[test]
fn selected_routes_do_not_resolve_runtime_environment() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));

    for route in ROUTES {
        let body = fs::read_to_string(root.join(route)).expect(route);
        assert!(
            !body.contains("resolve_env("),
            "{route} resolves runtime environment outside composition/service boundary"
        );
    }
}

#[test]
fn group_messages_route_does_not_own_history_business_logic() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let body = fs::read_to_string(root.join("src/routes/group_messages.rs"))
        .expect("group_messages route");
    let forbidden = [
        "resolve_history_source_bots",
        "fetch_history_from_bot",
        "convert_bot_history_messages",
        "history_metadata_for_message",
        "normalize_group_store_messages",
        "strip_from_prefix",
        "strip_bcs_context_header",
        "group_session_key",
    ];

    for helper in forbidden {
        assert!(
            !body.contains(helper),
            "group_messages route still owns history helper {helper}"
        );
    }
}

#[test]
fn selected_routes_do_not_keep_business_helpers() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let forbidden = [
        "check_bot_ownership",
        "check_visibility_read_access",
        "check_visibility_write_access",
        "target_reachable_for_collab",
        "resolve_history_source_bots",
        "convert_bot_history_messages",
        "build_delivery_frame",
        "parse_routing_policy",
    ];

    for route in ROUTES {
        let body = fs::read_to_string(root.join(route)).expect(route);
        for needle in forbidden {
            assert!(
                !body.contains(needle),
                "{route} still contains business helper {needle}"
            );
        }
    }
}

#[test]
fn selected_routes_do_not_call_core_service_fields_directly() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let forbidden = [
        ".services.registry.",
        ".services.group.",
        ".services.routing.",
        ".services.fusion.",
        ".services.proposal.",
        ".services.friend.",
        ".services.a2a_chat.",
    ];

    for route in ROUTES {
        let body = fs::read_to_string(root.join(route)).expect(route);
        let compact: String = body.chars().filter(|ch| !ch.is_whitespace()).collect();
        for needle in forbidden {
            assert!(
                !compact.contains(needle),
                "{route} calls core service field {needle} directly"
            );
        }
    }
}

#[test]
fn selected_routes_do_not_own_background_tasks_or_error_sniffing() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let forbidden = [
        "tokio::spawn",
        "message.contains(\"expired\")",
        "resolve_env(",
        "fn bot_belongs_to_staff",
        "bcs_http_auth::auth::resolve_bot_by_token",
    ];

    for route in ROUTES {
        let body = fs::read_to_string(root.join(route)).expect(route);
        for needle in forbidden {
            assert!(
                !body.contains(needle),
                "{route} still owns boundary concern {needle}"
            );
        }
    }
}

#[test]
fn retired_service_group_route_tree_is_absent() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));

    assert!(
        !root.join("src/routes/service_groups.rs").exists(),
        "legacy service_groups route file should be deleted"
    );

    let routes_mod = fs::read_to_string(root.join("src/routes/mod.rs")).expect("src/routes/mod.rs");
    assert!(
        !routes_mod.contains("service_groups"),
        "routes/mod.rs should not export service_groups"
    );

    let router = fs::read_to_string(root.join("src/router.rs")).expect("src/router.rs");
    assert!(
        !router.contains("/service-groups"),
        "router should not register retired /service-groups paths"
    );

    for route in ROUTES {
        let body = fs::read_to_string(root.join(route)).expect(route);
        assert!(
            !body.contains("state.service_group_env"),
            "{route} should not read retired service_group_env"
        );
        assert!(
            !body.contains("state.services.service_group_template"),
            "{route} should not call retired service_group_template service"
        );
        assert!(
            !body.contains("state.services.service_group_instance"),
            "{route} should not call retired service_group_instance service"
        );
    }
}

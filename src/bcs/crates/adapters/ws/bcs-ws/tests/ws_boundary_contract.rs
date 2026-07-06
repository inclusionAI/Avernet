use std::fs;
use std::path::Path;

const WS_SOURCES: &[&str] = &[
    "src/bot/connection_registry.rs",
    "src/bot/dispatcher.rs",
    "src/bot/handler.rs",
    "src/bot/mod.rs",
    "src/gateway/abort_manager.rs",
    "src/gateway/chat_handler.rs",
    "src/gateway/chat_types.rs",
    "src/gateway/context.rs",
    "src/gateway/event_broadcaster.rs",
    "src/gateway/mod.rs",
    "src/gateway/ws_handler.rs",
    "src/shared/mod.rs",
    "src/shared/run_channels.rs",
    "src/web/connection_registry.rs",
    "src/web/dispatcher.rs",
    "src/web/frontend_delivery.rs",
    "src/web/handler.rs",
    "src/web/mod.rs",
];

#[test]
fn selected_ws_sources_are_still_present() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    for source in WS_SOURCES {
        assert!(
            root.join(source).exists(),
            "missing ws source file: {source}"
        );
    }
}

#[test]
fn ws_adapter_does_not_import_concrete_service_crates() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let forbidden = [
        "use bcs_bot::",
        "use bcs_group::",
        "use bcs_message_flow::",
        "use bcs_routing::",
        "use bcs_friend::",
    ];

    for source in WS_SOURCES {
        let body = fs::read_to_string(root.join(source)).expect(source);
        for needle in forbidden {
            assert!(
                !body.contains(needle),
                "{source} imports concrete service crate via {needle}"
            );
        }
    }
}

#[test]
fn ws_adapter_does_not_hold_or_call_core_service_container() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let forbidden_paths = [
        ".services.registry",
        ".services.group",
        ".services.routing",
        ".services.message_flow",
        ".services.bot_management",
        ".services.group_query",
        ".services.group_management",
    ];

    for source in WS_SOURCES {
        let body = fs::read_to_string(root.join(source)).expect(source);
        assert!(
            !contains_identifier(&body, "Services"),
            "{source} holds the core service container"
        );

        let compact: String = body.chars().filter(|ch| !ch.is_whitespace()).collect();
        for needle in forbidden_paths {
            let compact_needle: String = needle.chars().filter(|ch| !ch.is_whitespace()).collect();
            assert!(
                !compact.contains(&compact_needle),
                "{source} reaches through core service container via {needle}"
            );
        }
    }
}

fn contains_identifier(body: &str, ident: &str) -> bool {
    body.match_indices(ident).any(|(start, _)| {
        let before = body[..start].chars().next_back();
        let after = body[start + ident.len()..].chars().next();
        !is_ident_char(before) && !is_ident_char(after)
    })
}

fn is_ident_char(ch: Option<char>) -> bool {
    ch.map(|ch| ch == '_' || ch.is_ascii_alphanumeric())
        .unwrap_or(false)
}

#[test]
fn ws_adapter_uses_application_services_instead_of_core_traits() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let forbidden = [
        "BotRegistryCoreService",
        "GroupCoreService",
        "RoutingCoreService",
    ];

    for source in WS_SOURCES {
        let body = fs::read_to_string(root.join(source)).expect(source);
        for needle in forbidden {
            assert!(
                !body.contains(needle),
                "{source} depends on core service trait {needle}"
            );
        }
    }
}

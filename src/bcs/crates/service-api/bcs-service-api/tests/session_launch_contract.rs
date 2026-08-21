use std::sync::Arc;

use bcs_service_api::{
    CreateSessionLaunch, ReactivateSessionLaunch, SessionCaller, SessionLaunchRequest,
    SessionLaunchService,
};

fn accepts_object_safe_service(_: Arc<dyn SessionLaunchService>) {}

#[test]
fn neutral_command_carries_no_transport_identity() {
    let request = SessionLaunchRequest {
        caller: SessionCaller::Human {
            actor_id: "human_alice".into(),
            owner_id: "alice".into(),
            display_name: Some("Alice".into()),
        },
        group_id: "group-1".into(),
        requested_creator: Some("bot-owned".into()),
        title: Some("task".into()),
        kind: None,
        input: Some(serde_json::json!({"query": "hello", "custom": 1})),
        meta: Some(serde_json::json!({"channel": {"source": "ding"}})),
        public_creator_role: None,
        context_delivery: None,
    };

    let create = CreateSessionLaunch {
        request: request.clone(),
    };
    let reactivate = ReactivateSessionLaunch {
        session_id: "session-1".into(),
        request,
    };

    assert_eq!(create.request.caller.actor_id(), "human_alice");
    assert_eq!(create.request.caller.owner_id(), Some("alice"));
    assert_eq!(create.request.caller.display_name(), Some("Alice"));
    assert_eq!(reactivate.session_id, "session-1");
}

#[test]
fn bot_caller_exposes_only_its_actor_id() {
    let caller = SessionCaller::Bot {
        bot_uuid: "bot-driver".into(),
    };

    assert_eq!(caller.actor_id(), "bot-driver");
    assert_eq!(caller.owner_id(), None);
    assert_eq!(caller.display_name(), None);
}

#[allow(dead_code)]
fn object_safety_compile_check(service: Arc<dyn SessionLaunchService>) {
    accepts_object_safe_service(service);
}

//! Request semantics of the SDK's camelCase send configuration.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "REST wire assertions")]
mod rest_common;

use a2a::{
    Message, Part, Role, SendMessageConfiguration, SendMessageRequest, SendMessageResponse,
    TaskPushNotificationConfig, TaskState,
};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use rest_common::*;
use serde_json::json;

fn sdk_send(configuration: SendMessageConfiguration) -> Request<Body> {
    let mut message = Message::new(Role::User, vec![Part::text("hello")]);
    message.message_id = "sdk-options".into();
    let request = SendMessageRequest {
        message,
        configuration: Some(configuration),
        metadata: None,
        tenant: None,
    };
    Request::post(format!("{GROUPS_PREFIX}/grp-research-01/message:send"))
        .header("content-type", "application/json")
        .body(Body::from(serde_json::to_vec(&request).unwrap()))
        .unwrap()
}

fn options() -> SendMessageConfiguration {
    SendMessageConfiguration {
        accepted_output_modes: None,
        task_push_notification_config: None,
        history_length: None,
        return_immediately: Some(true),
    }
}

#[tokio::test]
async fn sdk_config_validates_modes_and_push_without_admitting_a_task() {
    let p = ports();
    let app = test_router(&group_stack(&p));

    for config in [
        SendMessageConfiguration {
            accepted_output_modes: Some(vec!["video/mp4".into()]),
            ..options()
        },
        SendMessageConfiguration {
            task_push_notification_config: Some(TaskPushNotificationConfig {
                url: "https://example.invalid/notification".into(),
                id: None,
                task_id: String::new(),
                token: None,
                authentication: None,
                tenant: None,
            }),
            ..options()
        },
    ] {
        let (status, body) = response_of(&app, sdk_send(config)).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{body:?}");
        assert_eq!(code_of(&body), "INVALID_ARGUMENT");
    }
    let (status, body) = response_of(&app, rest_list("pageSize=2")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body.unwrap()["tasks"], json!([]));
}

#[tokio::test]
async fn sdk_send_preserves_blocking_default_and_honors_history_length() {
    let p = ports();
    let app = test_router(&group_stack(&p));

    // Without returnImmediately, an outstanding task reaches the configured
    // blocking wait limit instead of answering immediately with a 200.
    let (status, body) = response_of(&app, sdk_send(SendMessageConfiguration {
        return_immediately: None,
        ..options()
    })).await;
    assert_eq!(status, StatusCode::GATEWAY_TIMEOUT, "{body:?}");
    let SendMessageResponse::Task(task) = serde_json::from_value(body.unwrap()).unwrap() else {
        panic!("timed-out group send still returns the current Task");
    };
    let mut snapshot = snapshot(&task.id, TaskStatus::Working, None, NOW);
    snapshot.sid = Sid(task.context_id);
    snapshot.scope = TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("bot:pa-001".into()),
    };
    let older = Message::new(Role::User, vec![Part::text("older")]);
    let newer = Message::new(Role::Agent, vec![Part::text("newer")]);
    snapshot.content.snapshot = Payload::Inline(json!({
        "format_version": 1,
        "units": [],
        "history": [older, newer],
    }));
    p.worker.publish(&snapshot).await;

    let (status, body) = response_of(&app, sdk_send(SendMessageConfiguration {
        history_length: Some(1),
        ..options()
    })).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let SendMessageResponse::Task(response) = serde_json::from_value(body.unwrap()).unwrap() else {
        panic!("replaying the group send returns the Task");
    };
    assert_eq!(response.id, task.id);
    assert_eq!(response.status.state, TaskState::Working);
    assert_eq!(response.history.unwrap(), [newer]);
}

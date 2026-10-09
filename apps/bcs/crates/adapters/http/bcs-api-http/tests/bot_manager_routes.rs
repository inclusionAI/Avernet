//! Task 13 RED/GREEN suite: the Human-only manager list API
//! (`GET/PUT/DELETE /openapi/v1/collaboration/bots/{bot_id}/managers`,
//! spec §6) — real Router + middleware assembly, recording application
//! doubles, and the brief's status-code snapshot.
#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

mod manager_support;

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs_service_api::application::v1::{
    ApplicationError, BotManagerEntry, BotManagerGrantResult, BotManagerPage,
    BotManagerRevokeResult, BotManagerService, GrantBotManager, ListBotManagers, RevokeBotManager,
    ERROR_OWNER_ROLE_REQUIRES_TRANSFER,
};
use serde_json::{Value, json};
use tower::ServiceExt;

use manager_support::{bearer_request, caller, response_json, router_with_state, state_caller};

/// Recording application double: proves the routes forward the VERIFIED
/// Human caller (never a body value) and the exact use-case commands.
#[derive(Default)]
struct RecordingBotManagerService {
    lists: Mutex<Vec<ListBotManagers>>,
    grants: Mutex<Vec<GrantBotManager>>,
    revokes: Mutex<Vec<RevokeBotManager>>,
}

#[async_trait]
impl BotManagerService for RecordingBotManagerService {
    async fn list_managers(
        &self,
        command: ListBotManagers,
    ) -> Result<BotManagerPage, ApplicationError> {
        self.lists
            .lock()
            .expect("lists lock")
            .push(ListBotManagers {
                caller: command.caller.clone(),
                bot_id: command.bot_id.clone(),
                offset: command.offset,
                limit: command.limit,
            });
        Ok(BotManagerPage {
            bot_id: command.bot_id,
            owner_user_id: "user-a".to_string(),
            items: vec![BotManagerEntry {
                user_id: "user-b".to_string(),
                actor_id: "human_user-b".to_string(),
                role: "manager".to_string(),
            }],
            total: 1,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn grant_manager(
        &self,
        command: GrantBotManager,
    ) -> Result<BotManagerGrantResult, ApplicationError> {
        // Owner-target PUT/DELETE is the owner-transfer lane's business, not
        // direct semantics (spec §6): the application layer surfaces it as
        // the 409 `owner_role_requires_transfer` fixed code.
        if command.user_id == "user-a" {
            return Err(ApplicationError::owner_role_requires_transfer(format!(
                "user '{}' is the owner of bot '{}'; \
                 the owner role changes only through the ownership-transfer flow",
                command.user_id, command.bot_id
            )));
        }
        self.grants
            .lock()
            .expect("grants lock")
            .push(command.clone());
        Ok(BotManagerGrantResult {
            bot_id: command.bot_id,
            user_id: command.user_id,
            role: "manager".to_string(),
            changed: true,
        })
    }

    async fn revoke_manager(
        &self,
        command: RevokeBotManager,
    ) -> Result<BotManagerRevokeResult, ApplicationError> {
        if command.user_id == "user-a" {
            return Err(ApplicationError::owner_role_requires_transfer(format!(
                "user '{}' is the owner of bot '{}'; \
                 the owner role changes only through the ownership-transfer flow",
                command.user_id, command.bot_id
            )));
        }
        self.revokes
            .lock()
            .expect("revokes lock")
            .push(command.clone());
        Ok(BotManagerRevokeResult {
            bot_id: command.bot_id,
            user_id: command.user_id,
            revoked: true,
            remaining_team_sources: vec!["team-a".to_string()],
        })
    }
}

fn manager_router(service: Arc<RecordingBotManagerService>) -> axum::Router {
    let state = state_caller(caller()).with_bot_manager_service(service);
    router_with_state(state)
}

fn human_request(method: &str, uri: &str, body: Value) -> Request<Body> {
    bearer_request(method, uri, body, None).with_x_test_auth()
}

/// Attach the header-controlled Gateway Principal the shared verifier
/// requires (Human "staff-1").
trait WithTestAuth {
    fn with_x_test_auth(self) -> Request<Body>;
}

impl WithTestAuth for Request<Body> {
    fn with_x_test_auth(mut self) -> Request<Body> {
        if self.headers().get("x-test-auth").is_none() {
            self.headers_mut()
                .insert("x-test-auth", "yes".parse().expect("header value"));
        }
        self
    }
}

#[tokio::test]
async fn list_managers_requires_human_principal_and_projects_owner_separately() {
    let service = Arc::new(RecordingBotManagerService::default());
    let app = manager_router(service.clone());

    let response = app
        .clone()
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-a/managers",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["bot_id"], "bot-a");
    // The owner is a separate read-only field and never mixes into the page
    // (spec §6).
    assert_eq!(body["data"]["owner_user_id"], "user-a");
    assert_eq!(
        body["data"]["items"],
        json!([{ "user_id": "user-b", "actor_id": "human_user-b", "role": "manager" }])
    );
    assert_eq!(body["data"]["total"], 1);
    assert_eq!(body["data"]["offset"], 0);
    assert_eq!(body["data"]["limit"], 20);

    let lists = service.lists.lock().expect("lists lock");
    assert_eq!(lists.len(), 1);
    assert_eq!(lists[0].bot_id, "bot-a");
    assert_eq!(lists[0].offset, 0);
    assert_eq!(lists[0].limit, 20);
    // The VERIFIED Human, not a request-body value, is the caller.
    assert_eq!(
        lists[0].caller.user.as_ref().expect("verified user").id,
        "staff-1"
    );
}

#[tokio::test]
async fn list_managers_without_principal_is_unauthorized() {
    let app = manager_router(Arc::new(RecordingBotManagerService::default()));
    let response = app
        .oneshot(bearer_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-a/managers",
            json!({}),
            None,
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn list_managers_forwards_explicit_pagination() {
    let service = Arc::new(RecordingBotManagerService::default());
    let app = manager_router(service.clone());
    let response = app
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-a/managers?offset=40&limit=100",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let lists = service.lists.lock().expect("lists lock");
    assert_eq!(lists.len(), 1);
    assert_eq!(lists[0].offset, 40);
    assert_eq!(lists[0].limit, 100);
}

#[tokio::test]
async fn grant_manager_requires_a_bodyless_request() {
    let service = Arc::new(RecordingBotManagerService::default());
    let app = manager_router(service.clone());

    // The direct grant carries no business body (spec §6): a `team` field
    // (or any other body content) is rejected — the direct API never grows
    // a team parameter.
    for body in [json!({"team": "team-a"}), json!({"anything": 1})] {
        let response = app
            .clone()
            .oneshot(human_request(
                "PUT",
                "/openapi/v1/collaboration/bots/bot-a/managers/user-b",
                body,
            ))
            .await
            .expect("router call");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }
    // An empty JSON object is the no-body representation and stays a valid
    // idempotent grant that 200s.
    let response = app
        .clone()
        .oneshot(human_request(
            "PUT",
            "/openapi/v1/collaboration/bots/bot-a/managers/user-b",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body["data"],
        json!({ "bot_id": "bot-a", "user_id": "user-b", "role": "manager", "changed": true })
    );

    let grants = service.grants.lock().expect("grants lock");
    assert_eq!(grants.len(), 1);
    assert_eq!(grants[0].bot_id, "bot-a");
    assert_eq!(grants[0].user_id, "user-b");
    assert_eq!(
        grants[0].caller.user.as_ref().expect("verified user").id,
        "staff-1"
    );
    assert!(service.revokes.lock().expect("revokes lock").is_empty());
}

#[tokio::test]
async fn revoke_manager_projects_remaining_team_sources() {
    let service = Arc::new(RecordingBotManagerService::default());
    let app = manager_router(service.clone());

    let response = app
        .oneshot(human_request(
            "DELETE",
            "/openapi/v1/collaboration/bots/bot-a/managers/user-b",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    // The brief's RED snapshot: the direct revoke response keeps exposing
    // the team sources it did NOT touch (`revoked` is NOT total loss of
    // permission).
    let body = response_json(response).await;
    assert_eq!(body["data"]["revoked"], true);
    assert_eq!(
        body["data"]["remaining_team_sources"],
        serde_json::json!(["team-a"])
    );

    let revokes = service.revokes.lock().expect("revokes lock");
    assert_eq!(revokes.len(), 1);
    assert_eq!(revokes[0].bot_id, "bot-a");
    assert_eq!(revokes[0].user_id, "user-b");
}

#[tokio::test]
async fn owner_target_uses_the_transfer_lane_conflict_code() {
    let app = manager_router(Arc::new(RecordingBotManagerService::default()));
    for method in ["PUT", "DELETE"] {
        let response = app
            .clone()
            .oneshot(human_request(
                method,
                "/openapi/v1/collaboration/bots/bot-a/managers/user-a",
                json!({}),
            ))
            .await
            .expect("router call");
        assert_eq!(response.status(), StatusCode::CONFLICT);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], ERROR_OWNER_ROLE_REQUIRES_TRANSFER);
    }
}

#[tokio::test]
async fn unconfigured_manager_service_fails_closed() {
    let app = router_with_state(state_caller(caller()));
    let response = app
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-a/managers",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
}
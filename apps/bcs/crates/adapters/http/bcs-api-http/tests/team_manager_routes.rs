//! Task 13 RED/GREEN suite: the trusted team-manager sources slice
//! (`/api/v1/bots/{bot_id}/manager-sources/...`, spec §6.1) — a
//! credential-boundary slice mounted OUTSIDE the generic Gateway
//! Principal middleware. Every status in the brief's RED list runs
//! against real router + middleware assembly.
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
    ApplicationError, ERROR_INVALID_MANAGER_SYNC_SOURCE, TeamManagerMemberRepair,
    TeamManagerSyncCommand, TeamManagerSyncService,
};
use bcs_service_api::types::team_manager_sync::{
    TeamManagerSync, TeamManagerOperation, TeamSyncReceipt, VerifiedTeamManagerService,
    validate_team_sync_command,
};
use serde_json::{Value, json};
use tower::ServiceExt;

use manager_support::{
    bearer_request, caller, response_body, response_json, router_with_state, state_caller,
};

/// The env the canned verified services are bound to (mirrors the
/// application-side scope re-check in the production implementation).
const SLICE_ENV: &str = "test-env";

fn verified_service(
    service_id: &str,
    allowed_bots: Option<Vec<&str>>,
    allowed_teams: Option<Vec<&str>>,
    allowed_operations: Option<Vec<TeamManagerOperation>>,
) -> VerifiedTeamManagerService {
    VerifiedTeamManagerService {
        service_id: service_id.to_string(),
        env: SLICE_ENV.to_string(),
        allowed_bots: allowed_bots.map(|bots| bots.into_iter().map(str::to_string).collect()),
        allowed_teams: allowed_teams.map(|teams| teams.into_iter().map(str::to_string).collect()),
        allowed_operations: allowed_operations.map(|operations| operations.to_vec()),
    }
}

fn receipt(command: &TeamManagerSyncCommand) -> TeamSyncReceipt {
    TeamSyncReceipt {
        operation_id: "op-1".to_string(),
        bot_id: command.bot_id.clone(),
        team_id: command.team_id.clone(),
        operation: command.operation.clone(),
        granted_count: 2,
        revoked_count: 1,
    }
}

/// Recording application double. The REAL production implementation
/// re-checks the command's scopes; this double mirrors that through the
/// SAME shared fail-closed helpers so the route suite proves the HTTP
/// surface rather than a hand-rolled reimplementation.
#[derive(Default)]
struct FakeTeamManagerSyncService {
    verifications: Mutex<Vec<String>>,
    syncs: Mutex<Vec<TeamManagerSyncCommand>>,
    repairs: Mutex<Vec<TeamManagerMemberRepair>>,
}

impl FakeTeamManagerSyncService {
    fn service_for(&self, credential: &str) -> Result<VerifiedTeamManagerService, ApplicationError> {
        match credential {
            // Unrestricted trusted service.
            "credential-good" => Ok(verified_service("team-sync-1", None, None, None)),
            // Scoped to bot-a / team-a only — any other bot/team is 403.
            "credential-restricted" => Ok(verified_service(
                "team-sync-2",
                Some(vec!["bot-a"]),
                Some(vec!["team-a"]),
                None,
            )),
            // sync-only trusted service: a move under this credential is 403.
            "credential-sync-only" => Ok(verified_service(
                "team-sync-3",
                None,
                None,
                Some(vec![TeamManagerOperation::Sync]),
            )),
            _ => Err(ApplicationError::invalid_manager_sync_source(format!(
                "unknown team-manager service credential"
            ))),
        }
    }

    async fn authorize(
        &self,
        command: &TeamManagerSyncCommand,
    ) -> Result<(), ApplicationError> {
        let core_command = TeamManagerSync {
            service: command.service.clone(),
            bot_id: command.bot_id.clone(),
            team_id: command.team_id.clone(),
            operation: command.operation.clone(),
            manager_user_ids: command.manager_user_ids.clone(),
            idempotency_key: command.idempotency_key.clone(),
        };
        validate_team_sync_command(&core_command).map_err(|error| {
            ApplicationError::invalid("invalid_request", error.to_string())
        })?;
        command
            .service
            .authorize_sync(SLICE_ENV, &core_command)
            .map_err(|error| ApplicationError::invalid_manager_sync_source(error.to_string()))
    }
}

#[async_trait]
impl TeamManagerSyncService for FakeTeamManagerSyncService {
    async fn verify_service_credential(
        &self,
        credential: &str,
    ) -> Result<VerifiedTeamManagerService, ApplicationError> {
        self.verifications
            .lock()
            .expect("verifications lock")
            .push(credential.to_string());
        if credential.trim().is_empty() {
            // An empty credential is indistinguishable from a missing one:
            // authentication is required, never a forbidden-service branch.
            return Err(ApplicationError::Unauthenticated);
        }
        self.service_for(credential)
    }

    async fn sync(
        &self,
        command: TeamManagerSyncCommand,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        self.authorize(&command).await?;
        self.syncs.lock().expect("syncs lock").push(command.clone());
        Ok(receipt(&command))
    }

    async fn repair_add_team_member(
        &self,
        command: TeamManagerMemberRepair,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        self.repair_member(command, true).await
    }

    async fn repair_remove_team_member(
        &self,
        command: TeamManagerMemberRepair,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        self.repair_member(command, false).await
    }
}

impl FakeTeamManagerSyncService {
    async fn repair_member(
        &self,
        command: TeamManagerMemberRepair,
        add: bool,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        // Mirrors the production repair lane: one full-snapshot reconcile of
        // the team's member set under the verified service identity.
        let implied = TeamManagerSyncCommand {
            service: command.service.clone(),
            bot_id: command.bot_id.clone(),
            team_id: command.team_id.clone(),
            operation: TeamManagerOperation::Sync,
            manager_user_ids: vec![command.user_id.clone()],
            idempotency_key: command.idempotency_key.clone(),
        };
        self.authorize(&implied).await?;
        self.repairs
            .lock()
            .expect("repairs lock")
            .push(command.clone());
        Ok(TeamSyncReceipt {
            operation_id: "repair-1".to_string(),
            bot_id: command.bot_id,
            team_id: command.team_id,
            operation: TeamManagerOperation::Sync,
            granted_count: u64::from(add),
            revoked_count: u64::from(!add),
        })
    }
}

fn team_router() -> (axum::Router, Arc<FakeTeamManagerSyncService>) {
    let service = Arc::new(FakeTeamManagerSyncService::default());
    let state = state_caller(caller()).with_team_manager_sync_service(service.clone());
    (router_with_state(state), service)
}

const TEAM_PATH: &str = "/api/v1/bots/bot-a/manager-sources/teams/team-a";
const MEMBERS_PATH: &str = "/api/v1/bots/bot-a/manager-sources/teams/team-a/members";

fn sync_body() -> Value {
    json!({
        "operation": "sync",
        "manager_user_ids": ["user-a", "user-c"],
        "idempotency_key": "sync-uuid",
    })
}

#[tokio::test]
async fn sync_without_credential_is_unauthorized() {
    let (app, service) = team_router();
    let response = app
        .oneshot(bearer_request("PUT", TEAM_PATH, sync_body(), None))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    // No credential reaches the application unauthorized requests touch
    // no permission state.
    assert!(service.syncs.lock().expect("syncs lock").is_empty());
}

#[tokio::test]
async fn sync_with_unknown_credential_is_forbidden() {
    let (app, service) = team_router();
    let response = app
        .oneshot(bearer_request(
            "PUT",
            TEAM_PATH,
            sync_body(),
            Some("credential-bogus"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let (status, body) = response_body(response).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    let body: Value = serde_json::from_str(&body).expect("JSON body");
    assert_eq!(body["data"]["error_code"], ERROR_INVALID_MANAGER_SYNC_SOURCE);
    // The rejected credential value never echoes back in the response.
    assert!(!body.to_string().contains("credential-bogus"));
    assert!(service.syncs.lock().expect("syncs lock").is_empty());
}

#[tokio::test]
async fn sync_with_credential_outside_scopes_is_forbidden() {
    let (app, service) = team_router();
    // cred team scope violation: restricted credential manages team-a only.
    let response = app
        .clone()
        .oneshot(bearer_request(
            "PUT",
            "/api/v1/bots/bot-a/manager-sources/teams/team-b",
            sync_body(),
            Some("credential-restricted"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // Bot scope violation: restricted credential manages bot-a only.
    let response = app
        .oneshot(bearer_request(
            "PUT",
            "/api/v1/bots/bot-b/manager-sources/teams/team-a",
            sync_body(),
            Some("credential-restricted"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // The rejected command never reached the sync phase.
    assert!(service.syncs.lock().expect("syncs lock").is_empty());
}

#[tokio::test]
async fn move_under_sync_only_credential_is_forbidden() {
    let (app, _service) = team_router();
    let response = app
        .oneshot(bearer_request(
            "PUT",
            TEAM_PATH,
            json!({
                "operation": "move",
                "new_team_id": "team-new",
                "manager_user_ids": ["user-a"],
                "idempotency_key": "move-uuid",
            }),
            Some("credential-sync-only"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn sync_with_valid_credential_needs_no_human_principal() {
    let (app, service) = team_router();
    // The team slice is exempt from the generic Human login middleware —
    // the verified SERVICE identity is the principal. No `x-test-auth`
    // header is set.
    let response = app
        .clone()
        .oneshot(bearer_request(
            "PUT",
            TEAM_PATH,
            json!({
                "operation": "sync",
                "manager_user_ids": [],
                "idempotency_key": "sync-uuid",
            }),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(
        body["data"],
        json!({
            "operation_id": "op-1",
            "bot_id": "bot-a",
            "team_id": "team-a",
            "operation": "sync",
            "granted_count": 2,
            "revoked_count": 1,
        })
    );

    // RED proof: the service identity + scopes reached the application from
    // the VERIFIED credential, never from a body value. A body that tries
    // to smuggle `service_id` never parses (unknown field -> 400).
    // (The guard is scoped so the next request may re-enter the fake.)
    {
        let syncs = service.syncs.lock().expect("syncs lock");
        assert_eq!(syncs.len(), 1);
        assert_eq!(syncs[0].service, verified_service("team-sync-1", None, None, None));
        assert_eq!(syncs[0].bot_id, "bot-a");
        assert_eq!(syncs[0].team_id, "team-a");
        assert_eq!(syncs[0].operation, TeamManagerOperation::Sync);
        assert_eq!(syncs[0].idempotency_key, "sync-uuid");
        assert_eq!(syncs[0].manager_user_ids, Vec::<String>::new());
    }

    let response = app
        .oneshot(bearer_request(
            "PUT",
            TEAM_PATH,
            json!({
                "operation": "sync",
                "manager_user_ids": [],
                "idempotency_key": "sync-uuid",
                "service_id": "body-forged-service",
            }),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(service.syncs.lock().expect("syncs lock").len() == 1);
}

#[tokio::test]
async fn duplicate_snapshot_ids_are_accepted_verbatim() {
    let (app, service) = team_router();
    let response = app
        .oneshot(bearer_request(
            "PUT",
            TEAM_PATH,
            json!({
                "operation": "sync",
                "manager_user_ids": ["user-b", "user-a", "user-b"],
                "idempotency_key": "sync-uuid",
            }),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let syncs = service.syncs.lock().expect("syncs lock");
    assert_eq!(syncs[0].manager_user_ids, vec!["user-b", "user-a", "user-b"]);
}

#[tokio::test]
async fn move_body_schema_is_enforced() {
    let (app, _service) = team_router();
    let valid_move = json!({
        "operation": "move",
        "new_team_id": "team-new",
        "manager_user_ids": ["user-a", "user-c"],
        "idempotency_key": "move-sync-uuid",
    });
    let response = app
        .clone()
        .oneshot(bearer_request("PUT", TEAM_PATH, valid_move, Some("credential-good")))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);

    // move without new_team_id -> 400
    let response = app
        .clone()
        .oneshot(bearer_request(
            "PUT",
            TEAM_PATH,
            json!({
                "operation": "move",
                "manager_user_ids": ["user-a"],
                "idempotency_key": "move-sync-uuid",
            }),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    // move whose target equals the URL team -> 400 (store-rejected shape,
    // validated fail-closed in the application lane).
    let response = app
        .clone()
        .oneshot(bearer_request(
            "PUT",
            TEAM_PATH,
            json!({
                "operation": "move",
                "new_team_id": "team-a",
                "manager_user_ids": ["user-a"],
                "idempotency_key": "move-sync-uuid",
            }),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn sync_body_rejects_new_team_id() {
    let (app, _service) = team_router();
    let response = app
        .oneshot(bearer_request(
            "PUT",
            TEAM_PATH,
            json!({
                "operation": "sync",
                "new_team_id": "team-new",
                "manager_user_ids": ["user-a"],
                "idempotency_key": "sync-uuid",
            }),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn sync_body_rejects_missing_null_wrong_typed_and_unknown_fields() {
    let (app, _service) = team_router();
    let bad_bodies = [
        // missing manager_user_ids entirely
        json!({ "operation": "sync", "idempotency_key": "k" }),
        // null snapshot
        json!({ "operation": "sync", "manager_user_ids": Value::Null, "idempotency_key": "k" }),
        // wrong element type
        json!({ "operation": "sync", "manager_user_ids": "team-a", "idempotency_key": "k" }),
        // mixed types
        json!({ "operation": "sync", "manager_user_ids": ["user-a", 3], "idempotency_key": "k" }),
        // empty-string member (items minLength 1)
        json!({ "operation": "sync", "manager_user_ids": ["user-a", ""], "idempotency_key": "k" }),
        // unknown field
        json!({ "operation": "sync", "manager_user_ids": [], "idempotency_key": "k", "membership_version": 3 }),
        // unknown operation
        json!({ "operation": "reconcile", "manager_user_ids": [], "idempotency_key": "k" }),
        // missing idempotency_key
        json!({ "operation": "sync", "manager_user_ids": [] }),
        // blank idempotency_key
        json!({ "operation": "sync", "manager_user_ids": [], "idempotency_key": "   " }),
    ];
    for body in bad_bodies {
        let response = app
            .clone()
            .oneshot(bearer_request("PUT", TEAM_PATH, body, Some("credential-good")))
            .await
            .expect("router call");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "body was accepted");
    }
}

#[tokio::test]
async fn normal_internal_route_without_principal_is_unauthorized() {
    // The Principal exemption is ONLY for the credential-bound team slice:
    // the ordinary internal collaboration routes keep requiring a Human
    // Principal.
    let (app, _service) = team_router();
    let response = app
        .oneshot(Request::builder().method("GET").uri("/api/v1/collaboration/templates").body(Body::empty()).expect("request"))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn repair_add_member_uses_service_credential_and_idempotency() {
    let (app, service) = team_router();

    // Anonymous repair is rejected — no request without the trusted
    // service credential may change permission state.
    let response = app
        .clone()
        .oneshot(bearer_request(
            "POST",
            MEMBERS_PATH,
            json!({ "user_id": "user-b", "idempotency_key": "repair-1" }),
            None,
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(service.repairs.lock().expect("repairs lock").is_empty());

    let response = app
        .clone()
        .oneshot(bearer_request(
            "POST",
            MEMBERS_PATH,
            json!({ "user_id": "user-b", "idempotency_key": "repair-1" }),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["operation_id"], "repair-1");

    let repairs = service.repairs.lock().expect("repairs lock");
    assert_eq!(repairs.len(), 1);
    assert_eq!(repairs[0].service, verified_service("team-sync-1", None, None, None));
    assert_eq!(repairs[0].user_id, "user-b");
    assert_eq!(repairs[0].idempotency_key, "repair-1");
    assert_eq!(repairs[0].bot_id, "bot-a");
    assert_eq!(repairs[0].team_id, "team-a");

    // Body shape: only user_id + idempotency_key, additionalProperties
    // false, both required.
    let response = app
        .clone()
        .oneshot(bearer_request(
            "POST",
            MEMBERS_PATH,
            json!({ "user_id": "user-b" }),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let response = app
        .clone()
        .oneshot(bearer_request(
            "POST",
            MEMBERS_PATH,
            json!({ "user_id": "user-b", "idempotency_key": "repair-1", "team": "team-a" }),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    // A repair outside the credential's scopes is forbidden.
    let response = app
        .clone()
        .oneshot(bearer_request(
            "POST",
            "/api/v1/bots/bot-b/manager-sources/teams/team-b/members",
            json!({ "user_id": "user-b", "idempotency_key": "repair-1" }),
            Some("credential-restricted"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn repair_remove_member_uses_query_id_and_idempotency_header() {
    let (app, service) = team_router();

    let response = app
        .clone()
        .oneshot(bearer_request("DELETE", MEMBERS_PATH, json!({}), None))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(service.repairs.lock().expect("repairs lock").is_empty());

    let request =
        bearer_request("DELETE", &format!("{MEMBERS_PATH}?user_id=user-b"), json!({}), Some("credential-good"))
            .with_idempotency_key("repair-2");
    let response = app
        .clone()
        .oneshot(request)
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["operation_id"], "repair-1");

    let repairs = service.repairs.lock().expect("repairs lock");
    assert_eq!(repairs.len(), 1);
    assert_eq!(repairs[0].user_id, "user-b");
    assert_eq!(repairs[0].idempotency_key, "repair-2");

    // Missing idempotency header or missing user_id -> 400.
    let response = app
        .clone()
        .oneshot(bearer_request(
            "DELETE",
            &format!("{MEMBERS_PATH}?user_id=user-b"),
            json!({}),
            Some("credential-good"),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    let request = bearer_request("DELETE", MEMBERS_PATH, json!({}), Some("credential-good"))
        .with_idempotency_key("repair-2");
    let response = app.oneshot(request).await.expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

trait WithIdempotencyKey {
    fn with_idempotency_key(self, key: &str) -> Request<Body>;
}

impl WithIdempotencyKey for Request<Body> {
    fn with_idempotency_key(mut self, key: &str) -> Request<Body> {
        if self.headers().get("idempotency-key").is_none() {
            self.headers_mut()
                .insert("idempotency-key", key.parse().expect("header value"));
        }
        self
    }
}

#[tokio::test]
async fn unconfigured_team_slice_is_not_mounted() {
    // Without the team-manager sync service in state, the team write routes
    // are NOT mounted — unconfigured credential boundaries never expose
    // an anonymous entry point (404, not a callable route).
    let app = router_with_state(state_caller(caller()));
    for (method, uri) in [
        ("PUT", TEAM_PATH),
        ("POST", MEMBERS_PATH),
        ("DELETE", MEMBERS_PATH),
    ] {
        let response = app
            .clone()
            .oneshot(bearer_request(method, uri, json!({}), Some("credential-good")))
            .await
            .expect("router call");
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{method}");
    }
}

#[tokio::test]
async fn credential_is_verified_before_any_body_is_parsed_or_state_changes() {
    let (app, service) = team_router();
    // Anonymous callers cannot probe which teams exist: no credential
    // -> 401 with any team id, and nothing reaches the application.
    let response = app
        .clone()
        .oneshot(bearer_request(
            "PUT",
            "/api/v1/bots/bot-ghost/manager-sources/teams/team-ghost",
            json!({"operation": "sync", "manager_user_ids": [], "idempotency_key": "k"}),
            None,
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(
        service
            .verifications
            .lock()
            .expect("verifications lock")
            .is_empty()
    );
}
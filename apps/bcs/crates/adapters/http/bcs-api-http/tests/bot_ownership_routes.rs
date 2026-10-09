//! Task 14 RED/GREEN suite: the Human-only ownership view/transfer API
//! (`/openapi/v1/collaboration/bots/{bot_id}/ownership`, the idempotent
//! `.../ownership-transfers` create, `/ownership-transfers*` reads and
//! the bodyless accept/reject/cancel decisions, spec §11.1) — real
//! Router + middleware assembly with a scripted application double, so
//! every RED status asserts the ROUTE layer's transport contract
//! while the behavioral RED list (concealment, persisted invalidation,
//! 不落单, committed-outcome codes) runs against the REAL facade +
//! memory twin in the bcs-app-bot lane suite.
#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

mod manager_support;

use std::collections::{BTreeSet, HashMap};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs_domain::TransferStatus;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, BotOwnership, BotOwnershipTransferCreation,
    BotOwnershipTransferPage, BotOwnershipTransferReceipt, CreateBotOwnershipTransfer,
    DecideBotOwnershipTransfer, ERROR_INVALID_TRANSFER_RECIPIENT, GetBotOwnership,
    GetBotOwnershipTransfer, ListBotOwnershipTransfers, OwnershipTransferService,
    TransferListDirection,
};
use serde_json::{json, Value};
use tower::ServiceExt;

use manager_support::{bearer_request, caller, response_json, router_with_state, state_caller};

/// Scripted application double: records every call and its VERIFIED
/// caller; answered from swappable scripts so each RED status asserts
/// exactly what the ROUTES must project.
#[derive(Default)]
struct ScriptedOwnershipService {
    ownership_queries: Mutex<Vec<GetBotOwnership>>,
    creates: Mutex<Vec<CreateBotOwnershipTransfer>>,
    lists: Mutex<Vec<ListBotOwnershipTransfers>>,
    gets: Mutex<Vec<GetBotOwnershipTransfer>>,
    decides: Mutex<Vec<(&'static str, String, String)>>,
    /// Receipts remembered per create key: same-key replays answer with
    /// the ORIGINAL historical receipt and `created = false`.
    receipts_by_key: Mutex<HashMap<String, BotOwnershipTransferReceipt>>,
    next_create_error: Mutex<Option<ApplicationError>>,
    next_decide_error: Mutex<Option<ApplicationError>>,
    next_get_error: Mutex<Option<ApplicationError>>,
}

impl ScriptedOwnershipService {
    fn script_create_error(&self, error: ApplicationError) {
        *self.next_create_error.lock().expect("create error lock") = Some(error);
    }

    fn script_decide_error(&self, error: ApplicationError) {
        *self.next_decide_error.lock().expect("decide error lock") = Some(error);
    }

    fn script_get_error(&self, error: ApplicationError) {
        *self.next_get_error.lock().expect("get error lock") = Some(error);
    }
}

fn receipt(id: &str, status: TransferStatus) -> BotOwnershipTransferReceipt {
    BotOwnershipTransferReceipt {
        transfer_id: id.to_string(),
        bot_id: "bot-x".to_string(),
        from_user_id: "staff-1".to_string(),
        to_user_id: "staff-2".to_string(),
        status,
        expected_owner_version: 1,
        result_owner_version: None,
        expires_at: 1_800_000_000_000,
        decided_by: None,
        decided_at: None,
        terminal_reason: None,
        bot_name_snapshot: "Bot X".to_string(),
        gmt_create: 1,
        gmt_modified: 2,
    }
}

#[async_trait]
impl OwnershipTransferService for ScriptedOwnershipService {
    async fn get_ownership(
        &self,
        query: GetBotOwnership,
    ) -> Result<BotOwnership, ApplicationError> {
        self.ownership_queries
            .lock()
            .expect("ownership lock")
            .push(GetBotOwnership {
                caller: query.caller,
                bot_id: query.bot_id,
            });
        Ok(BotOwnership {
            bot_id: "bot-x".to_string(),
            owner_user_id: "staff-1".to_string(),
            ownership_version: 3,
        })
    }

    async fn create_transfer(
        &self,
        command: CreateBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferCreation, ApplicationError> {
        if let Some(error) = self
            .next_create_error
            .lock()
            .expect("create error lock")
            .take()
        {
            return Err(error);
        }
        self.creates
            .lock()
            .expect("creates lock")
            .push(CreateBotOwnershipTransfer {
                caller: command.caller,
                bot_id: command.bot_id,
                to_user_id: command.to_user_id,
                expected_owner_version: command.expected_owner_version,
                client_request_id: command.client_request_id.clone(),
            });
        let mut receipts = self.receipts_by_key.lock().expect("receipts lock");
        let created = !receipts.contains_key(&command.client_request_id);
        let receipt = receipts
            .entry(command.client_request_id)
            .or_insert_with(|| receipt("transfer-create", TransferStatus::Pending))
            .clone();
        Ok(BotOwnershipTransferCreation { receipt, created })
    }

    async fn list_transfers(
        &self,
        query: ListBotOwnershipTransfers,
    ) -> Result<BotOwnershipTransferPage, ApplicationError> {
        self.lists
            .lock()
            .expect("lists lock")
            .push(ListBotOwnershipTransfers {
                caller: query.caller,
                direction: query.direction,
                status: query.status,
                offset: query.offset,
                limit: query.limit,
            });
        Ok(BotOwnershipTransferPage {
            items: vec![receipt("transfer-list", TransferStatus::Pending)],
            total: 1,
            offset: query.offset,
            limit: query.limit,
        })
    }

    async fn get_transfer(
        &self,
        query: GetBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        if let Some(error) = self.next_get_error.lock().expect("get error lock").take() {
            return Err(error);
        }
        self.gets
            .lock()
            .expect("gets lock")
            .push(GetBotOwnershipTransfer {
                caller: query.caller,
                transfer_id: query.transfer_id,
            });
        Ok(receipt("transfer-create", TransferStatus::Pending))
    }

    async fn accept_transfer(
        &self,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        self.decide("accept", command).await
    }

    async fn reject_transfer(
        &self,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        self.decide("reject", command).await
    }

    async fn cancel_transfer(
        &self,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        self.decide("cancel", command).await
    }
}

impl ScriptedOwnershipService {
    async fn decide(
        &self,
        action: &'static str,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        if let Some(error) = self
            .next_decide_error
            .lock()
            .expect("decide error lock")
            .take()
        {
            return Err(error);
        }
        let caller_id = command
            .caller
            .user
            .as_ref()
            .expect("verified user")
            .id
            .clone();
        self.decides
            .lock()
            .expect("decides lock")
            .push((action, caller_id, command.transfer_id));
        let mut r = receipt("transfer-create", TransferStatus::Accepted);
        r.result_owner_version = Some(2);
        Ok(r)
    }
}

fn ownership_router(service: Arc<ScriptedOwnershipService>) -> axum::Router {
    let state = state_caller(caller()).with_ownership_transfer_service(service);
    router_with_state(state)
}

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

fn human_request(method: &str, uri: &str, body: Value) -> Request<Body> {
    bearer_request(method, uri, body, None).with_x_test_auth()
}

#[tokio::test]
async fn ownership_route_requires_the_gateway_principal() {
    let app = ownership_router(Arc::new(ScriptedOwnershipService::default()));
    let response = app
        .clone()
        .oneshot(bearer_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-x/ownership",
            json!({}),
            None,
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn create_is_201_the_first_time_and_200_for_the_same_key_replay() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());
    let body = json!({
        "to_user_id": "staff-2",
        "expected_owner_version": 1,
        "client_request_id": "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1",
    });
    let response = app
        .clone()
        .oneshot(human_request(
            "POST",
            "/openapi/v1/collaboration/bots/bot-x/ownership-transfers",
            body.clone(),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::CREATED);
    let first = response_json(response).await;
    assert_eq!(first["code"], 20_000);
    assert_eq!(first["data"]["transfer_id"], "transfer-create");
    assert_eq!(first["data"]["status"], "pending");

    // The SAME key replays the ORIGINAL receipt: HTTP 200, never a new
    // transfer id, never a re-execution.
    let response = app
        .clone()
        .oneshot(human_request(
            "POST",
            "/openapi/v1/collaboration/bots/bot-x/ownership-transfers",
            body,
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let replay = response_json(response).await;
    assert_eq!(replay["data"]["transfer_id"], "transfer-create");

    // The VERIFIED Human (staff-1), never a body value, is the actor.
    let creates = service.creates.lock().expect("creates lock");
    assert_eq!(creates.len(), 2);
    assert_eq!(
        creates[0].caller.user.as_ref().expect("verified user").id,
        "staff-1"
    );
    assert_eq!(creates[0].to_user_id, "staff-2");
    assert_eq!(creates[0].expected_owner_version, 1);
    assert_eq!(
        creates[0].client_request_id,
        "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1"
    );
}

#[tokio::test]
async fn ownership_query_projects_the_owner_and_version() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());
    let response = app
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-x/ownership",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["bot_id"], "bot-x");
    assert_eq!(body["data"]["owner_user_id"], "staff-1");
    assert_eq!(body["data"]["ownership_version"], 3);
    assert_eq!(body["data"].as_object().expect("object").len(), 3);
    let queries = service.ownership_queries.lock().expect("queries lock");
    assert_eq!(queries.len(), 1);
    assert_eq!(queries[0].bot_id, "bot-x");
    assert_eq!(
        queries[0].caller.user.as_ref().expect("verified user").id,
        "staff-1"
    );
}

#[tokio::test]
async fn lane_errors_project_their_fixed_envelope_codes() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());
    let create_uri = "/openapi/v1/collaboration/bots/bot-x/ownership-transfers";
    let valid_create = json!({
        "to_user_id": "staff-2",
        "expected_owner_version": 1,
        "client_request_id": "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1",
    });

    // The manager that cannot initiate: 403 forbidden.
    service.script_create_error(ApplicationError::forbidden(
        "user 'staff-2' holds no owner role",
    ));
    let response = app
        .clone()
        .oneshot(human_request("POST", create_uri, valid_create.clone()))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "forbidden");

    // An unresolvable recipient NEVER surfaces as a 500: the authorized
    // initiator sees 400 invalid_transfer_recipient.
    service.script_create_error(ApplicationError::invalid(
        ERROR_INVALID_TRANSFER_RECIPIENT,
        "recipient is not a live Human",
    ));
    let response = app
        .clone()
        .oneshot(human_request("POST", create_uri, valid_create.clone()))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_transfer_recipient");

    // Uninitialized ownership shares the manager/ownership 409 code
    // (never an auto-claim).
    service.script_create_error(ApplicationError::conflict(
        "ownership_not_initialized",
        "bot ownership is not initialized",
    ));
    let response = app
        .clone()
        .oneshot(human_request("POST", create_uri, valid_create.clone()))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "ownership_not_initialized");

    // The create-lane typed conflict family keeps each fixed code.
    let conflict_cases: [(&str, &str); 3] = [
        ("ownership_transfer_pending", "still pending"),
        ("ownership_changed", "the version snapshot moved"),
        ("idempotency_conflict", "same key different payload"),
    ];
    for (code, message) in conflict_cases {
        service.script_create_error(ApplicationError::conflict(code, message));
        let response = app
            .clone()
            .oneshot(human_request("POST", create_uri, valid_create.clone()))
            .await
            .expect("router call");
        assert_eq!(response.status(), StatusCode::CONFLICT);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], code);
    }
}

#[tokio::test]
async fn get_concealment_uses_the_404_envelope_code() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());
    // The RED snapshot: unrelated readers get 404 and cannot learn
    // whether the transfer id exists (anti-enumeration).
    service.script_get_error(ApplicationError::not_found(
        "ownership_transfer_not_found",
        "ownership transfer not found or not visible",
    ));
    let response = app
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/ownership-transfers/transfer-404",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "ownership_transfer_not_found");
}

#[tokio::test]
async fn receipt_projection_is_exact_and_leaks_nothing_private() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());
    let response = app
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/ownership-transfers/transfer-create",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let data = body["data"].as_object().expect("receipt object");
    // The §11.1 confirmation-scope keys ONLY: no current_owner, no env,
    // no client_request_id, no summary/prompt/config/credentials.
    let expected: BTreeSet<String> = [
        "transfer_id",
        "bot_id",
        "from_user_id",
        "to_user_id",
        "status",
        "expected_owner_version",
        "expires_at",
        "bot_name_snapshot",
        "gmt_create",
        "gmt_modified",
    ]
    .iter()
    .map(|key| (*key).to_string())
    .collect();
    let actual: BTreeSet<String> = data.keys().cloned().collect();
    assert_eq!(actual, expected, "the pending receipt must not leak private fields");
}

#[tokio::test]
async fn decided_transfer_receipts_project_the_committed_outcome() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());
    let response = app
        .clone()
        .oneshot(human_request(
            "POST",
            "/openapi/v1/collaboration/ownership-transfers/transfer-create/accept",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "accepted");
    assert_eq!(body["data"]["result_owner_version"], 2);

    // The committed-outcome 409 family keeps each fixed code through
    // the shared error envelope — no rollback was ever implied.
    let decide_conflicts = [
        (
            ApplicationError::ownership_changed("invalidated by owner change"),
            "ownership_changed",
        ),
        (
            ApplicationError::ownership_transfer_expired("window lapsed"),
            "ownership_transfer_expired",
        ),
        (
            ApplicationError::ownership_transfer_not_pending("terminal row"),
            "ownership_transfer_not_pending",
        ),
    ];
    for (error, code) in decide_conflicts {
        service.script_decide_error(error);
        let response = app
            .clone()
            .oneshot(human_request(
                "POST",
                "/openapi/v1/collaboration/ownership-transfers/transfer-create/accept",
                json!({}),
            ))
            .await
            .expect("router call");
        assert_eq!(response.status(), StatusCode::CONFLICT);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], code);
    }
}

#[tokio::test]
async fn corrupt_authority_is_a_sanitized_500() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());
    service.script_get_error(ApplicationError::internal(
        "CorruptAuthority: SELECT failed near `bcs_bots.ownership_version`",
    ));
    let response = app
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/ownership-transfers/transfer-create",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "internal_error");
    let message = body["message"].as_str().expect("message");
    assert!(!message.contains("SELECT"), "no SQL leakage: {message}");
    assert_eq!(message, "Internal server error");
}

#[tokio::test]
async fn create_body_shape_is_validated_before_the_application() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());
    let uri = "/openapi/v1/collaboration/bots/bot-x/ownership-transfers";
    let cases = [
        // Unknown fields are rejected.
        json!({"to_user_id": "staff-2", "expected_owner_version": 1,
               "client_request_id": "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1",
               "team": "smuggled"}),
        // The idempotency key must be a UUID.
        json!({"to_user_id": "staff-2", "expected_owner_version": 1,
               "client_request_id": "not-a-uuid"}),
        // The version snapshot must be positive.
        json!({"to_user_id": "staff-2", "expected_owner_version": 0,
               "client_request_id": "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1"}),
        // Blank recipient.
        json!({"to_user_id": " ", "expected_owner_version": 1,
               "client_request_id": "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1"}),
        // Missing field.
        json!({"to_user_id": "staff-2", "expected_owner_version": 1}),
    ];
    for body in cases {
        let response = app
            .clone()
            .oneshot(human_request("POST", uri, body))
            .await
            .expect("router call");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "invalid_request");
    }
    assert!(service.creates.lock().expect("creates lock").is_empty());
}

#[tokio::test]
async fn decision_actions_carry_no_business_body() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());
    // Any body content — `{}` excluded — is 400 before the application.
    let response = app
        .clone()
        .oneshot(human_request(
            "POST",
            "/openapi/v1/collaboration/ownership-transfers/transfer-create/reject",
            json!({"foo": 1}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(service.decides.lock().expect("decides lock").is_empty());

    // Absent/empty bodies are the contract; every action forwards the
    // VERIFIED user and the exact transfer id.
    for (suffix, action) in [
        ("accept", "accept"),
        ("reject", "reject"),
        ("cancel", "cancel"),
    ] {
        let response = app
            .clone()
            .oneshot(human_request(
                "POST",
                &format!(
                    "/openapi/v1/collaboration/ownership-transfers/transfer-create/{suffix}"
                ),
                json!({}),
            ))
            .await
            .expect("router call");
        assert_eq!(response.status(), StatusCode::OK);
        let decisions = service.decides.lock().expect("decides lock");
        assert_eq!(decisions.last().expect("recorded").0, action);
        assert_eq!(decisions.last().expect("recorded").1, "staff-1");
        assert_eq!(decisions.last().expect("recorded").2, "transfer-create");
    }
}

#[tokio::test]
async fn list_forwards_direction_defaults_and_rejects_bad_windows() {
    let service = Arc::new(ScriptedOwnershipService::default());
    let app = ownership_router(service.clone());

    // Defaults: direction received, offset 0, limit 20, no status filter.
    let response = app
        .clone()
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/ownership-transfers",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 1);
    assert_eq!(body["data"]["offset"], 0);
    assert_eq!(body["data"]["limit"], 20);
    let lists = service.lists.lock().expect("lists lock");
    assert_eq!(lists.len(), 1);
    assert_eq!(lists[0].direction, TransferListDirection::Received);
    assert_eq!(lists[0].status, None);
    drop(lists);

    // Explicit direction + status + window forward verbatim.
    let response = app
        .clone()
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/ownership-transfers?direction=sent&status=pending&offset=4&limit=100",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::OK);
    let lists = service.lists.lock().expect("lists lock");
    assert_eq!(lists.len(), 2);
    assert_eq!(lists[1].direction, TransferListDirection::Sent);
    assert_eq!(lists[1].status, Some(TransferStatus::Pending));
    assert_eq!(lists[1].offset, 4);
    assert_eq!(lists[1].limit, 100);
    drop(lists);

    // Out-of-range windows and unknown filters never query.
    for uri in [
        "/openapi/v1/collaboration/ownership-transfers?limit=0",
        "/openapi/v1/collaboration/ownership-transfers?limit=101",
        "/openapi/v1/collaboration/ownership-transfers?bogus=1",
        "/openapi/v1/collaboration/ownership-transfers?status=whatever",
    ] {
        let response = app
            .clone()
            .oneshot(human_request("GET", uri, json!({})))
            .await
            .expect("router call");
        assert_eq!(
            response.status(),
            StatusCode::BAD_REQUEST,
            "{uri} must be a transport 400"
        );
    }
    assert_eq!(service.lists.lock().expect("lists lock").len(), 2);
}

#[tokio::test]
async fn unconfigured_owner_transfer_service_fails_closed() {
    let app = router_with_state(state_caller(caller()));
    let response = app
        .oneshot(human_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-x/ownership",
            json!({}),
        ))
        .await
        .expect("router call");
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
}
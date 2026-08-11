use std::sync::Arc;

use async_trait::async_trait;
use axum::http::StatusCode;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::application::v1::{
    AuthorizeGroupSessionConnection, AuthorizedGroupSessionConnection,
    GroupSessionConnectionBinding, GroupSessionConnectionError, GroupSessionConnectionService,
    IssueGroupSessionConnectionToken, IssuedGroupSessionConnectionToken,
    VerifyGroupSessionConnectionToken,
};
use bcs_services_container::Services;
use bcs_test_support::{NoopCollaborationRuntimeService, NoopWsLifecycleInstrumentationHook};
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{WebDispatchState, WorkbenchConnectionRegistry, group_session_websocket_router};
use futures::{SinkExt, StreamExt};
use serde_json::json;
use tokio::sync::Mutex;
use tokio_tungstenite::tungstenite::Message;

const PATH: &str = "/openapi/v1/collaboration/messages/ws";

#[derive(Clone, Copy)]
enum VerifyMode {
    Valid,
    Invalid,
    Unavailable,
}

struct RecordingConnectionService {
    mode: VerifyMode,
    verified_tokens: Mutex<Vec<String>>,
    authorizations: Mutex<Vec<AuthorizeGroupSessionConnection>>,
}

impl RecordingConnectionService {
    fn new(mode: VerifyMode) -> Self {
        Self {
            mode,
            verified_tokens: Mutex::new(Vec::new()),
            authorizations: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl GroupSessionConnectionService for RecordingConnectionService {
    async fn issue_token(
        &self,
        _command: IssueGroupSessionConnectionToken,
    ) -> Result<IssuedGroupSessionConnectionToken, GroupSessionConnectionError> {
        panic!("WebSocket Upgrade does not issue connection tokens")
    }

    async fn verify_token(
        &self,
        command: VerifyGroupSessionConnectionToken,
    ) -> Result<GroupSessionConnectionBinding, GroupSessionConnectionError> {
        self.verified_tokens.lock().await.push(command.token);
        match self.mode {
            VerifyMode::Valid => Ok(GroupSessionConnectionBinding {
                tenant: Some("tenant-a".to_string()),
                user_id: "user-a".to_string(),
                group_id: "group-a".to_string(),
                session_id: "session-a".to_string(),
            }),
            VerifyMode::Invalid => Err(GroupSessionConnectionError::InvalidConnectionToken),
            VerifyMode::Unavailable => Err(GroupSessionConnectionError::TokenServiceUnavailable),
        }
    }

    async fn authorize_connect(
        &self,
        command: AuthorizeGroupSessionConnection,
    ) -> Result<AuthorizedGroupSessionConnection, GroupSessionConnectionError> {
        self.authorizations.lock().await.push(command);
        Ok(AuthorizedGroupSessionConnection {
            participants: Vec::new(),
        })
    }
}

fn app(service: Arc<RecordingConnectionService>) -> axum::Router {
    let services = Services::noop();
    let dispatch_state = Arc::new(WebDispatchState {
        message_flow: services.message_flow,
        collaboration_runtime: Arc::new(NoopCollaborationRuntimeService),
        workbench_sessions: services.workbench_sessions,
        group_session_connections: Some(service.clone()),
        frontend_connections: Arc::new(WorkbenchConnectionRegistry::new()),
        run_channels: Arc::new(RunChannelManager::new()),
    });
    group_session_websocket_router(
        service,
        dispatch_state,
        Arc::new(NoopWsLifecycleInstrumentationHook),
    )
}

async fn start(
    service: Arc<RecordingConnectionService>,
) -> (std::net::SocketAddr, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind test server");
    let addr = listener.local_addr().expect("test server address");
    let handle = tokio::spawn(async move {
        axum::serve(listener, app(service))
            .await
            .expect("serve test app");
    });
    (addr, handle)
}

async fn rejected_upgrade(url: &str) -> (StatusCode, serde_json::Value) {
    let error = tokio_tungstenite::connect_async(url)
        .await
        .expect_err("Upgrade must be rejected");
    let tokio_tungstenite::tungstenite::Error::Http(response) = error else {
        panic!("expected HTTP rejection, got {error}")
    };
    let status = response.status();
    let body = response.body().as_deref().expect("JSON rejection body");
    let body = serde_json::from_slice(body).expect("JSON rejection");
    (status, body)
}

#[tokio::test]
async fn missing_and_invalid_connection_tokens_return_unauthorized_before_upgrade() {
    let service = Arc::new(RecordingConnectionService::new(VerifyMode::Invalid));
    let (addr, handle) = start(service.clone()).await;

    let (status, body) = rejected_upgrade(&format!("ws://{addr}{PATH}")).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["data"]["error_code"], "invalid_connection_token");
    assert!(service.verified_tokens.lock().await.is_empty());

    for case in ["malformed", "forged", "expired", "wrong-purpose"] {
        let token = format!("sensitive-{case}-credential-123");
        let (status, body) = rejected_upgrade(&format!("ws://{addr}{PATH}?token={token}")).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
        assert_eq!(body["data"]["error_code"], "invalid_connection_token");
        assert!(!body.to_string().contains(&token));
    }

    assert_eq!(service.verified_tokens.lock().await.len(), 4);
    handle.abort();
}

#[tokio::test]
async fn unavailable_token_service_returns_service_unavailable_without_upgrading() {
    let service = Arc::new(RecordingConnectionService::new(VerifyMode::Unavailable));
    let (addr, handle) = start(service).await;

    let (status, body) = rejected_upgrade(&format!("ws://{addr}{PATH}?token=opaque")).await;

    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body["data"]["error_code"], "token_service_unavailable");
    handle.abort();
}

#[tokio::test]
async fn valid_token_upgrades_and_connect_uses_the_immutable_verified_binding() {
    let service = Arc::new(RecordingConnectionService::new(VerifyMode::Valid));
    let (addr, handle) = start(service.clone()).await;
    let url = format!("ws://{addr}{PATH}?token=opaque-valid-token");

    let (mut socket, response) = tokio_tungstenite::connect_async(url)
        .await
        .expect("valid token upgrades");
    assert_eq!(response.status(), StatusCode::SWITCHING_PROTOCOLS);
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-1",
        "connect",
        Some(json!({
            "group_id": "group-a",
            "session_id": "session-a"
        })),
    ));
    socket
        .send(Message::Text(
            serde_json::to_string(&connect)
                .expect("serialize connect")
                .into(),
        ))
        .await
        .expect("send connect");
    let response = socket
        .next()
        .await
        .expect("connect response frame")
        .expect("valid WebSocket frame");
    let Message::Text(response) = response else {
        panic!("expected text response")
    };
    let response: BcsFrame = serde_json::from_str(&response).expect("BCS response frame");
    assert!(matches!(response, BcsFrame::Response(response) if response.ok));

    assert_eq!(
        service.verified_tokens.lock().await.as_slice(),
        ["opaque-valid-token"]
    );
    let authorizations = service.authorizations.lock().await;
    assert_eq!(authorizations.len(), 1);
    assert_eq!(
        authorizations[0].binding,
        GroupSessionConnectionBinding {
            tenant: Some("tenant-a".to_string()),
            user_id: "user-a".to_string(),
            group_id: "group-a".to_string(),
            session_id: "session-a".to_string(),
        }
    );
    drop(authorizations);

    socket.close(None).await.expect("close WebSocket");
    handle.abort();
}

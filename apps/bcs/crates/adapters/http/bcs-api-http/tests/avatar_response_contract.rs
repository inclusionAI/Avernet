//! HTTP serialization regression for the bot-avatar plan (Task 8).
//!
//! Design §5.1 (docs/specs/2026-11-02-bot-avatar-collaboration-repair/design.md):
//! only the V1 OpenAPI surfaces gain `avatar_url` on participant rows. The
//! Workbench WebSocket connect rows and the stored `RegisteredBot` wire
//! record keep their pre-avatar shapes, so the same stored Bot can never
//! extend an old interface just because a cross-version shared V1
//! serialization type gained a field. The tests fetch live JSON from the
//! real V1 router — no direct DTO assertions on the route-facing surfaces.
//! The LEGACY http half (bcs-http list/detail/create/PATCH rows) lives in
//! the twin file `bcs-http/tests/avatar_response_contract.rs`, on its own
//! side of the manifest boundary contract.
//!
//! The WS connect rows and the persisted Bot record are additionally pinned
//! on the wire-DTO level here: `WorkbenchParticipantView` is the only shape
//! the session-bound connect frame serializes, and a bot whose avatar was
//! persisted through the store contract must still serialize the historical
//! `RegisteredBot` field set.

use std::path::PathBuf;
use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::Request,
};
use bcs_api_http::{
    ApiState, CredentialKind, PrincipalVerificationError, PrincipalVerifier,
    VerifiedRequestIdentity, router,
};
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, AddGroupParticipant, AddSessionParticipant,
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, BotFinalDelivery,
    ChatConfiguration, CollaborationConfiguration, CollaborationGroupDetail, CollectSession,
    CompleteSession, CreateBotFriendRequest, CreateGroup, CreateGroupInvitation, CreateSession,
    CreateSessionInvitation, CreateSessionOutcome, DeleteBotFriendship, DeleteGroup,
    DeleteGroupParticipant, DeleteResult, DeleteSession, DeleteSessionParticipant,
    DirectMessageGroupSummary, FriendRequest, Friendship, FriendshipService, GetGroup, GetSession,
    GroupDeliveryPolicy, GroupDetail, GroupService, GroupStatus, GroupSummary, GroupVisibility,
    Invitation, InvitationAcceptResult, InvitationService, IssueRegisterToken,
    ListBotFriendRequests, ListBotFriendships, ListGroups, ListPublicGroups, ListSessionMessages,
    ListSessions, Membership, MessageViewScope, NormalGroupSummary, Page, Participant,
    ParticipantSummary, RegisterBot, RegisterService, RegisterTokenView, RejectFriendRequest,
    SessionCollectionResult, SessionCompletionResult, SessionDetail, SessionMessageService,
    SessionParticipant, SessionService, SessionStatus, SessionSummary, UncollectSession,
    UpdateGroup, UpdateGroupParticipant, UpdateSession, UpdateSessionParticipant,
};
use bcs_service_api::types::bot_profile::AvatarChange;
use bcs_service_api::workbench_use_cases::WorkbenchParticipantView;
use bcs_service_api::{
    ActorKind, BotCapabilities, BotRepoPort, GroupMessage, HumanMentionNotifyMode,
    MessageHistoryOptions, ParticipantMode, ParticipantRole, RegisteredBot, SessionKind,
};
use serde_json::{Value, json};
use tempfile::TempDir;
use tower::ServiceExt;

const AVATAR: &str = "https://cdn.example.com/avatar/regression.png";

// ---------------------------------------------------------------------------
// Shared shape assertions. The plan's verbatim legacy-row helper lives in
// the bcs-http twin, where the legacy surfaces are actually asserted.
// ---------------------------------------------------------------------------

/// Every V1 participant row gains the `avatar_url` wire key, including the
/// rows whose avatar is null.
fn assert_v1_participants_have_avatar(value: &Value) {
    let participants = value["participants"]
        .as_array()
        .expect("V1 response carries a participants array");
    assert!(!participants.is_empty(), "fixture page has participants");
    for participant in participants {
        assert!(
            participant.get("avatar_url").is_some(),
            "V1 participant row must serialize avatar_url: {participant}"
        );
    }
}

/// No `avatar` / `avatar_url` key anywhere in a whole JSON tree (legacy
/// surfaces serialize explicit DTOs, so the field must not appear under any
/// nesting level).
fn assert_no_avatar_keys(value: &Value) {
    fn walk(value: &Value) {
        match value {
            Value::Object(map) => {
                for (key, child) in map {
                    assert!(
                        key != "avatar_url" && key != "avatar",
                        "legacy surface carries an avatar field '{key}'"
                    );
                    walk(child);
                }
            }
            Value::Array(items) => items.iter().for_each(walk),
            _ => {}
        }
    }
    walk(value);
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

// ---------------------------------------------------------------------------
// V1 fixtures (bcs-api-http router over application fakes).
// ---------------------------------------------------------------------------

struct HeaderVerifier {
    caller: AuthenticatedCaller,
}

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &axum::http::HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(VerifiedRequestIdentity::pin(
                self.caller.clone(),
                "test-double",
                CredentialKind::GatewayPrincipalHeader,
            ))
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

fn caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: "staff-1".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn v1_participant(actor_id: &str, avatar: Option<&str>) -> Participant {
    Participant {
        actor_id: actor_id.into(),
        actor_kind: ActorKind::Bot,
        name: Some(format!("{actor_id}-name")),
        avatar_url: avatar.map(str::to_string),
        role: ParticipantRole::Driver,
        mode: ParticipantMode::Auto,
        message_view_scope: MessageViewScope::Full,
        tags: Vec::new(),
    }
}

fn v1_summary_row(actor_id: &str, avatar: Option<&str>) -> ParticipantSummary {
    ParticipantSummary {
        actor_id: actor_id.into(),
        actor_kind: ActorKind::Bot,
        name: Some(format!("{actor_id}-snapshot")),
        avatar_url: avatar.map(str::to_string),
    }
}

fn group_detail() -> GroupDetail {
    GroupDetail::Collaboration(CollaborationGroupDetail {
        group_id: "group-1".into(),
        version: 1,
        name: Some("Avatar Regression".into()),
        status: GroupStatus::Active,
        visibility: GroupVisibility::Private,
        context: None,
        opening_message: None,
        originator_actor_id: "driver-bot".into(),
        participants: vec![
            v1_participant("driver-bot", Some(AVATAR)),
            v1_participant("plain-bot", None),
        ],
        driver_bot_uuid: "driver-bot".into(),
        driver_bot_owner: None,
        driver_bot_owner_name: None,
        collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
            delivery_policy: GroupDeliveryPolicy {
                bot_final_delivery: BotFinalDelivery::SendToDriver,
            },
        }),
        human_mention_notify_mode: HumanMentionNotifyMode::All,
        created_at: 1,
        updated_at: 2,
    })
}

fn normal_summary(group_id: &str) -> GroupSummary {
    GroupSummary::Normal(NormalGroupSummary {
        group_id: group_id.into(),
        version: 1,
        name: Some("Summary".into()),
        context: None,
        status: GroupStatus::Active,
        visibility: GroupVisibility::Private,
        membership: Membership::Direct,
        originator_actor_id: "driver-bot".into(),
        participant_count: 2,
        driver_bot_uuid: "driver-bot".into(),
        driver_bot_name: Some("Driver".into()),
        strategy: bcs_service_api::application::v1::GroupStrategy::Chat,
        participants: vec![
            v1_summary_row("driver-bot", Some(AVATAR)),
            v1_summary_row("plain-bot", None),
        ],
        human_mention_notify_mode: HumanMentionNotifyMode::All,
        created_at: 1,
        updated_at: 2,
    })
}

fn dm_summary() -> GroupSummary {
    GroupSummary::DirectMessage(DirectMessageGroupSummary {
        group_id: "dm-1".into(),
        version: 1,
        name: None,
        context: None,
        status: GroupStatus::Active,
        visibility: GroupVisibility::Private,
        membership: Membership::SessionOnly,
        originator_actor_id: "driver-bot".into(),
        participant_count: 2,
        peer_actor: Some(bcs_service_api::application::v1::Actor {
            actor_id: "plain-bot".into(),
            actor_kind: ActorKind::Bot,
            name: Some("Peer".into()),
            avatar_url: Some(AVATAR.into()),
        }),
        participants: vec![
            v1_summary_row("driver-bot", None),
            v1_summary_row("plain-bot", Some(AVATAR)),
        ],
        human_mention_notify_mode: HumanMentionNotifyMode::All,
        created_at: 1,
        updated_at: 2,
    })
}

/// V1 group application fake: every read/mutation returns participant rows
/// that already carry the stored avatar (as the real application would).
struct AvatarGroupService;

#[async_trait]
impl GroupService for AvatarGroupService {
    async fn list_groups(&self, command: ListGroups) -> Result<Page<GroupSummary>, ApplicationError> {
        Ok(Page {
            items: vec![normal_summary("group-1"), dm_summary()],
            total: 2,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn list_public_groups(
        &self,
        command: ListPublicGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        Ok(Page {
            items: vec![normal_summary("group-1")],
            total: 1,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn create(&self, _command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Ok(group_detail())
    }

    async fn get(&self, _query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        Ok(group_detail())
    }

    async fn update(&self, _command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        Ok(group_detail())
    }

    async fn delete(&self, _command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        Ok(DeleteResult { deleted: true })
    }

    async fn add_participant(&self, _command: AddGroupParticipant) -> Result<Participant, ApplicationError> {
        Ok(v1_participant("added-bot", Some(AVATAR)))
    }

    async fn update_participant(
        &self,
        _command: UpdateGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        Ok(v1_participant("plain-bot", Some(AVATAR)))
    }

    async fn delete_participant(
        &self,
        _command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Ok(DeleteResult { deleted: true })
    }
}

fn session_participant(actor_id: &str, avatar: Option<&str>) -> SessionParticipant {
    SessionParticipant {
        actor_id: actor_id.into(),
        actor_kind: ActorKind::Bot,
        name: Some(format!("{actor_id}-name")),
        avatar_url: avatar.map(str::to_string),
        role: ParticipantRole::Driver,
        tags: Vec::new(),
        mode: ParticipantMode::Auto,
        message_view_scope: MessageViewScope::Full,
        joined_at: Some(1),
    }
}

fn session_detail() -> SessionDetail {
    SessionDetail {
        session_id: "session-1".into(),
        version: 1,
        group_id: "group-1".into(),
        status: SessionStatus::Running,
        kind: SessionKind::Chat,
        title: Some("Avatar Regression".into()),
        input: None,
        meta: None,
        participants: vec![
            session_participant("driver-bot", Some(AVATAR)),
            session_participant("plain-bot", None),
        ],
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 2,
        state_machine_run_id: None,
        state_machine_run: None,
    }
}

fn session_summary() -> SessionSummary {
    SessionSummary {
        session_id: "session-1".into(),
        version: 1,
        group_id: "group-1".into(),
        status: SessionStatus::Running,
        title: Some("Avatar Regression".into()),
        participant_count: Some(2),
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 2,
        collected: None,
        participants: vec![
            v1_summary_row("driver-bot", Some(AVATAR)),
            v1_summary_row("plain-bot", None),
        ],
    }
}

/// V1 session application fake with the same avatar-bearing row semantics.
struct AvatarSessionService;

#[async_trait]
impl SessionService for AvatarSessionService {
    async fn create(&self, _command: CreateSession) -> Result<CreateSessionOutcome, ApplicationError> {
        Ok(CreateSessionOutcome {
            session: session_detail(),
            created: true,
        })
    }

    async fn list(&self, command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        Ok(Page {
            items: vec![session_summary()],
            total: 1,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn get(&self, _query: GetSession) -> Result<SessionDetail, ApplicationError> {
        Ok(session_detail())
    }

    async fn update(&self, _command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        Ok(session_detail())
    }

    async fn delete(&self, _command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        Ok(DeleteResult { deleted: true })
    }

    async fn complete(&self, _command: CompleteSession) -> Result<SessionCompletionResult, ApplicationError> {
        Ok(SessionCompletionResult {
            session_id: "session-1".into(),
            status: SessionStatus::Completed,
            completed_at: 3,
        })
    }

    async fn collect(&self, command: CollectSession) -> Result<SessionCollectionResult, ApplicationError> {
        Ok(SessionCollectionResult {
            session_id: command.session_id,
            participant: command.participant,
            collected: true,
        })
    }

    async fn uncollect(&self, command: UncollectSession) -> Result<SessionCollectionResult, ApplicationError> {
        Ok(SessionCollectionResult {
            session_id: command.session_id,
            participant: command.participant,
            collected: false,
        })
    }

    async fn add_participant(
        &self,
        _command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Ok(session_participant("added-bot", Some(AVATAR)))
    }

    async fn update_participant(
        &self,
        _command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Ok(session_participant("plain-bot", Some(AVATAR)))
    }

    async fn delete_participant(
        &self,
        _command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Ok(DeleteResult { deleted: true })
    }
}

struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(&self, _: CreateGroupInvitation) -> Result<Invitation, ApplicationError> {
        unreachable!("invitations are not exercised by this contract test")
    }

    async fn create_session_invitation(
        &self,
        _: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        unreachable!("invitations are not exercised by this contract test")
    }

    async fn accept_invitation(&self, _: AcceptInvitation) -> Result<InvitationAcceptResult, ApplicationError> {
        unreachable!("invitations are not exercised by this contract test")
    }
}

struct NoopFriendshipService;

#[async_trait]
impl FriendshipService for NoopFriendshipService {
    async fn list_bot_friendships(&self, _: ListBotFriendships) -> Result<Page<Friendship>, ApplicationError> {
        unreachable!("friendship is not exercised by this contract test")
    }

    async fn delete_bot_friendship(&self, _: DeleteBotFriendship) -> Result<DeleteResult, ApplicationError> {
        unreachable!("friendship is not exercised by this contract test")
    }

    async fn create_bot_friend_request(&self, _: CreateBotFriendRequest) -> Result<FriendRequest, ApplicationError> {
        unreachable!("friendship is not exercised by this contract test")
    }

    async fn list_bot_friend_requests(&self, _: ListBotFriendRequests) -> Result<Page<FriendRequest>, ApplicationError> {
        unreachable!("friendship is not exercised by this contract test")
    }

    async fn accept_friend_request(&self, _: AcceptFriendRequest) -> Result<FriendRequest, ApplicationError> {
        unreachable!("friendship is not exercised by this contract test")
    }

    async fn reject_friend_request(&self, _: RejectFriendRequest) -> Result<FriendRequest, ApplicationError> {
        unreachable!("friendship is not exercised by this contract test")
    }
}

struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(&self, _: IssueRegisterToken) -> Result<RegisterTokenView, ApplicationError> {
        unreachable!("register is not exercised by this contract test")
    }

    async fn register_bot(&self, _: RegisterBot) -> Result<bcs_service_api::application::v1::BotRegistration, ApplicationError> {
        unreachable!("register is not exercised by this contract test")
    }
}

struct NoopSessionMessageService;

#[async_trait]
impl SessionMessageService for NoopSessionMessageService {
    async fn list(&self, _: ListSessionMessages) -> Result<Vec<GroupMessage>, ApplicationError> {
        unreachable!("messages are not exercised by this contract test")
    }

    async fn list_with_options(
        &self,
        _: ListSessionMessages,
        _: MessageHistoryOptions,
    ) -> Result<Vec<GroupMessage>, ApplicationError> {
        unreachable!("messages are not exercised by this contract test")
    }
}

fn v1_router() -> axum::Router {
    router(ApiState::new(
        Arc::new(AvatarGroupService),
        Arc::new(AvatarSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier { caller: caller() }),
    ))
}

async fn v1_request(method: &str, uri: &str, body: Value) -> Value {
    v1_request_expect(method, uri, body, axum::http::StatusCode::OK)
        .await
        .1
}

async fn v1_request_expect(
    method: &str,
    uri: &str,
    body: Value,
    expected: axum::http::StatusCode,
) -> (axum::http::StatusCode, Value) {
    let response = v1_router()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
                .header("content-type", "application/json")
                .header("x-test-auth", "yes")
                .header("x-request-id", "request-avatar-contract")
                .body(Body::from(body.to_string()))
                .expect("request"),
        )
        .await
        .expect("response");
    let status = response.status();
    let json = response_json(response).await;
    assert_eq!(status, expected, "V1 response: {json}");
    (status, json)
}

fn v1_create_group_body() -> Value {
    json!({
        "group_kind": "normal",
        "name": "Avatar Regression",
        "driver_bot_uuid": "driver-bot",
        "participants": [
            { "actor_id": "plain-bot", "role": "consultant" }
        ],
        "collaboration": { "strategy": "chat" }
    })
}

// The legacy half of this regression — the bcs-http list/detail/create/PATCH
// surfaces — lives in `bcs-http/tests/avatar_response_contract.rs`: the
// versioned adapter's manifest boundary contract forbids depending on the
// legacy adapter, so the two halves of the plan's old/new fixture regression
// are twin files on their own sides of that boundary.

// ---------------------------------------------------------------------------
// V1 surface regressions.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn v1_group_list_and_public_list_serialize_participant_avatars() {
    let body = v1_request(
        "GET",
        "/openapi/v1/collaboration/groups?offset=0&limit=20",
        json!({}),
    )
    .await;
    for item in body["data"]["items"].as_array().expect("list items") {
        assert_v1_participants_have_avatar(item);
    }
    assert_eq!(
        body["data"]["items"][0]["participants"][0]["avatar_url"],
        json!(AVATAR)
    );
    // A DM row keeps peer_actor.avatar_url and its member rows visible.
    assert_eq!(
        body["data"]["items"][1]["peer_actor"]["avatar_url"],
        json!(AVATAR)
    );
    assert_eq!(
        body["data"]["items"][1]["participants"][1]["avatar_url"],
        json!(AVATAR)
    );

    let body = v1_request(
        "GET",
        "/openapi/v1/collaboration/public-groups?offset=0&limit=20",
        json!({}),
    )
    .await;
    for item in body["data"]["items"].as_array().expect("public items") {
        assert_v1_participants_have_avatar(item);
    }
}

#[tokio::test]
async fn v1_group_create_get_and_update_serialize_participant_avatars() {
    let (status, body) = v1_request_expect(
        "POST",
        "/openapi/v1/collaboration/groups",
        v1_create_group_body(),
        axum::http::StatusCode::CREATED,
    )
    .await;
    assert_eq!(status, axum::http::StatusCode::CREATED);
    // The create envelope flattens the GroupDetail into `data`.
    assert_v1_participants_have_avatar(&body["data"]);
    assert_eq!(body["data"]["participants"][0]["avatar_url"], json!(AVATAR));

    let body = v1_request("GET", "/openapi/v1/collaboration/groups/group-1", json!({}))
        .await;
    assert_v1_participants_have_avatar(&body["data"]);
    assert_eq!(body["data"]["participants"][0]["avatar_url"], json!(AVATAR));
    // The null-avatar row still serializes the explicit null wire field.
    assert_eq!(body["data"]["participants"][1]["avatar_url"], Value::Null);

    let body = v1_request(
        "PATCH",
        "/openapi/v1/collaboration/groups/group-1",
        json!({"name": "Renamed"}),
    )
    .await;
    assert_v1_participants_have_avatar(&body["data"]);
}

#[tokio::test]
async fn v1_group_participant_operations_serialize_participant_avatars() {
    let body = v1_request(
        "POST",
        "/openapi/v1/collaboration/groups/group-1/participants",
        json!({"actor_id": "plain-bot"}),
    )
    .await;
    assert_eq!(body["data"]["avatar_url"], json!(AVATAR));

    let body = v1_request(
        "PATCH",
        "/openapi/v1/collaboration/groups/group-1/participants/plain-bot",
        json!({"mode": "auto"}),
    )
    .await;
    assert_eq!(body["data"]["avatar_url"], json!(AVATAR));
}

#[tokio::test]
async fn v1_session_surfaces_serialize_participant_avatars() {
    // Group-scoped session list: lightweight rows.
    let body = v1_request(
        "GET",
        "/openapi/v1/collaboration/groups/group-1/sessions?offset=0&limit=20",
        json!({}),
    )
    .await;
    for item in body["data"]["items"].as_array().expect("session items") {
        assert_v1_participants_have_avatar(item);
    }
    assert_eq!(
        body["data"]["items"][0]["participants"][0]["avatar_url"],
        json!(AVATAR)
    );

    // Session create: the created detail keeps the full participant rows
    // (the envelope flattens the SessionDetail into `data`).
    let (status, body) = v1_request_expect(
        "POST",
        "/openapi/v1/collaboration/groups/group-1/sessions",
        json!({"kind": "chat"}),
        axum::http::StatusCode::CREATED,
    )
    .await;
    assert_eq!(status, axum::http::StatusCode::CREATED);
    assert_v1_participants_have_avatar(&body["data"]);

    // Detail and update reuse the same participant model.
    let body = v1_request("GET", "/openapi/v1/collaboration/sessions/session-1", json!({}))
        .await;
    assert_v1_participants_have_avatar(&body["data"]);
    assert_eq!(body["data"]["participants"][0]["avatar_url"], json!(AVATAR));
    assert_eq!(body["data"]["participants"][1]["avatar_url"], Value::Null);

    let body = v1_request(
        "PATCH",
        "/openapi/v1/collaboration/sessions/session-1",
        json!({"title": "Renamed"}),
    )
    .await;
    assert_v1_participants_have_avatar(&body["data"]);

    // Session participant operations return the same participant model.
    let body = v1_request(
        "POST",
        "/openapi/v1/collaboration/sessions/session-1/participants",
        json!({"bot_uuid": "plain-bot"}),
    )
    .await;
    assert_eq!(body["data"]["avatar_url"], json!(AVATAR));

    let body = v1_request(
        "PATCH",
        "/openapi/v1/collaboration/sessions/session-1/participants/plain-bot",
        json!({"mode": "auto"}),
    )
    .await;
    assert_eq!(body["data"]["avatar_url"], json!(AVATAR));
}


// ---------------------------------------------------------------------------
// Wire-DTO regressions: WS connect rows and the stored Bot record.
// ---------------------------------------------------------------------------

#[test]
fn ws_connect_participant_view_serializes_no_avatar_fields() {
    let view = WorkbenchParticipantView {
        bot_uuid: "driver-bot".into(),
        role: "driver".into(),
        kind: bcs_service_api::ParticipantKind::Bot,
        mode: Some(ParticipantMode::Auto),
        message_view_scope: MessageViewScope::Full,
    };
    let value = serde_json::to_value(&view).expect("serialize WS connect row");
    assert_no_avatar_keys(&value);
    assert_eq!(value["bot_uuid"], json!("driver-bot"));
}

#[tokio::test]
async fn stored_registered_bot_record_serializes_no_avatar_fields() {
    let temp_dir = TempDir::new().expect("temp bots base dir");
    let repo = store_with_avatar(temp_dir.path().to_path_buf()).await;

    // The display profile read sees the stored avatar…
    let avatars = repo
        .read_avatar_batch(&["stored-bot".to_string()])
        .await
        .expect("avatar batch read");
    assert_eq!(avatars["stored-bot"].as_deref(), Some(AVATAR));

    // …while the historical RegisteredBot wire record that legacy Bot
    // endpoints serialize stays on its pre-avatar field set.
    let record: Option<RegisteredBot> = repo.get("stored-bot").await;
    let record = record.expect("registered record");
    let value = serde_json::to_value(&record).expect("serialize RegisteredBot");
    assert_no_avatar_keys(&value);
    assert_eq!(value["bot_uuid"], json!("stored-bot"));
}

async fn store_with_avatar(bots_base_dir: PathBuf) -> MemoryBotRepo {
    let repo = MemoryBotRepo::with_base_dir(bots_base_dir);
    assert!(repo
        .create_registration_if_absent(
            "stored-bot".to_string(),
            BotCapabilities {
                name: Some("Stored Bot".into()),
                ..Default::default()
            },
            "owner",
            "stored-token"
        )
        .await
        .expect("registration"));
    assert_eq!(
        repo.update_avatar_existing("stored-bot", AvatarChange::Set(AVATAR.into()))
            .await
            .expect("avatar write"),
        bcs_service_api::types::bot_profile::ProfileWrite::Updated
    );
    repo
}

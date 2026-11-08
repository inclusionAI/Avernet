//! Legacy-surface half of the HTTP avatar serialization regression
//! (bot-avatar plan Task 8).
//!
//! Design §5.1 (docs/specs/2026-11-02-bot-avatar-collaboration-repair/design.md):
//! legacy bcs-http routes keep their pre-avatar wire shapes — no participant
//! row, list item, detail, create or PATCH response gains an avatar field,
//! even though the V1 application types the legacy routes delegate to now
//! carry `avatar_url`. The V1 half of the regression lives in the twin file
//! `bcs-api-http/tests/avatar_response_contract.rs`; the versioned adapter's
//! manifest boundary keeps the legacy fixtures on this side.
//!
//! The fixtures run the REAL legacy router over use-case fakes whose rows
//! predate the avatar column, plus the V1 `GroupService` fake whose
//! participants carry avatars — the same application fake the V1 half
//! uses — so the PATCH delegation path is proven to strip the shared
//! V1 serialization fields.

use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::application::{GroupManagementService, GroupQueryService};
use bcs_service_api::application::v1::{
    AddGroupParticipant, ApplicationError, BotFinalDelivery, ChatConfiguration,
    CollaborationConfiguration, CollaborationGroupDetail, CreateGroup, DeleteGroup,
    DeleteGroupParticipant, DeleteResult, GetGroup, GroupDeliveryPolicy, GroupDetail,
    GroupService, GroupStatus, GroupSummary, GroupVisibility, ListGroups, Page, Participant,
    UpdateGroup, UpdateGroupParticipant,
};
use bcs_service_api::types::MessageViewScope;
use bcs_service_api::{
    ActorKind, DmCreateCommand, DmCreateResult, GroupAddMemberCommand, GroupAddMemberResult,
    GroupCreateCommand, GroupDeleteCommand, GroupDeleteResult, GroupDetailCommand,
    GroupDetailResult, GroupKind, GroupListCommand, GroupListEntry, GroupListResult,
    GroupParticipantModeCommand, GroupParticipantModeResult, GroupParticipantView,
    GroupRemoveMemberCommand, GroupRemoveMemberResult, GroupRoutingPolicyCommand,
    GroupRoutingPolicyResult, GroupStatusCommand, GroupTerminateCommand, GroupUpdateLabelCommand,
    GroupUpdateVisibilityCommand, GroupUseCaseError, HumanMentionNotifyMode, ParticipantMode,
    ParticipantRole,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tower::ServiceExt;

const AVATAR: &str = "https://cdn.example.com/avatar/regression.png";

/// Plan helper (verbatim): legacy participant rows stay exactly the
/// pre-avatar shape.
fn assert_legacy_participants_unchanged(value: &Value) {
    for participant in value["participants"].as_array().unwrap() {
        assert!(participant.get("avatar_url").is_none());
    }
}

/// No `avatar` / `avatar_url` key anywhere in a whole JSON tree: legacy
/// surfaces serialize explicit DTOs, so the field must not appear at any
/// nesting level.
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
// V1 application fake: participants already carry the stored avatar.
// ---------------------------------------------------------------------------

fn v1_participant(actor_id: &str, avatar: Option<&str>) -> Participant {
    Participant {
        actor_id: actor_id.into(),
        avatar_url: avatar.map(str::to_string),
        name: Some(format!("{actor_id}-name")),
        actor_kind: ActorKind::Bot,
        role: ParticipantRole::Driver,
        mode: ParticipantMode::Auto,
        message_view_scope: MessageViewScope::Full,
        tags: Vec::new(),
    }
}

fn avatar_group_detail() -> GroupDetail {
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

struct AvatarGroupService;

#[async_trait]
impl GroupService for AvatarGroupService {
    async fn list_groups(
        &self,
        command: ListGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        // The legacy router lists through GroupQueryService, never through
        // the V1 application; an empty page is a loud no-op if that changes.
        Ok(Page::empty(command.offset, command.limit))
    }

    async fn create(&self, _command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Ok(avatar_group_detail())
    }

    async fn get(&self, _query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        Ok(avatar_group_detail())
    }

    async fn update(&self, _command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        Ok(avatar_group_detail())
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

// ---------------------------------------------------------------------------
// Legacy use-case fakes.
// ---------------------------------------------------------------------------

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}

fn legacy_participant_view(bot_uuid: &str) -> GroupParticipantView {
    GroupParticipantView {
        bot_uuid: bot_uuid.into(),
        bot_name: Some(format!("{bot_uuid}-snapshot")),
        kind: None,
        role: "driver".into(),
        actor_kind: ActorKind::Bot,
        mode: None,
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
    }
}

fn legacy_group_detail() -> GroupDetailResult {
    GroupDetailResult {
        group_id: "group-1".into(),
        label: Some("Legacy".into()),
        status: bcs_service_api::GroupStatus::Active,
        driver_bot_id: "driver-bot".into(),
        context: None,
        opening_message: None,
        participants: vec![
            legacy_participant_view("driver-bot"),
            legacy_participant_view("plain-bot"),
        ],
        message_count: 0,
        service_group_uuid: None,
        service_mode: None,
        group_kind: GroupKind::Normal,
        dm_pair_key: None,
        group_strategy: bcs_service_api::GroupStrategy::Chat,
        created_at: 10,
        updated_at: 20,
        chat_url: None,
        context_injected: 0,
        latest_running_session_id: None,
        initial_run: None,
        originator: Some("driver-bot".into()),
        visibility: "private".into(),
        human_mention_notify_mode: HumanMentionNotifyMode::All,
    }
}

struct LegacyGroupQuery;

#[async_trait]
impl GroupQueryService for LegacyGroupQuery {
    async fn list_groups(
        &self,
        cmd: GroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError> {
        Ok(GroupListResult {
            items: vec![GroupListEntry {
                group_id: "group-1".into(),
                label: Some("Legacy".into()),
                driver_bot_id: "driver-bot".into(),
                originator: Some("driver-bot".into()),
                context: None,
                participants: vec![
                    legacy_participant_view("driver-bot"),
                    legacy_participant_view("plain-bot"),
                ],
                participant_count: 2,
                message_count: 0,
                created_at: 10,
                updated_at: 20,
                group_kind: GroupKind::Normal,
                group_strategy: bcs_service_api::GroupStrategy::Chat,
                visibility: "private".into(),
                human_mention_notify_mode: HumanMentionNotifyMode::All,
            }],
            total: 1,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }

    async fn get_group(
        &self,
        _cmd: GroupDetailCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Ok(legacy_group_detail())
    }

    async fn list_bot_groups(
        &self,
        cmd: bcs_service_api::BotGroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError> {
        Ok(GroupListResult {
            items: Vec::new(),
            total: 0,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }
}

struct LegacyGroupManagement;

#[async_trait]
impl GroupManagementService for LegacyGroupManagement {
    async fn create_group(
        &self,
        _cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Ok(legacy_group_detail())
    }

    async fn create_dm(&self, _cmd: DmCreateCommand) -> Result<DmCreateResult, GroupUseCaseError> {
        let mut detail = legacy_group_detail();
        detail.group_kind = GroupKind::Dm;
        detail.dm_pair_key = Some("human_alice|plain-bot".into());
        Ok(DmCreateResult {
            group: detail,
            created: true,
        })
    }

    async fn update_status(
        &self,
        cmd: GroupStatusCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let _ = cmd;
        Err(GroupUseCaseError::Conflict(
            "not exercised by this contract test".into(),
        ))
    }

    async fn add_member(
        &self,
        _cmd: GroupAddMemberCommand,
    ) -> Result<GroupAddMemberResult, GroupUseCaseError> {
        unreachable!("member writes are not exercised by this contract test")
    }

    async fn remove_member(
        &self,
        _cmd: GroupRemoveMemberCommand,
    ) -> Result<GroupRemoveMemberResult, GroupUseCaseError> {
        unreachable!("member writes are not exercised by this contract test")
    }

    async fn delete_group(
        &self,
        _cmd: GroupDeleteCommand,
    ) -> Result<GroupDeleteResult, GroupUseCaseError> {
        unreachable!("deletes are not exercised by this contract test")
    }

    async fn terminate_group(
        &self,
        _cmd: GroupTerminateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        unreachable!("termination is not exercised by this contract test")
    }

    async fn update_label(
        &self,
        _cmd: GroupUpdateLabelCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        unreachable!("rename is not exercised by this contract test")
    }

    async fn update_visibility(
        &self,
        _cmd: GroupUpdateVisibilityCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        unreachable!("visibility is not exercised by this contract test")
    }

    async fn update_routing_policy(
        &self,
        _cmd: GroupRoutingPolicyCommand,
    ) -> Result<GroupRoutingPolicyResult, GroupUseCaseError> {
        unreachable!("routing is not exercised by this contract test")
    }

    async fn update_participant_mode(
        &self,
        _cmd: GroupParticipantModeCommand,
    ) -> Result<GroupParticipantModeResult, GroupUseCaseError> {
        unreachable!("mode writes are not exercised by this contract test")
    }
}

fn legacy_router() -> axum::Router {
    let services = Services::builder()
        .group_query(Arc::new(LegacyGroupQuery))
        .group_management(Arc::new(LegacyGroupManagement))
        .build_for_test();
    build_router(
        HttpAppState::new(services)
            .with_group_application(Arc::new(AvatarGroupService))
            .with_user_identity(Arc::new(ChainUserIdentityPort::new(static_auth_chain(
                "alice", "Alice",
            )))),
    )
}

async fn legacy_request(method: &str, uri: &str, body: Value) -> Value {
    let response = legacy_router()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .expect("request"),
        )
        .await
        .expect("response");
    let status = response.status();
    let json = response_json(response).await;
    assert_eq!(status, StatusCode::OK, "legacy response: {json}");
    json
}

// ---------------------------------------------------------------------------
// Regressions.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn legacy_group_list_and_detail_keep_participants_unchanged() {
    let body = legacy_request("GET", "/groups?offset=0&limit=20", json!({})).await;
    for item in body["items"].as_array().expect("legacy list items") {
        assert_no_avatar_keys(item);
    }
    assert_no_avatar_keys(&body);

    let body = legacy_request("GET", "/groups/group-1", json!({})).await;
    assert_legacy_participants_unchanged(&body);
    assert_no_avatar_keys(&body);
    assert_eq!(body["participants"][0]["bot_uuid"], json!("driver-bot"));
}

#[tokio::test]
async fn legacy_group_create_keeps_participants_unchanged() {
    let body = legacy_request(
        "POST",
        "/groups",
        json!({
            "id": "group-1",
            "driver_bot": "driver-bot",
            "label": "Legacy room",
            "participants": [
                { "bot_uuid": "driver-bot", "role": "driver" },
                { "bot_uuid": "plain-bot", "role": "consultant" }
            ]
        }),
    )
    .await;
    assert_legacy_participants_unchanged(&body);
    assert_no_avatar_keys(&body);
    assert_eq!(body["participants"], json!(["driver-bot", "plain-bot"]));

    // DM create reuses the same legacy projection.
    let body = legacy_request(
        "POST",
        "/groups",
        json!({
            "group_kind": "dm",
            "driver_bot": "plain-bot",
            "target_actor_id": "plain-bot"
        }),
    )
    .await;
    assert_legacy_participants_unchanged(&body);
    assert_no_avatar_keys(&body);
}

#[tokio::test]
async fn legacy_patch_group_keeps_participants_unchanged() {
    let body = legacy_request(
        "PATCH",
        "/groups/group-1",
        json!({"name": "Renamed"}),
    )
    .await;
    // The PATCH route must take its response from a legacy projection: the
    // shared V1 GroupDetail now carries avatar_url and must NOT pass
    // through this legacy surface (design §5.1).
    assert_legacy_participants_unchanged(&body);
    assert_no_avatar_keys(&body);
    // The pinned response contract of the legacy PATCH route still holds.
    assert_eq!(body["group_id"], json!("group-1"));
    assert_eq!(body["name"], json!("Avatar Regression"));
}

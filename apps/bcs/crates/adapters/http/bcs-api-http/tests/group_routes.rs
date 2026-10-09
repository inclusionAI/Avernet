use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, BotRegistration, CreateBotFriendRequest,
    CreateGroupInvitation, CreateSessionInvitation, DeleteBotFriendship, FriendRequest,
    Friendship, FriendshipService, Invitation, InvitationAcceptResult, InvitationService,
    IssueRegisterToken, ListBotFriendRequests, ListBotFriendships, RegisterBot, RegisterService,
    RegisterTokenView, RejectFriendRequest,
};
use bcs_service_api::application::v1::{
    AddGroupParticipant, ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity,
    BotFinalDelivery, ChatConfiguration, CollaborationConfiguration, CollaborationGroupDetail,
    CreateGroup, CreateGroupOutcome, CreateGroupSpec, DeleteGroup, DeleteGroupParticipant,
    DeleteResult, GetGroup, GroupDeliveryPolicy, GroupDetail, GroupService, GroupStatus,
    GroupStrategy, GroupSummary, GroupVisibility, InlineGroupEventSubscriptionRequest,
    ListGroups, ListPublicGroups, MembershipFilter,
    DirectMessageGroupSummary, Membership, NormalGroupSummary, Page, Participant,
    UpdateGroup, UpdateGroupParticipant,
};
use bcs_service_api::application::v1::{
    AddSessionParticipant, CompleteSession, CreateSession, CreateSessionOutcome, DeleteSession,
    DeleteSessionParticipant, GetSession, ListSessionMessages, ListSessions,
    SessionCompletionResult, SessionDetail, SessionMessageService, SessionParticipant,
    SessionService, SessionSummary, UpdateSession, UpdateSessionParticipant,
};
use bcs_service_api::{
    ActorKind, InitialGroupRun, InitialGroupRunActivityKind, InitialGroupRunState, ParticipantMode,
    ParticipantRole,
};
use serde_json::{Value, json};
use tower::ServiceExt;

struct HeaderVerifier {
    caller: AuthenticatedCaller,
}

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(self.caller.clone())
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

#[derive(Default)]
struct FakeGroupService {
    list: Mutex<Option<ListGroups>>,
    list_public: Mutex<Option<ListPublicGroups>>,
    populated_list_items: Mutex<Vec<GroupSummary>>,
    detail_override: Mutex<Option<GroupDetail>>,
    created: Mutex<Option<CreateGroup>>,
    inline_event_subscriptions: Mutex<Vec<InlineGroupEventSubscriptionRequest>>,
    reuse_dm: AtomicBool,
    initial_session_id: Mutex<Option<String>>,
    initial_run: Mutex<Option<InitialGroupRun>>,
    get: Mutex<Option<GetGroup>>,
    updated: Mutex<Option<UpdateGroup>>,
    deleted: Mutex<Option<DeleteGroup>>,
    added_participant: Mutex<Option<AddGroupParticipant>>,
    updated_participant: Mutex<Option<UpdateGroupParticipant>>,
    removed_participant: Mutex<Option<DeleteGroupParticipant>>,
}

#[async_trait]
impl GroupService for FakeGroupService {
    async fn list_groups(
        &self,
        command: ListGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        let offset = command.offset;
        let limit = command.limit;
        *self.list.lock().expect("list lock") = Some(command);
        let items = self.populated_list_items.lock().expect("populated list lock");
        if items.is_empty() {
            Ok(Page::empty(0, 20))
        } else {
            let total = items.len() as u64;
            Ok(Page {
                items: items.clone(),
                total,
                offset,
                limit,
            })
        }
    }

    async fn list_public_groups(
        &self,
        command: ListPublicGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        let offset = command.offset;
        let limit = command.limit;
        *self.list_public.lock().expect("list public lock") = Some(command);
        let items = self
            .populated_list_items
            .lock()
            .expect("populated list lock")
            .clone();
        Ok(Page {
            total: items.len() as u64,
            items,
            offset,
            limit,
        })
    }

    async fn create(&self, command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        *self.created.lock().expect("create lock") = Some(command);
        Ok(self
            .detail_override
            .lock()
            .expect("detail lock")
            .clone()
            .unwrap_or_else(group_detail))
    }

    async fn create_with_outcome(
        &self,
        command: CreateGroup,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        let group = self.create(command).await?;
        Ok(CreateGroupOutcome {
            group,
            created: !self.reuse_dm.load(Ordering::Relaxed),
            initial_session_id: self
                .initial_session_id
                .lock()
                .expect("initial Session lock")
                .clone(),
            initial_run: self.initial_run.lock().expect("initial run lock").clone(),
            event_subscriptions: Vec::new(),
        })
    }

    async fn create_with_event_subscriptions(
        &self,
        command: CreateGroup,
        event_subscriptions: Vec<InlineGroupEventSubscriptionRequest>,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        *self
            .inline_event_subscriptions
            .lock()
            .expect("inline Event Subscription lock") = event_subscriptions;
        self.create_with_outcome(command).await
    }

    async fn get(&self, query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        *self.get.lock().expect("get lock") = Some(query);
        Ok(self
            .detail_override
            .lock()
            .expect("detail lock")
            .clone()
            .unwrap_or_else(group_detail))
    }

    async fn update(&self, command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        *self.updated.lock().expect("update lock") = Some(command);
        Ok(self
            .detail_override
            .lock()
            .expect("detail lock")
            .clone()
            .unwrap_or_else(group_detail))
    }

    async fn delete(&self, command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        *self.deleted.lock().expect("delete lock") = Some(command);
        Ok(DeleteResult { deleted: true })
    }

    async fn add_participant(
        &self,
        command: AddGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        *self.added_participant.lock().expect("add participant lock") = Some(command.clone());
        Ok(Participant {
            actor_id: command.actor_id,
            actor_kind: ActorKind::Bot,
            name: None,
            role: ParticipantRole::Consultant,
            tags: Vec::new(),
            mode: ParticipantMode::Auto,
            message_view_scope: bcs_domain::MessageViewScope::Full,
        })
    }

    async fn update_participant(
        &self,
        command: UpdateGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        *self
            .updated_participant
            .lock()
            .expect("update participant lock") = Some(command.clone());
        Ok(Participant {
            actor_id: command.actor_id,
            actor_kind: ActorKind::Bot,
            name: None,
            role: ParticipantRole::Consultant,
            tags: Vec::new(),
            mode: command.mode.unwrap_or(ParticipantMode::Auto),
            message_view_scope: command
                .message_view_scope
                .unwrap_or(bcs_domain::MessageViewScope::Full),
        })
    }

    async fn delete_participant(
        &self,
        command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        *self
            .removed_participant
            .lock()
            .expect("remove participant lock") = Some(command);
        Ok(DeleteResult { deleted: true })
    }
}

struct NoopSessionService;

#[async_trait]
impl SessionService for NoopSessionService {
    async fn create(
        &self,
        _command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn list(&self, _command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn get(&self, _query: GetSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn update(&self, _command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn delete(&self, _command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn complete(
        &self,
        _command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn collect(
        &self,
        _: bcs_service_api::application::v1::CollectSession,
    ) -> Result<bcs_service_api::application::v1::SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn uncollect(
        &self,
        _: bcs_service_api::application::v1::UncollectSession,
    ) -> Result<bcs_service_api::application::v1::SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn add_participant(
        &self,
        _command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn update_participant(
        &self,
        _command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn delete_participant(
        &self,
        _command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }
}

struct NoopSessionMessageService;

#[async_trait]
impl SessionMessageService for NoopSessionMessageService {
    async fn list(
        &self,
        _query: ListSessionMessages,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Err(ApplicationError::internal(
            "session messages not configured",
        ))
    }
}

struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(
        &self,
        _command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }

    async fn create_session_invitation(
        &self,
        _command: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }

    async fn accept_invitation(
        &self,
        _command: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }
}

struct NoopFriendshipService;

#[async_trait]
impl FriendshipService for NoopFriendshipService {
    async fn list_bot_friendships(
        &self,
        _command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn delete_bot_friendship(
        &self,
        _command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn create_bot_friend_request(
        &self,
        _command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn list_bot_friend_requests(
        &self,
        _command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn accept_friend_request(
        &self,
        _command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn reject_friend_request(
        &self,
        _command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }
}

struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(
        &self,
        _command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
    }

    async fn register_bot(
        &self,
        _command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
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

fn caller_user_id(caller: &AuthenticatedCaller) -> &str {
    caller.user.as_ref().expect("User identity").id.as_str()
}

fn group_detail() -> GroupDetail {
    GroupDetail::Collaboration(CollaborationGroupDetail {
        group_id: "group-1".into(),
        version: 1,
        name: Some("Planning".into()),
        status: GroupStatus::Active,
        visibility: GroupVisibility::Private,
        context: None,
        opening_message: None,
        originator_actor_id: "bot-1".into(),
        participants: vec![Participant {
            actor_id: "bot-1".into(),
            actor_kind: ActorKind::Bot,
            name: Some("Bot 1".into()),
            role: ParticipantRole::Driver,
            tags: Vec::new(),
            mode: ParticipantMode::Auto,
            message_view_scope: bcs_domain::MessageViewScope::Full,
        }],
        driver_bot_uuid: "bot-1".into(),
        driver_bot_owner: Some("human_owner".into()),
        driver_bot_owner_name: Some("Owner Name".into()),
        collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
            delivery_policy: GroupDeliveryPolicy {
                bot_final_delivery: BotFinalDelivery::SendToDriver,
            },
        }),
        human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
        created_at: 1,
        updated_at: 2,
    })
}

fn test_router(service: Arc<FakeGroupService>) -> axum::Router {
    router(ApiState::new(
        service,
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier { caller: caller() }),
    ))
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

fn authenticated_request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-123")
        .body(Body::from(body.to_string()))
        .expect("request")
}

// Step 4 V1 adapter: successful Group detail/list responses surface the
// stored `human_mention_notify_mode` and leave every existing client-facing
// field unchanged.
fn populated_group_summaries() -> Vec<GroupSummary> {
    vec![
        GroupSummary::Normal(NormalGroupSummary {
            group_id: "group-normal".into(),
            version: 1,
            name: None,
            context: None,
            status: GroupStatus::Active,
            visibility: GroupVisibility::Private,
            membership: Membership::Direct,
            originator_actor_id: "bot-1".into(),
            participant_count: 2,
            driver_bot_uuid: "bot-1".into(),
            driver_bot_name: Some("Bot 1".into()),
            strategy: GroupStrategy::Chat,
            human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
            created_at: 1,
            updated_at: 2,
        }),
        GroupSummary::DirectMessage(DirectMessageGroupSummary {
            group_id: "group-dm".into(),
            version: 1,
            name: None,
            context: None,
            status: GroupStatus::Active,
            visibility: GroupVisibility::Private,
            membership: Membership::Direct,
            originator_actor_id: "bot-1".into(),
            participant_count: 2,
            peer_actor: None,
            human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::DriverBotOnly,
            created_at: 1,
            updated_at: 2,
        }),
    ]
}

#[path = "group_routes/existing.rs"]
mod existing;

#[path = "group_routes/driver_metadata.rs"]
mod driver_metadata;

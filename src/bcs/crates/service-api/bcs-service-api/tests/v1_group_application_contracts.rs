use std::collections::BTreeSet;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedUser, BotFinalDelivery, DeleteGroup, DeleteResult,
    DirectMessageGroupSummary, GetGroup, GroupDeliveryPolicy, GroupDetail, GroupKindFilter,
    GroupService, GroupStatus, GroupSummary, GroupVisibility, ListBotGroups, Membership,
    MembershipFilter, Page, Principal, UpdateGroup,
};

struct NoopGroupService;

#[async_trait]
impl GroupService for NoopGroupService {
    async fn list_bot_groups(
        &self,
        _command: ListBotGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        Ok(Page::empty(0, 20))
    }

    async fn create(
        &self,
        _command: bcs_service_api::application::v1::CreateGroup,
    ) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn get(&self, _query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn update(&self, _command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn delete(&self, _command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        Ok(DeleteResult {
            group_id: "group-1".into(),
            deleted: false,
        })
    }
}

#[test]
fn principal_preserves_gateway_identity_without_bot_impersonation() {
    let bot = Principal::bot("bot-123", "tenant-a", BTreeSet::new());
    assert_eq!(bot.actor_id(), "bot-123");
    assert_eq!(bot.bot_uuid(), Some("bot-123"));

    let human = Principal::human(
        "human-actor-1",
        AuthenticatedUser {
            id: "staff-1".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        },
        "tenant-a",
        BTreeSet::new(),
    );
    assert_eq!(human.actor_id(), "human-actor-1");
    assert_eq!(human.bot_uuid(), None);
    assert_eq!(
        human.authenticated_user().expect("human subject").id,
        "staff-1"
    );
}

#[test]
fn list_command_carries_principal_and_all_approved_filters() {
    let command = ListBotGroups {
        principal: Principal::bot("bot-1", "tenant-a", BTreeSet::new()),
        bot_uuid: "bot-1".into(),
        offset: 10,
        limit: 25,
        q: Some("planning".into()),
        membership: MembershipFilter::SessionOnly,
        kind: GroupKindFilter::All,
        strategy: Some(bcs_service_api::application::v1::GroupStrategy::StateMachine),
    };

    assert_eq!(command.principal.actor_id(), "bot-1");
    assert_eq!(command.membership, MembershipFilter::SessionOnly);
    assert_eq!(command.kind, GroupKindFilter::All);
}

#[test]
fn direct_message_summary_cannot_carry_normal_group_fields() {
    let summary = GroupSummary::DirectMessage(DirectMessageGroupSummary {
        group_id: "dm-1".into(),
        version: 1,
        name: None,
        status: GroupStatus::Active,
        visibility: GroupVisibility::Private,
        membership: Membership::Direct,
        originator_actor_id: "bot-a".into(),
        participant_count: 2,
        peer_actor: None,
        created_at: 1,
        updated_at: 2,
    });

    let json = serde_json::to_value(summary).expect("serialize summary");
    assert_eq!(json["kind"], "dm");
    assert!(json.get("strategy").is_none());
    assert!(json.get("driver_bot_uuid").is_none());
    assert!(json.get("delivery_policy").is_none());
}

#[test]
fn delivery_policy_is_narrower_than_legacy_routing_policy() {
    let policy = GroupDeliveryPolicy {
        bot_final_delivery: BotFinalDelivery::InjectObservers,
    };

    let json = serde_json::to_value(policy).expect("serialize policy");
    assert_eq!(json["bot_final_delivery"], "inject_observers");
    assert!(json.get("mode").is_none());
    assert!(json.get("sender_routes").is_none());
}

#[test]
fn group_service_is_object_safe() {
    fn accepts_service(_: &dyn GroupService) {}
    accepts_service(&NoopGroupService);
}

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{
    ActorDirectory, Bot, BotCandidateSearchCore, BotOnboarding, BotCore,
    EmptyWorkerProfileCoreService, HumanActor,
};
use bcs_group::{GroupManagement, GroupStore};
use bcs_group_store::MemoryGroupRepo;
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_http::{
    router::build_router,
    state::{
        ChatRunCleanupPort, ChainUserIdentityPort, HttpAppState, VisibilitySyncPort,
        VisibilitySyncRequest,
    },
};
use bcs_service_api::{
    A2aChatCommand, A2aChatOutcome, A2aChatRunService, A2aChatService, A2aRunStatus,
    ActorDirectoryService, ActorStatus, AsyncA2aChatAccepted, AsyncA2aChatCommand,
    BotCapabilities, BotOnboardingService,
    BotRegistryCoreService, CallerContext, ChatRunCancelCommand, ChatRunQueryCommand,
    FriendCoreService, Group, GroupCoreService, GroupKind, GroupStatus,
    GroupStrategy, HumanActorService, Participant, ParticipantRole, RelationCoreService,
    ServiceResult, SessionRepoPort, Skill,
};
use bcs_services_container::{Services, ServicesBuilder};
use bcs_test_support::{NoopFriendCoreService, NoopRelationCoreService};
use serde_json::Value;
use std::sync::Arc;
use tempfile::TempDir;
use tokio::sync::{Mutex, Notify};
use tower::ServiceExt;


fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(principal))]))
}


#[derive(Default)]
struct StaticFriendCoreService {
    friends: Vec<(String, String)>,
}

impl StaticFriendCoreService {
    fn new(friends: Vec<(&str, &str)>) -> Self {
        Self {
            friends: friends
                .into_iter()
                .map(|(left, right)| (left.to_string(), right.to_string()))
                .collect(),
        }
    }
}

fn noop_relation() -> Arc<dyn RelationCoreService> {
    Arc::new(NoopRelationCoreService)
}



fn services_builder_with_bot_use_cases(registry: Arc<BotCore>) -> ServicesBuilder {
    services_builder_with_bot_use_cases_and_friend(registry, Arc::new(NoopFriendCoreService))
}

fn services_builder_with_bot_use_cases_and_friend(
    registry: Arc<BotCore>,
    friend: Arc<dyn FriendCoreService>,
) -> ServicesBuilder {
    let registry_service: Arc<dyn BotRegistryCoreService> = registry;
    let bot_use_cases = Arc::new(
        Bot::new_with_friend(registry_service.clone(), friend.clone())
            // 删除/配置/状态 consult the CURRENT
            // owner edges (shared_route_authority).
            .with_bot_authority(shared_route_authority()),
    );
    Services::builder()
        .registry(registry_service)
        .friend(friend)
        .bot_query(bot_use_cases.clone())
        .bot_management(bot_use_cases.clone())
        .bot_discovery(bot_use_cases)
}

/// A process-wide LAZY authority chain for the
/// route-contract suites: `save_created_by` owner
/// facts are mirrored into it via
/// `grant_owner_by_registry`, and the control legs
/// read them live.
fn shared_route_authority(
) -> Arc<bcs_test_support::LazySeedAuthorityHook> {
    static AUTHORITY: std::sync::OnceLock<
        Arc<bcs_test_support::LazySeedAuthorityHook>,
    > = std::sync::OnceLock::new();
    AUTHORITY
        .get_or_init(|| {
            bcs_test_support::lazy_authority_chain().hook
        })
        .clone()
}


#[async_trait::async_trait]
impl FriendCoreService for StaticFriendCoreService {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        self.friends
            .iter()
            .filter_map(|(left, right)| {
                if left == bot_id {
                    Some(right.clone())
                } else if right == bot_id {
                    Some(left.clone())
                } else {
                    None
                }
            })
            .collect()
    }

    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> bool {
        self.friends.iter().any(|(left, right)| {
            (left == bot_a && right == bot_b) || (left == bot_b && right == bot_a)
        })
    }

    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()> {
        for other in others {
            if !self.are_friends(bot_id, other).await {
                return Err(bcs_service_api::ServiceError::NotFriends(vec![
                    other.clone(),
                ]));
            }
        }
        Ok(())
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        unreachable!("add_friendship is not used by route contract tests")
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        unreachable!("remove_all_friendships is not used by route contract tests")
    }
}

















#[path = "route_contract/bots_success.rs"]
mod bots_success;
#[path = "route_contract/bots_failure.rs"]
mod bots_failure;
#[path = "route_contract/identity.rs"]
mod identity;
#[path = "route_contract/chat_runs.rs"]
mod chat_runs;
#[path = "route_contract/health.rs"]
mod health;
#[path = "route_contract/groups.rs"]
mod groups;

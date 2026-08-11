#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use async_trait::async_trait;
use bcs_app_bot::{BotServiceConfig, BotServiceImpl};
use bcs_bot::{BotControlPlaneCore, BotCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_friend::FriendCore;
use bcs_service_api::application::v1::{
    ApplicationError, Bot, BotCandidatePurpose, BotDescriptorPatch, BotKind, BotPatch,
    BotReachability, BotService, BotStatus, BotVisibility, GetBot, ListBotCandidates, ListMyBots,
    QueryBots, UpdateBot,
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotControlPlaneCoreService,
    BotControlPlaneDescriptor, BotControlPlaneRecord, BotRegistryCoreService, BotRepoPort,
    FriendCoreService, ProviderBotBinding, ProviderBotBindingRepoPort, ProviderRecord,
    ProviderRepoPort, ServiceError, ServiceResult, Skill,
};
use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};

#[test]
fn v1_bot_commands_expose_the_approved_control_plane_surface() {
    let caller = human_caller("staff-1");

    let _ = ListBotCandidates {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
        purpose: Default::default(),
        name: None,
        offset: 0,
        limit: 20,
    };
    let _ = QueryBots {
        caller: caller.clone(),
        bot_ids: vec!["bot-1".to_string()],
    };
    let _ = GetBot {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
    };
    let _ = UpdateBot {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
        patch: BotPatch {
            name: Some("Renamed".to_string()),
            visibility: Some(BotVisibility::Protected),
            status: Some(BotStatus::Online),
            descriptor: Some(BotDescriptorPatch {
                summary: Some("summary".to_string()),
                domains: None,
                skills: None,
                scopes: None,
            }),
        },
    };
    let _ = ListMyBots {
        caller,
        kind: Some(BotKind::Bot),
        name: None,
        status: None,
        reachability: Some(BotReachability::Reachable),
        offset: 0,
        limit: 20,
    };

    let _assert_object_safe: fn(&dyn BotService) = |_| {};
}

struct Fixture {
    service: BotServiceImpl,
    repo: Arc<MemoryBotRepo>,
    friends: Arc<FriendCore>,
    providers: Arc<MemoryProviderStore>,
    _temp: tempfile::TempDir,
}

impl Fixture {
    fn new() -> Self {
        let temp = tempfile::tempdir().expect("temp dir");
        let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
        let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
        let friends = Arc::new(FriendCore::memory());
        let providers = Arc::new(MemoryProviderStore::new());
        let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(
            BotControlPlaneCore::new(repo.clone(), providers.clone(), providers.clone()),
        );
        let env = bcs_config::resolve_env_str();
        let service = BotServiceImpl::new(
            control_plane,
            registry,
            friends.clone(),
            BotServiceConfig { env: env.clone() },
        );
        Self {
            service,
            repo,
            friends,
            providers,
            _temp: temp,
        }
    }

    async fn add_bot(&self, bot_id: &str, owner: &str, visibility: &str, status: ActorStatus) {
        self.repo
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    summary: Some(format!("summary-{bot_id}")),
                    domains: vec!["planning".to_string()],
                    skills: vec![Skill::with_description("plan", "Make a plan")],
                    scopes: vec!["workspace".to_string()],
                    visibility: visibility.to_string(),
                    agent_code: Some(format!("agent-{bot_id}")),
                    ..Default::default()
                },
                owner,
                &format!("token-{bot_id}"),
            )
            .await
            .expect("register bot");
        self.repo
            .update_actor_status(bot_id, status)
            .await
            .expect("update actor status");
    }
}

struct AuthorizationProbeCore {
    record: BotControlPlaneRecord,
}

#[async_trait]
impl BotControlPlaneCoreService for AuthorizationProbeCore {
    async fn get_record(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        Ok(Some(self.record.clone()))
    }

    async fn get(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "Provider hydration must happen after authorization".to_string(),
        ))
    }

    async fn get_by_ids(
        &self,
        _bot_ids: &[String],
        _env: &str,
    ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        unreachable!("not used by authorization-priority test")
    }

    async fn list_candidates(
        &self,
        _query: bcs_service_api::BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<bcs_service_api::BotControlPlaneCandidate>, u64)> {
        unreachable!("not used by authorization-priority test")
    }

    async fn list_by_creator(
        &self,
        _query: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        unreachable!("not used by authorization-priority test")
    }

    async fn patch(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        unreachable!("not used by authorization-priority test")
    }
}

#[tokio::test]
async fn ownership_denial_precedes_provider_hydration() {
    let env = bcs_config::resolve_env_str();
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(AuthorizationProbeCore {
        record: BotControlPlaneRecord {
            bot_id: "owned".to_string(),
            kind: ActorKind::Bot,
            name: "Owned".to_string(),
            visibility: "public".to_string(),
            status: ActorStatus::Online,
            env: env.clone(),
            created_by: Some("staff-1".to_string()),
            descriptor: BotControlPlaneDescriptor {
                summary: String::new(),
                domains: Vec::new(),
                skills: Vec::new(),
                scopes: Vec::new(),
            },
            agent_code: None,
            created_at: 1,
            updated_at: 1,
        },
    });
    let service = BotServiceImpl::new(
        control_plane,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        BotServiceConfig { env },
    );

    let error = service
        .update(UpdateBot {
            caller: human_caller("staff-2"),
            bot_id: "owned".to_string(),
            patch: BotPatch {
                name: Some("Nope".to_string()),
                ..Default::default()
            },
        })
        .await
        .expect_err("ownership denial must not hydrate Provider metadata");

    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn candidates_require_a_human_owner_and_a_physical_acting_bot() {
    let fixture = Fixture::new();
    fixture
        .add_bot("acting", "staff-1", "private", ActorStatus::Online)
        .await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");

    let error = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-2"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect_err("non-owner must fail");
    assert_eq!(error.code(), "forbidden");

    let error = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect_err("human acting row must fail");
    assert_eq!(error.code(), "invalid_bot_kind");
}

#[tokio::test]
async fn collaboration_candidates_include_private_friends_without_status_filtering() {
    let fixture = Fixture::new();
    fixture
        .add_bot("acting", "staff-1", "private", ActorStatus::Online)
        .await;
    fixture
        .add_bot("private-friend", "staff-2", "private", ActorStatus::Hidden)
        .await;
    fixture
        .friends
        .add_friendship("acting", "private-friend")
        .await
        .expect("add friendship");

    let page = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Collaboration,
            name: Some(" FRIEND ".to_string()),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("list candidates");
    assert_eq!(page.total, 1);
    assert_eq!(page.items[0].bot.bot_id, "private-friend");
    assert_eq!(page.items[0].bot.status, BotStatus::Hidden);
    assert_eq!(page.items[0].bot.reachability, BotReachability::Unreachable);
    assert!(page.items[0].is_friend);
}

#[tokio::test]
async fn query_preserves_first_occurrence_and_projects_both_kinds_provider_and_reachability() {
    let fixture = Fixture::new();
    fixture
        .add_bot("physical", "staff-1", "private", ActorStatus::Online)
        .await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");
    fixture
        .repo
        .register_streaming_connection("physical".to_string())
        .await
        .expect("connect physical bot");
    fixture
        .providers
        .insert_provider(ProviderRecord {
            provider_id: "provider-1".to_string(),
            name: "Provider One".to_string(),
            config: "{}".to_string(),
            created_by: "staff-1".to_string(),
            owners: "[]".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("insert provider");
    fixture
        .providers
        .insert_binding(ProviderBotBinding {
            bot_uuid: "physical".to_string(),
            provider_id: "provider-1".to_string(),
            provider_bot_ref: "secret-internal-ref".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("insert binding");

    let bots = fixture
        .service
        .query(QueryBots {
            caller: human_caller("staff-2"),
            bot_ids: vec![
                "human_staff-1".to_string(),
                "missing".to_string(),
                "physical".to_string(),
                "human_staff-1".to_string(),
            ],
        })
        .await
        .expect("query bots");
    assert_eq!(
        bots.iter().map(Bot::bot_id).collect::<Vec<_>>(),
        vec!["human_staff-1", "physical"]
    );
    let Bot::Physical(physical) = &bots[1] else {
        panic!("expected physical bot");
    };
    assert_eq!(physical.reachability, BotReachability::Reachable);
    assert_eq!(
        physical.provider.as_ref().map(|p| p.name.as_str()),
        Some("Provider One")
    );
    assert_eq!(physical.agent_code.as_deref(), Some("agent-physical"));
}

#[tokio::test]
async fn update_requires_created_by_and_rejects_descriptor_for_human() {
    let fixture = Fixture::new();
    fixture
        .add_bot("owned", "staff-1", "public", ActorStatus::Online)
        .await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");

    let error = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-2"),
            bot_id: "owned".to_string(),
            patch: BotPatch {
                name: Some("Nope".to_string()),
                ..Default::default()
            },
        })
        .await
        .expect_err("non-owner update");
    assert_eq!(error.code(), "forbidden");

    let error = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            patch: BotPatch {
                descriptor: Some(BotDescriptorPatch {
                    summary: Some("not allowed".to_string()),
                    ..Default::default()
                }),
                ..Default::default()
            },
        })
        .await
        .expect_err("human descriptor update");
    assert_eq!(error.code(), "invalid_bot_kind");

    let updated = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-1"),
            bot_id: "owned".to_string(),
            patch: BotPatch {
                name: Some(" Renamed ".to_string()),
                visibility: Some(BotVisibility::Protected),
                status: Some(BotStatus::Hidden),
                descriptor: Some(BotDescriptorPatch {
                    domains: Some(vec![]),
                    scopes: Some(vec!["new-scope".to_string()]),
                    ..Default::default()
                }),
            },
        })
        .await
        .expect("owner update");
    let Bot::Physical(updated) = updated else {
        panic!("expected physical bot");
    };
    assert_eq!(updated.name, "Renamed");
    assert_eq!(updated.visibility, BotVisibility::Protected);
    assert_eq!(updated.status, BotStatus::Hidden);
    assert!(updated.descriptor.domains.is_empty());
    assert_eq!(updated.descriptor.scopes, vec!["new-scope"]);
}

#[tokio::test]
async fn mine_accepts_tenantless_users_and_callers_without_user_are_forbidden() {
    let fixture = Fixture::new();
    fixture
        .add_bot("reachable", "staff-1", "public", ActorStatus::Online)
        .await;
    fixture
        .add_bot("unreachable", "staff-1", "public", ActorStatus::Online)
        .await;
    fixture
        .repo
        .register_streaming_connection("reachable".to_string())
        .await
        .expect("connect bot");
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");

    let mut tenantless_user = human_caller("staff-1");
    tenantless_user.tenant = None;
    let page = fixture
        .service
        .list_mine(ListMyBots {
            caller: tenantless_user,
            kind: None,
            name: None,
            status: None,
            reachability: Some(BotReachability::Reachable),
            offset: 0,
            limit: 1,
        })
        .await
        .expect("list mine");
    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].bot_id(), "reachable");

    let error = fixture
        .service
        .get(GetBot {
            caller: bot_only_caller("reachable"),
            bot_id: "reachable".to_string(),
        })
        .await
        .expect_err("caller without User must be rejected");
    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn invalid_application_inputs_use_stable_codes() {
    let fixture = Fixture::new();
    let error = fixture
        .service
        .query(QueryBots {
            caller: human_caller("staff-1"),
            bot_ids: (0..101).map(|index| format!("bot-{index}")).collect(),
        })
        .await
        .expect_err("oversized query must fail");
    assert!(matches!(error, ApplicationError::InvalidInput { .. }));
    assert_eq!(error.code(), "invalid_request");
}

fn human_caller(staff_no: &str) -> bcs_service_api::application::v1::AuthenticatedCaller {
    bcs_service_api::application::v1::AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: Some(bcs_service_api::application::v1::AuthenticatedUserIdentity {
            id: staff_no.to_string(),
            username: staff_no.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn bot_only_caller(bot_uuid: &str) -> bcs_service_api::application::v1::AuthenticatedCaller {
    bcs_service_api::application::v1::AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: None,
        bot: Some(bcs_service_api::application::v1::AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: "staff-1".into(),
            app_id: 1,
            agent_code: "agent".into(),
        }),
        app: None,
        access_key: None,
    }
}

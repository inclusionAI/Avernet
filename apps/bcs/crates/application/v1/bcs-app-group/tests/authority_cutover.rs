//! bcs-app-group consumer-cutover tests (rows 8/10 of
//! the mounted-entry table; bot-authority plan
//! Task 6.2/6.3, spec §3.3 and §8.1).
//!
//! The V1 Group facade runs against a REAL authority
//! chain: the A(owner)/M(manager)/X matrix is
//! exercised at the list/detail/view-actor entries,
//! the friendless private-Group sponsorship (§8.1) is
//! exercised at the real create entry, and the
//! hybrid-credential resolution entries prove the
//! resolver swap (row 10).

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use bcs_app_group::{GroupServiceImpl, GroupProvisioningReconciler};
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_friend::FriendCore;
use bcs_group::{GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedBotIdentity, AuthenticatedCaller,
    AuthenticatedUserIdentity, BotFinalDelivery,
    CollaborationConfiguration, CreateCollaborationGroup, CreateGroup,
    CreateGroupSpec, GetGroup, GroupKindFilter, GroupService,
    GroupVisibility, ListGroups, MembershipFilter,
};
use bcs_service_api::application::v1 as application_v1;
use bcs_service_api::application::SessionManagementService;
use bcs_service_api::application::v1::ChatConfiguration;
use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, FriendCoreService,
    Group as DomainGroup, GroupCoreService, GroupStrategy, Participant,
    ParticipantRole,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{
    authority_hook_for, failing_authority_hook, grant_owner_edge,
    NoopSystemMessageService,
};

struct Fixture {
    service: GroupServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    authority_repo: Arc<MemoryBotRepo>,
}

impl Fixture {
    async fn new() -> Self {
        let bot_repo = Arc::new(MemoryBotRepo::new());
        let bots = Arc::new(BotCore::with_repo(bot_repo.clone()));
        let groups = Arc::new(GroupCore::with_repo(
            Arc::new(MemoryGroupRepo::new()) as Arc<dyn GroupRepoPort>
        ));
        let relation = Arc::new(RelationCore::memory());
        let friends =
            Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let sessions: Arc<dyn SessionManagementService> = Arc::new(
            SessionManagementServiceImpl::new(
                Arc::new(MemorySessionRepo::new()),
                Arc::new(MemoryGroupRepo::new()) as _,
            ),
        );
        let authority = authority_hook_for(bot_repo.clone());
        let management = Arc::new(
            GroupManagement::new(
                groups.clone(),
                bots.clone(),
                friends.clone(),
                relation,
                bcs_group::GroupConfig::default(),
                sessions.clone(),
                Arc::new(NoopSystemMessageService),
            )
            .with_bot_authority(authority.clone())
            .for_v1_openapi(),
        );

        let service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends,
            sessions,
            management,
        )
        .with_bot_authority_hook(authority);
        Self {
            service,
            groups,
            bots,
            authority_repo: bot_repo,
        }
    }

    async fn add_bot(&self, bot_id: &str, owner: &str, visibility: &str) {
        self.bots
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.into()),
                    visibility: visibility.into(),
                    ..Default::default()
                },
            )
            .await
            .expect("register fixture bot");
        grant_owner_edge(&self.authority_repo, bot_id, owner).await;
    }

    async fn store_group(&self, group_id: &str, driver: &str, visibility: &str, extra: Vec<Participant>) {
        let mut participants =
            vec![Participant::bot(driver, ParticipantRole::Driver)];
        participants.extend(extra);
        let mut group = DomainGroup::new(group_id, driver, participants);
        group.group_strategy = GroupStrategy::Chat;
        group.visibility = visibility.into();
        self.groups.upsert(group).await.expect("store group");
    }
}

fn human_caller(id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("dev".into()),
        user: Some(AuthenticatedUserIdentity {
            id: id.into(),
            username: id.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn hybrid_caller(user: &str, bot: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("dev".into()),
        user: Some(AuthenticatedUserIdentity {
            id: user.into(),
            username: user.into(),
            display_name: None,
            full_name: None,
        }),
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot.into(),
            owner_id: user.into(),
            agent_code: String::new(),
            app_id: 0,
        }),
        app: None,
        access_key: None,
    }
}

// ── Rows 8/10: the view/list matrix ──────────────

#[tokio::test]
async fn list_view_rows_follow_the_current_edge() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice", "public").await;
    fx.store_group(
        "g1",
        "bot-a",
        "private",
        vec![],
    )
    .await;

    // A: the explicit view through the controlled
    // Bot passes.
    fx.viewed_list_ok("alice", Some("bot-a")).await;

    // X: someone else's bot view is denied.
    let x = fx
        .viewed_list("stranger", Some("bot-a"))
        .await;
    assert!(
        matches!(x, Err(ApplicationError::Forbidden(_))),
        "X is denied: {x:?}"
    );

    // A→B handover: A's leg is fallback-free
    // denied; B takes the view over.
    grant_owner_edge(&fx.authority_repo, "bot-a", "bob").await;
    let a = fx.viewed_list("alice", Some("bot-a")).await;
    assert!(
        matches!(a, Err(ApplicationError::Forbidden(_))),
        "A without edges is denied: {a:?}"
    );
    fx.viewed_list_ok("bob", Some("bot-a")).await;
}

impl Fixture {
    async fn viewed_list(
        &self,
        user: &str,
        view_bot: Option<&str>,
    ) -> Result<
        application_v1::Page<application_v1::GroupSummary>,
        ApplicationError,
    > {
        fx_group_service_list(
            &self.service,
            human_caller(user),
            view_bot,
        )
        .await
    }

    async fn viewed_list_ok(&self, user: &str, view_bot: Option<&str>) {
        self.viewed_list(user, view_bot)
            .await
            .unwrap_or_else(|error| {
                panic!("{user} list must pass: {error:?}")
            });
    }
}

async fn fx_group_service_list(
    service: &GroupServiceImpl,
    caller: AuthenticatedCaller,
    view_bot: Option<&str>,
) -> Result<
    bcs_service_api::application::v1::Page<bcs_service_api::application::v1::GroupSummary>,
    ApplicationError,
> {
    service.list_groups(ListGroups {
        caller,
        view_bot_id: view_bot.map(str::to_string),
        offset: 0,
        limit: 20,
        q: None,
        visibility: None,
        membership: MembershipFilter::All,
        kind: GroupKindFilter::All,
        strategy: None,
    })
    .await
}

// ── Row 10: hybrid credentials via the resolver ──

#[tokio::test]
async fn hybrid_credentials_resolve_via_the_live_edge() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice", "public").await;
    fx.store_group("g1", "bot-a", "private", vec![]).await;

    // Hybrid User+Bot credential: the resolver
    // checks the live edge BEFORE the Bot
    // becomes the principal.
    let detail = GroupService::get(
        &fx.service,
        GetGroup {
            caller: hybrid_caller("alice", "bot-a"),
            group_id: "g1".into(),
        },
    )
    .await
    .expect("hybrid caller resolves");


    // Revoke with the handover: A's hybrid
    // entry is denied, B's opens.
    grant_owner_edge(&fx.authority_repo, "bot-a", "bob").await;
    let denied = GroupService::get(
        &fx.service,
        GetGroup {
            caller: hybrid_caller("alice", "bot-a"),
            group_id: "g1".into(),
        },
    )
    .await;
    assert!(
        matches!(denied, Err(ApplicationError::Forbidden(_))),
        "A's hybrid entry is denied: {denied:?}"
    );
    GroupService::get(
        &fx.service,
        GetGroup {
            caller: hybrid_caller("bob", "bot-a"),
            group_id: "g1".into(),
        },
    )
    .await
    .expect("B's hybrid entry opens");

    // A pure-Bot caller (no Human side) keeps its
    // own contract — no authority read at all.
    let pure_bot = AuthenticatedCaller {
        tenant: Some("dev".into()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: "bot-a".into(),
            owner_id: "ghost".into(),
            agent_code: String::new(),
            app_id: 0,
        }),
        app: None,
        access_key: None,
    };
    GroupService::get(
        &fx.service,
        GetGroup {
            caller: pure_bot,
            group_id: "g1".into(),
        },
    )
    .await
    .expect("pure Bot keeps its contract regardless of edges");
}

// ── §8.1: the friendless private-Group sponsorship ──

#[tokio::test]
async fn create_sponsors_controlled_bot_without_friendship() {
    let fx = Fixture::new().await;
    fx.add_bot("driver", "alice", "public").await;
    fx.add_bot("sponsor", "alice", "protected").await;

    let build = |name: &str| CreateGroup {
        caller: human_caller(name),
        group: CreateGroupSpec::Collaboration(
            CreateCollaborationGroup {
                originator: None,
                name: Some("priv".into()),
                human_mention_notify_mode: Default::default(),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![application_v1::CreateParticipant {
                    actor_id: "sponsor".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: application_v1::CollaborationConfiguration::Chat(
                    application_v1::ChatConfiguration {
                        delivery_policy:
                            application_v1::GroupDeliveryPolicy {
                                bot_final_delivery:
                                    BotFinalDelivery::SendToDriver,
                            },
                    },
                ),
            },
        ),
    };

    // A protected Bot with NO friendship anywhere
    // enters A's private Group through the CURRENT
    // control edge (§8.1).
    fx.service
        .create(build("alice"))
        .await
        .expect("sponsorship creates the Group");

    // Withdraw via the handover: the sponsorship for
    // Alice dies while B's opens.
    grant_owner_edge(&fx.authority_repo, "sponsor", "bob").await;
    let denied = fx.service.create(build("alice")).await;
    assert!(
        matches!(denied, Err(ApplicationError::Forbidden(_))),
        "the sponsorship dies with the edge: {denied:?}"
    );
    // B is now the owner of the driver too... the driver
    // edge stays with alice; B only reaches via bot
    // relations: B as a caller with a hybrid credential
    // must pass through the resolver.
    grant_owner_edge(&fx.authority_repo, "driver", "bob").await;
    let mut b_create = build("bob");
    if let CreateGroupSpec::Collaboration(spec) = &mut b_create.group {
        spec.originator = Some("driver".into());
    }
    fx.service
        .create(b_create)
        .await
        .expect_err("B cannot re-create with A's driver (originator pickup)");
}

// ── Review fix (Important#3): a failing
// authority read is an internal error, never a
// deny-masked-forbidden.
#[tokio::test]
async fn authority_read_failure_is_internal_at_detail() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a", "alice", "private").await;
    fx.groups
        .upsert(DomainGroup::new(
            "g-fail",
            "bot-a",
            vec![Participant::bot(
                "bot-a",
                ParticipantRole::Driver,
            )],
        ))
        .await
        .expect("store group");

    // A SECOND facade instance wired with the
    // failing hook (the production shape stays
    // the same service otherwise).
    let failing = GroupServiceImpl::new(
        fx.groups.clone(),
        fx.bots.clone(),
        Arc::new(FriendCore::memory().with_relation(
            Arc::new(RelationCore::memory()),
        )),
        {
            let sessions: Arc<dyn SessionManagementService> =
                Arc::new(SessionManagementServiceImpl::new(
                    Arc::new(MemorySessionRepo::new()),
                    Arc::new(MemoryGroupRepo::new()) as _,
                ));
            sessions
        },
        Arc::new(
            GroupManagement::new(
                fx.groups.clone(),
                fx.bots.clone(),
                Arc::new(FriendCore::memory().with_relation(
                    Arc::new(RelationCore::memory()),
                )),
                Arc::new(RelationCore::memory()),
                bcs_group::GroupConfig::default(),
                {
                    let sessions: Arc<dyn SessionManagementService> =
                        Arc::new(SessionManagementServiceImpl::new(
                            Arc::new(MemorySessionRepo::new()),
                            Arc::new(MemoryGroupRepo::new()) as _,
                        ));
                    sessions
                },
                Arc::new(NoopSystemMessageService),
            )
            .with_bot_authority(failing_authority_hook())
            .for_v1_openapi(),
        ),
    )
    .with_bot_authority_hook(failing_authority_hook());

    let error = fx_get(&failing, "g-fail")
        .await
        .expect_err("authority failure must not masquerade");
    assert!(
        matches!(error, ApplicationError::Internal(_)),
        "expected internal: {error:?}"
    );
}

async fn fx_get(
    service: &GroupServiceImpl,
    group_id: &str,
) -> Result<application_v1::GroupDetail, ApplicationError> {
    use application_v1::GroupService as _;
    service
        .get(GetGroup {
            caller: human_caller("alice"),
            group_id: group_id.into(),
        })
        .await
}

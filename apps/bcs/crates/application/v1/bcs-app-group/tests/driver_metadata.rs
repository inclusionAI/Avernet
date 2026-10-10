use std::sync::Arc;

use bcs_app_group::{GroupServiceConfig, GroupServiceImpl};
use bcs_bot::BotCore;
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::{
    AuthenticatedCaller, AuthenticatedUserIdentity, GetGroup, GroupService, ListPublicGroups,
};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService, GroupKind, Participant,
    ParticipantRole,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

mod common;
use serde_json::{Value, json};

struct Fixture {
    service: GroupServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
}

impl Fixture {
    fn new() -> Self {
        let repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(repo.clone()));
        let authority_repo = Arc::new(bcs_bot_store::MemoryBotRepo::new());
        let bots = Arc::new(BotCore::with_repo(authority_repo.clone()));
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MemorySessionRepo::new()),
            repo,
        ));
        let management = Arc::new(
            GroupManagement::new(
                groups.clone(),
                bots.clone(),
                friends.clone(),
                relation.clone(),
                GroupConfig::default(),
                sessions.clone(),
                Arc::new(NoopSystemMessageService),
            )
            .for_v1_openapi(),
        );
        let service = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends,
            relation,
            sessions,
            management,
            common::repo_authority_hook(&authority_repo),
            GroupServiceConfig {
                relation_env: "dev".into(),
            },
        );
        Self {
            service,
            groups,
            bots,
        }
    }

    async fn register_driver(&self, owner: Option<&str>) {
        self.bots
            .register(
                "driver".into(),
                BotCapabilities {
                    name: Some("Driver Bot".into()),
                    ..Default::default()
                },
            )
            .await
            .expect("register driver");
        if let Some(owner) = owner {
            self.bots
                .save_created_by("driver", owner, true)
                .await
                .expect("save owner");
        }
    }

    async fn store_group(&self, driver: &str) {
        let mut group = Group::new(
            "group",
            driver,
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::human("human_reader", ParticipantRole::Observer),
            ],
        );
        group.visibility = "public".into();
        self.groups.upsert(group).await.expect("store group");
    }

    async fn detail(&self) -> Value {
        let caller = AuthenticatedCaller {
            tenant: None,
            user: Some(AuthenticatedUserIdentity {
                id: "reader".into(),
                username: "reader".into(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        };
        serde_json::to_value(
            self.service
                .get(GetGroup {
                    caller,
                    group_id: "group".into(),
                })
                .await
                .expect("get group"),
        )
        .expect("serialize detail")
    }

    async fn summary(&self) -> Value {
        let page = self
            .service
            .list_public_groups(ListPublicGroups {
                offset: 0,
                limit: 20,
                q: None,
                strategy: None,
            })
            .await
            .expect("list groups");
        assert_eq!(page.total, 1);
        serde_json::to_value(&page.items[0]).expect("serialize summary")
    }
}

#[tokio::test]
async fn public_summary_resolves_driver_name_from_backfilled_participants() {
    let fixture = Fixture::new();
    fixture.register_driver(None).await;
    fixture.store_group("driver").await;
    assert_eq!(fixture.summary().await["driver_bot_name"], "Driver Bot");
}

#[tokio::test]
async fn public_summary_preserves_existing_participant_name() {
    let fixture = Fixture::new();
    fixture.register_driver(None).await;
    let mut participant = Participant::bot("driver", ParticipantRole::Driver);
    participant.bot_name = Some("Saved Display Name".into());
    let mut group = Group::new("group", "driver", vec![participant]);
    group.visibility = "public".into();
    fixture.groups.upsert(group).await.expect("store group");
    assert_eq!(
        fixture.summary().await["driver_bot_name"],
        "Saved Display Name"
    );
}

#[tokio::test]
async fn public_summary_emits_null_when_driver_cannot_be_resolved() {
    for driver in ["", "unknown", "driver"] {
        let fixture = Fixture::new();
        fixture.store_group(driver).await;
        assert_eq!(
            fixture.summary().await.get("driver_bot_name"),
            Some(&Value::Null)
        );
    }
}

#[tokio::test]
async fn detail_resolves_driver_owner_not_requesting_user() {
    let fixture = Fixture::new();
    fixture.register_driver(Some("owner")).await;
    fixture
        .bots
        .ensure_human_actor("owner", "Owner Name")
        .await
        .expect("owner");
    fixture.store_group("driver").await;
    let detail = fixture.detail().await;
    assert_eq!(detail["driver_bot_owner"], "human_owner");
    assert_eq!(detail["driver_bot_owner_name"], "Owner Name");
}

#[tokio::test]
async fn detail_keeps_owner_id_when_human_registration_is_missing() {
    let fixture = Fixture::new();
    fixture.register_driver(Some("owner")).await;
    fixture.store_group("driver").await;
    let detail = fixture.detail().await;
    assert_eq!(detail["driver_bot_owner"], "human_owner");
    assert_eq!(detail.get("driver_bot_owner_name"), Some(&Value::Null));
}

#[tokio::test]
async fn detail_preserves_empty_human_display_name_like_legacy() {
    let fixture = Fixture::new();
    fixture.register_driver(Some("owner")).await;
    fixture
        .bots
        .ensure_human_actor("owner", "")
        .await
        .expect("owner");
    fixture.store_group("driver").await;
    let detail = fixture.detail().await;
    assert_eq!(detail["driver_bot_owner"], "human_owner");
    assert_eq!(detail["driver_bot_owner_name"], "");
}

#[tokio::test]
async fn detail_emits_null_owner_for_missing_or_empty_created_by() {
    for owner in [None, Some("")] {
        let fixture = Fixture::new();
        fixture.register_driver(owner).await;
        fixture.store_group("driver").await;
        let detail = fixture.detail().await;
        assert_eq!(detail.get("driver_bot_owner"), Some(&Value::Null));
        assert_eq!(detail.get("driver_bot_owner_name"), Some(&Value::Null));
    }
}

#[tokio::test]
async fn detail_emits_null_owner_for_missing_driver() {
    for driver in ["", "unknown"] {
        let fixture = Fixture::new();
        fixture.store_group(driver).await;
        let detail = fixture.detail().await;
        assert_eq!(detail.get("driver_bot_owner"), Some(&Value::Null));
        assert_eq!(detail.get("driver_bot_owner_name"), Some(&Value::Null));
    }
}

#[tokio::test]
async fn dm_detail_and_summary_do_not_gain_driver_metadata() {
    let fixture = Fixture::new();
    fixture.register_driver(Some("owner")).await;
    let mut group = Group::new(
        "group",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::human("human_reader", ParticipantRole::Observer),
        ],
    );
    group.group_kind = GroupKind::Dm;
    group.visibility = "public".into();
    fixture
        .groups
        .upsert(group)
        .await
        .expect("store DM fixture");
    for value in [fixture.detail().await, fixture.summary().await] {
        assert_eq!(value["kind"], json!("dm"));
        for key in [
            "driver_bot_name",
            "driver_bot_owner",
            "driver_bot_owner_name",
        ] {
            assert!(value.get(key).is_none(), "DM must omit {key}");
        }
    }
}

//! F5: the TC-origin delete guard is INTRINSIC to the Bot record.
//!
//! The legacy TeamClaw delete restriction classifies a Bot by its
//! own persisted `created_by` against the `<prefix>:<staff_no>`
//! owner-suffix convention — never by the requester's staff id.
//! Before this cut, a current MANAGER (or a new owner after an
//! ownership transfer) did not suffix-match the id and could
//! retire a TC-origin Bot through the ordinary path. Permission
//! still comes from the CURRENT authority (`require_manage`);
//! origin classification comes from the record.

use std::sync::Arc;

use bcs_bot::{Bot, BotCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::{
    BotCapabilities, BotLeaveCommand, BotManagementService, BotRegistryCoreService,
    BotUseCaseError,
};
use tempfile::TempDir;

struct Fixture {
    registry: Arc<BotCore>,
    authority: Arc<bcs_test_support::LazySeedAuthorityHook>,
    _data_dir: TempDir,
}

impl Fixture {
    fn new() -> Self {
        let data_dir = tempfile::tempdir().expect("temp data dir");
        // ONE shared memory store: the authority hook sees the
        // edges it seeds AND the registry's store-level retirement
        // re-verifies the same operator edge in its atomic span.
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(
            data_dir.path().to_path_buf(),
        ));
        let chain = bcs_test_support::lazy_authority_chain_with_repo(bot_repo.clone());
        let registry = Arc::new(BotCore::with_repo(bot_repo));
        Self {
            registry,
            authority: chain.hook,
            _data_dir: data_dir,
        }
    }

    fn service(&self) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        Bot::new(registry)
            .with_bot_core(self.registry.clone())
            .with_bot_authority(self.authority.clone())
    }

    /// Persist a Bot with the historical origin marker
    /// (`created_by`) intact; the CURRENT edges are seeded
    /// separately per scenario.
    async fn register(&self, bot_id: &str, created_by: Option<&str>) {
        self.registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.into()),
                    visibility: "public".into(),
                    ..Default::default()
                },
            )
            .await
            .expect("register bot");
        if let Some(staff_no) = created_by {
            self.registry
                .save_created_by(bot_id, staff_no, true)
                .await
                .expect("save creator");
        }
    }
}

fn delete_as(_service: &Bot, staff_no: &str, bot_id: &str) -> BotLeaveCommand {
    BotLeaveCommand {
        caller_actor_id: Some(format!("human_{staff_no}")),
        human_actor_id: Some(format!("human_{staff_no}")),
        bot_id: bot_id.to_string(),
    }
}

#[tokio::test]
async fn tc_origin_bot_rejects_every_current_owner_and_manager_delete() {
    let fixture = Fixture::new();
    let service = fixture.service();
    fixture.register("teamclaw-bot:alice", Some("alice")).await;
    fixture.authority.seed_owner("teamclaw-bot:alice", "alice");
    fixture.authority.seed_manager("teamclaw-bot:alice", "bob");

    let owner = service
        .leave_bot(delete_as(&service, "alice", "teamclaw-bot:alice"))
        .await
        .expect_err("the current owner can never delete a TC-origin Bot here");
    assert!(matches!(
        owner,
        BotUseCaseError::Forbidden(message) if message.contains("TC")
    ));

    // F5 (the reported bypass): the requester's staff id used to
    // classify the Bot — a manager suffix-mismatched and fell
    // through to the ordinary retire. The intrinsic guard denies.
    let manager = service
        .leave_bot(delete_as(&service, "bob", "teamclaw-bot:alice"))
        .await
        .expect_err("the current manager can never delete a TC-origin Bot here");
    assert!(matches!(
        manager,
        BotUseCaseError::Forbidden(message) if message.contains("TC")
    ));
    assert!(
        fixture.registry.get("teamclaw-bot:alice").await.is_some(),
        "the denied attempts leave Bot and authority state untouched"
    );
}

#[tokio::test]
async fn tc_origin_survives_ownership_transfer() {
    let fixture = Fixture::new();
    let service = fixture.service();
    fixture.register("teamclaw-bot:alice", Some("alice")).await;
    fixture.authority.seed_owner("teamclaw-bot:alice", "alice");

    // Handover: a new CURRENT owner edge (and a manager) — the
    // persisted origin marker does not move, so neither can use
    // the ordinary delete.
    fixture.authority.seed_owner("teamclaw-bot:alice", "carol");
    fixture.authority.seed_manager("teamclaw-bot:alice", "dave");
    for (staff, role) in [("carol", "new owner"), ("dave", "manager")] {
        let denied = service
            .leave_bot(delete_as(&service, staff, "teamclaw-bot:alice"))
            .await
            .expect_err(&format!("the {role} can never delete a TC-origin Bot"));
        assert!(matches!(
            denied,
            BotUseCaseError::Forbidden(message) if message.contains("TC")
        ));
    }
    assert!(fixture.registry.get("teamclaw-bot:alice").await.is_some());
}

#[tokio::test]
async fn ordinary_bots_keep_the_ordinary_delete_matrix() {
    let fixture = Fixture::new();
    let service = fixture.service();

    // Plain bot: current owner deletes, current manager deletes,
    // a stranger is denied by the authority gate itself.
    fixture.register("plain-bot", Some("alice")).await;
    fixture.authority.seed_owner("plain-bot", "alice");
    fixture.authority.seed_manager("plain-bot", "bob");

    let stranger = service
        .leave_bot(delete_as(&service, "eve", "plain-bot"))
        .await
        .expect_err("a stranger has no CURRENT manage edge");
    assert!(matches!(
        stranger,
        BotUseCaseError::Forbidden(message) if message.contains("owner or manager")
    ));

    let manager_left = service
        .leave_bot(delete_as(&service, "bob", "plain-bot"))
        .await
        .expect("the current manager keeps the ordinary delete (§8 删除)");
    assert!(manager_left.left);

    fixture.register("owner-bot", Some("alice")).await;
    fixture.authority.seed_owner("owner-bot", "alice");
    let owner_left = service
        .leave_bot(delete_as(&service, "alice", "owner-bot"))
        .await
        .expect("the current owner keeps the ordinary delete");
    assert!(owner_left.left);
}

#[tokio::test]
async fn suffix_mismatch_and_missing_creator_are_not_tc_origin() {
    let fixture = Fixture::new();
    let service = fixture.service();

    // A TC-SHAPED id whose persisted creator does NOT match the
    // suffix is not a TC-origin Bot by the documented rule.
    fixture.register("teamclaw-bot:alice", Some("bob")).await;
    fixture.authority.seed_owner("teamclaw-bot:alice", "bob");
    let left = service
        .leave_bot(delete_as(&service, "bob", "teamclaw-bot:alice"))
        .await
        .expect("suffix/creator mismatch takes the ordinary path");
    assert!(left.left);

    // A MISSING creator cannot prove origin by this rule (such
    // legacy rows need their own data investigation); the guard
    // never invents one.
    fixture.register("teamclaw-bot:orphan", None).await;
    fixture.authority.seed_owner("teamclaw-bot:orphan", "alice");
    let left = service
        .leave_bot(delete_as(&service, "alice", "teamclaw-bot:orphan"))
        .await
        .expect("a creator-less legacy record falls to the ordinary path");
    assert!(left.left);
}

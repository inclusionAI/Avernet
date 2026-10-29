//! `BotAuthorityCoreService` conformance harness
//! (bot-authority spec §3.2/§5/§6 read+write semantics).
//!
//! The Core is the composed read/write authority surface the
//! application facades and the runtime hook call. This harness
//! drives any `&dyn BotAuthorityCoreService` built over the SAME
//! [`BotAuthorityContractFixture`] seeding channel the
//! repo-port harness uses, so the core cannot drift from the
//! store facts it is composed over: strict role mapping,
//! boolean/deny helpers, ownership/manager projections,
//! uninitialized/corrupt/deleted state, and the manager-write
//! and transfer round trips through the core itself.
//!
//! Conformance entries:
//! - `bcs-edge-permission-store/tests/conformance_bot_authority.rs`
//!   (`BotAuthorityCoreServiceImpl` over `DbBotAuthorityStore`).

#![allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]

use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::types::bot_authority::{
    human_actor_id, AuthorityMutation, BotAccessRelation, BotRoleQuery,
    ManagerEdit, ManagerEditAction, ManagerListQuery, ManagerSync,
    OwnershipSync, TransferAction, TransferCommand, TransferKey,
};

use super::bot_authority_contract::BotAuthorityContractFixture;

const OWNER: &str = "core_owner";
const MANAGER: &str = "core_manager";
const STRANGER: &str = "core_stranger";
const NEW_MANAGER: &str = "core_new_manager";
const TARGET: &str = "core_target";

/// Real core-level semantics over the fixture's committed
/// state (spec §3.2/§5/§6).
pub async fn bot_authority_core_service_contract_tests(
    core: &dyn BotAuthorityCoreService,
    fixture: &(impl BotAuthorityContractFixture + ?Sized),
    env: &str,
) {
    for user in [OWNER, MANAGER, STRANGER, NEW_MANAGER, TARGET] {
        fixture.seed_human(env, user).await;
    }

    // A live, initialized Bot with the complete owner state.
    fixture.seed_bot(env, "bot_core_live", 1).await;
    fixture
        .grant_edge(
            env,
            "bot_core_live",
            &human_actor_id(OWNER),
            BotAccessRelation::Owner,
        )
        .await;
    fixture
        .grant_edge(
            env,
            "bot_core_live",
            &human_actor_id(MANAGER),
            BotAccessRelation::Manager,
        )
        .await;

    // Strict role mapping (spec §3.2): one query per relation,
    // strict-complete owner state.
    let role_owner = core.role(OWNER, "bot_core_live").await.expect("owner role");
    let role_manager = core.role(MANAGER, "bot_core_live").await.expect("manager role");
    let role_stranger = core.role(STRANGER, "bot_core_live").await.expect("stranger role");
    assert_eq!(role_owner, Some(BotAccessRelation::Owner));
    assert_eq!(role_manager, Some(BotAccessRelation::Manager));
    assert_eq!(role_stranger, None);

    // Batch form: same length/order, no per-id re-query collapse.
    let batched = core
        .roles_for(&[
            BotRoleQuery { user_id: OWNER.into(), bot_id: "bot_core_live".into() },
            BotRoleQuery { user_id: MANAGER.into(), bot_id: "bot_core_live".into() },
            BotRoleQuery { user_id: STRANGER.into(), bot_id: "bot_core_live".into() },
        ])
        .await
        .expect("roles_for");
    assert_eq!(
        batched,
        vec![
            Some(BotAccessRelation::Owner),
            Some(BotAccessRelation::Manager),
            None,
        ],
        "roles_for matches role per query"
    );

    // Boolean/deny helpers propagate the same verdicts and keep
    // errors strict.
    assert!(core.can_manage(OWNER, "bot_core_live").await.expect("owner manage"));
    assert!(core.can_manage(MANAGER, "bot_core_live").await.expect("manager manage"));
    assert!(!core.can_manage(STRANGER, "bot_core_live").await.expect("stranger denied"));
    core.require_owner(OWNER, "bot_core_live").await.expect("owner passes owner gate");
    assert!(
        core.require_owner(MANAGER, "bot_core_live").await.is_err(),
        "manager must fail the owner gate"
    );
    core.require_manage(MANAGER, "bot_core_live").await.expect("manager passes manage gate");
    assert!(
        core.require_manage(STRANGER, "bot_core_live").await.is_err(),
        "stranger must fail the manage gate"
    );

    // Ownership + paged manager projection.
    let ownership = core.ownership("bot_core_live").await.expect("ownership view");
    assert_eq!(ownership.owner_user_id, OWNER);
    assert_eq!(ownership.owner_actor_id, human_actor_id(OWNER));
    let page = core
        .managers(ManagerListQuery {
            bot_id: "bot_core_live".into(),
            offset: 0,
            limit: 50,
        })
        .await
        .expect("manager page");
    assert_eq!(page.owner.owner_user_id, OWNER);
    assert_eq!(page.items, vec![MANAGER.to_string()]);

    // Uninitialized (version 0) Bot denies completely — this is
    // a deny, not an uninitialized-owner synthesis.
    fixture.seed_bot(env, "bot_core_v0", 0).await;
    assert_eq!(
        core.role(OWNER, "bot_core_v0").await.expect("v0 role"),
        None,
        "version 0 denies"
    );
    assert!(core.ownership("bot_core_v0").await.is_err(), "v0 ownership reads are Err");

    // A deleted Human cannot hold a role once its edge is
    // gone (the same path Human deletion uses: edges
    // revoked, spec §3.2.7) — a bare identity removal with
    // the edge intact does NOT silently deny.
    fixture
        .revoke_edge(env, "bot_core_live", &human_actor_id(MANAGER), BotAccessRelation::Manager)
        .await;
    fixture.remove_human(env, MANAGER).await;
    assert_eq!(
        core.role(MANAGER, "bot_core_live").await.expect("deleted-human role"),
        None,
        "deleted Human with revoked edge denies"
    );
    fixture.seed_human(env, MANAGER).await;

    // Corrupt owner state (duplicate approved owner) is an Err
    // that must never be swallowed into a deny.
    fixture.seed_bot(env, "bot_core_corrupt", 1).await;
    fixture
        .grant_edge(
            env,
            "bot_core_corrupt",
            &human_actor_id(OWNER),
            BotAccessRelation::Owner,
        )
        .await;
    fixture
        .seed_corrupt_duplicate_owner(
            env,
            "bot_core_corrupt",
            &human_actor_id(MANAGER),
        )
        .await;
    assert!(
        core.role(MANAGER, "bot_core_corrupt").await.is_err(),
        "duplicated owner state must be Err, not a deny"
    );
    assert!(core.ownership("bot_core_corrupt").await.is_err());

    // Manager write round trip THROUGH the core (Task-2 path),
    // reflected in the strict reads.
    let grant = ManagerEdit {
        bot_id: "bot_core_live".into(),
        user_id: NEW_MANAGER.into(),
        action: ManagerEditAction::Grant,
        mutation: mutation("core-grant"),
    };
    core.edit_manager(grant).await.expect("core manager grant");
    assert_eq!(
        core.role(NEW_MANAGER, "bot_core_live").await.expect("new manager role"),
        Some(BotAccessRelation::Manager)
    );
    let revoke = ManagerEdit {
        bot_id: "bot_core_live".into(),
        user_id: NEW_MANAGER.into(),
        action: ManagerEditAction::Revoke,
        mutation: mutation("core-revoke"),
    };
    let change = core.edit_manager(revoke).await.expect("core manager revoke");
    assert_eq!(change.removed, 1, "the revoke removed one manager");
    assert_eq!(
        core.role(NEW_MANAGER, "bot_core_live").await.expect("revoked role"),
        None
    );

    // Sync keeps the owner and replaces the set (the owner is
    // never a sync operand).
    let synced = core
        .sync_managers(ManagerSync {
            bot_id: "bot_core_live".into(),
            user_ids: vec![TARGET.to_string()],
            mutation: service_mutation("core-sync"),
        })
        .await
        .expect("core sync");
    assert_eq!(synced.added, 1);
    let page = core
        .managers(ManagerListQuery {
            bot_id: "bot_core_live".into(),
            offset: 0,
            limit: 50,
        })
        .await
        .expect("page after sync");
    assert_eq!(page.items, vec![TARGET.to_string()]);

    // Ownership sync through the core (设计 S01/S06 core 面):
    // Service-only, target shape gated, owner swapped + fully
    // removed (no demotion).
    let shape_rejected = core
        .sync_ownership(OwnershipSync {
            bot_id: "bot_core_live".into(),
            owner_user_id: "  ".into(),
            mutation: service_mutation("core-ownsync-shape"),
        })
        .await
        .expect_err("blank target is a shape rejection");
    assert!(shape_rejected.to_string().contains("invalid_request"), "{shape_rejected}");
    let actor_denied = core
        .sync_ownership(OwnershipSync {
            bot_id: "bot_core_live".into(),
            owner_user_id: STRANGER.into(),
            mutation: mutation("core-ownsync-human"),
        })
        .await
        .expect_err("Human mutation is denied at the core");
    assert!(actor_denied.to_string().contains("bot_authority_operator_forbidden"), "{actor_denied}");
    let swapped = core
        .sync_ownership(OwnershipSync {
            bot_id: "bot_core_live".into(),
            owner_user_id: NEW_MANAGER.into(),
            mutation: service_mutation("core-ownsync"),
        })
        .await
        .expect("core ownership sync");
    assert!(swapped.changed);
    assert_eq!(
        core.ownership("bot_core_live").await.expect("ownership").owner_user_id,
        NEW_MANAGER
    );
    assert_eq!(
        core.role(OWNER, "bot_core_live").await.expect("old owner role"),
        None,
        "the old owner is NOT demoted to manager by sync"
    );
    // Restore OWNER as owner for the transfer round trip that
    // follows (sync again).
    core.sync_ownership(OwnershipSync {
        bot_id: "bot_core_live".into(),
        owner_user_id: OWNER.into(),
        mutation: service_mutation("core-ownsync-restore"),
    })
    .await
    .expect("restore owner");

    // Transfer round trip through the core (Task-3 path):
    // request → read → accept moves the owner atomically;
    // the old owner is restored as a plain manager.
    let key = TransferKey {
        bot_id: "bot_core_live".into(),
        target_owner_actor_id: human_actor_id(TARGET),
    };
    core.change_transfer(TransferCommand {
        key: key.clone(),
        action: TransferAction::RequestOrRefresh,
        mutation: mutation("core-transfer"),
    })
    .await
    .expect("core transfer request");
    let view = core.transfer(&key).await.expect("transfer read");
    assert!(view.is_some(), "pending transfer readable through the core");
    core.change_transfer(TransferCommand {
        key: key.clone(),
        action: TransferAction::Accept,
        mutation: from_user("core-accept", TARGET),
    })
    .await
    .expect("core accept");
    let ownership = core.ownership("bot_core_live").await.expect("ownership after accept");
    assert_eq!(ownership.owner_user_id, TARGET, "accept moved the owner");
    let page = core
        .managers(ManagerListQuery {
            bot_id: "bot_core_live".into(),
            offset: 0,
            limit: 50,
        })
        .await
        .expect("page after accept");
    assert!(
        page.items.contains(&OWNER.to_string()),
        "the ex-owner is restored as a plain manager"
    );
}

/// Sync is trusted-service only (the actor-kind
/// policy); the harness syncs as a trusted App.
fn service_mutation(id: &str) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: format!("{id}-{}", std::process::id()),
        actor: bcs_service_api::types::bot_authority::AuthorityActor::Service {
            app_id: 7,
        },
    }
}

fn mutation(id: &str) -> AuthorityMutation {
    from_user(id, OWNER)
}

fn from_user(id: &str, user: &str) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: format!("{id}-{}", std::process::id()),
        actor: bcs_service_api::types::bot_authority::AuthorityActor::Human {
            user_id: user.to_string(),
        },
    }
}

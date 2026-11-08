//! The memory projection's bot-authority contract
//! (`BotAuthorityRepoPort` on `MemoryBotRepo`,
//! bot-authority plan Task 1 reads + Task 2 manager
//! writes + Task 3 transfer writes), driven through the
//! SAME shared harness scenarios as the SQL authority
//! Store so the two stores cannot drift.
//!
//! The fixture drives the SAME shared atomic state
//! boundary (`MemoryBotState`'s authority facet) the
//! production facets read and write, per spec §7, via
//! `MemoryBotRepo::authority_state` — not through any
//! test-specific write path. The fixture body lives in
//! `memory_authority_fixture.rs` (shared with the Rule 25
//! conformance entry).

#![allow(clippy::expect_used, clippy::unwrap_used)]

#[path = "memory_authority_fixture.rs"]
mod memory_authority_fixture;

use std::sync::Arc;

use bcs_bot_store::MemoryBotRepo;
use bcs_test_support::contract::bot_authority_contract::run_bot_authority_contract;
use bcs_test_support::contract::bot_transfer_contract::run_bot_transfer_contract;

use memory_authority_fixture::{MemoryAuthorityFixture, target_env};

use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};

/// The FULL shared harness (Task-1 strict reads + Task-2
/// manager writes + Task-3 transfer writes) must produce
/// the same legal results and errors as the SQL authority
/// Store.
#[tokio::test]
async fn memory_bot_repo_bot_authority_contract() {
    let env = target_env().await;
    let other = format!("{env}-other");
    let fixture = MemoryAuthorityFixture {
        repo: Arc::new(MemoryBotRepo::new()),
    };
    run_bot_authority_contract(&fixture, &env, &other).await;
}

/// The FULL shared transfer harness (Task 3) on the memory
/// projection: identical outcomes to the SQL store.
#[tokio::test]
async fn memory_bot_repo_transfer_contract() {
    let env = target_env().await;
    let fixture = MemoryAuthorityFixture {
        repo: Arc::new(MemoryBotRepo::new()),
    };
    run_bot_transfer_contract(&fixture, &env).await;
}

/// Migration-window shim (memory arm): a version-0 legacy Bot
/// answers role reads with its legacy `created_by` fact while the
/// flag is ON; flag OFF and every version > 0 state keep the strict
/// semantics. Both driver arms are pinned separately because they
/// share only the contract's scenario inventory, not this shim.
#[tokio::test]
async fn legacy_created_by_fallback_resolves_only_the_legacy_creator() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    use bcs_service_api::types::bot_authority::{BotAccessRelation, BotRoleQuery};

    repo.register_with_owner_and_token(
        "legacy-bot".to_string(),
        bcs_service_api::BotCapabilities::default(),
        "85020",
        "legacy-token",
    )
    .await
    .expect("legacy bot row");

    // Flag OFF (store default): the strict read denies exactly as
    // before, even for the legacy creator.
    assert_eq!(
        repo.role("human_85020", "legacy-bot").await.expect("role off"),
        None,
    );

    repo.set_legacy_created_by_fallback(true);
    assert_eq!(
        repo.role("human_85020", "legacy-bot").await.expect("role on"),
        Some(BotAccessRelation::Owner),
    );
    assert_eq!(
        repo.roles_for(&[BotRoleQuery {
            user_id: "human_85020".into(),
            bot_id: "legacy-bot".into(),
        }])
        .await
        .expect("roles_for on"),
        vec![Some(BotAccessRelation::Owner)],
    );
    // Another Human (and arbitrary non-prefixed actor spellings) deny.
    assert_eq!(repo.role("human_99999", "legacy-bot").await.expect("other human"), None);
    assert_eq!(repo.role("85020", "legacy-bot").await.expect("no prefix"), None);

    // Flag back OFF revives the strict deny.
    repo.set_legacy_created_by_fallback(false);
    assert_eq!(
        repo.role("human_85020", "legacy-bot").await.expect("role strict again"),
        None,
    );
}

/// The governance initialize composite refuses anything but the
/// stored creator record (byte-for-byte) as the requested owner —
/// memory arm mirroring the SQL lifecycle scenario
/// `governance_initialize_refuses_creator_mismatch`.
#[tokio::test]
async fn initialize_refuses_creator_mismatch_memory() {
    use bcs_service_api::port::repo::BotAuthorityRepoPort;
    use bcs_service_api::types::bot_authority::{AuthorityActor, AuthorityMutation};

    let temp = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    repo.ensure_human_actor("11111111", "11111111")
        .await
        .expect("ensure owner actor");
    repo.register_with_owner_and_token(
        "bot_mismatch".to_string(),
        bcs_service_api::BotCapabilities::default(),
        "11111111",
        "tok",
    )
    .await
    .expect("legacy bot");

    let error = repo
        .initialize_ownership(
            "bot_mismatch",
            "22222222",
            AuthorityMutation {
                mutation_id: "mismatch".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "22222222".to_string(),
                },
            },
        )
        .await
        .expect_err("mismatching operator must be refused");
    assert!(
        matches!(error, bcs_service_api::ServiceError::Conflict(_)),
        "Conflict expected, got {error:?}"
    );
    let state = repo.authority_state().read().await;
    assert!(
        !state.ownership_versions.keys().any(|(_, bot)| bot == "bot_mismatch"),
        "mismatch must not move the version"
    );
    drop(state);
    assert_eq!(
        BotAuthorityRepoPort::role(&repo, "human_22222222", "bot_mismatch")
            .await
            .expect("role"),
        None,
        "no owner edge minted by the refusal"
    );

    // The recorded creator matches → the initialize succeeds.
    let view = repo
        .initialize_ownership(
            "bot_mismatch",
            "11111111",
            AuthorityMutation {
                mutation_id: "matched".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "11111111".to_string(),
                },
            },
        )
        .await
        .expect("matching creator initializes");
    assert_eq!(view.owner_user_id, "11111111");

    // A creatorless runtime Bot carries no fact to contradict and
    // stays initializable (governance classification lives upstream).
    BotRepoPort::register(&repo, "creatorless".to_string(), bcs_service_api::BotCapabilities::default())
        .await
        .expect("creatorless bot");
    repo.initialize_ownership(
        "creatorless",
        "22222222",
        AuthorityMutation {
            mutation_id: "creatorless".to_string(),
            actor: AuthorityActor::Human {
                user_id: "22222222".to_string(),
            },
        },
    )
    .await
    .expect("creatorless bot initializes");
}

/// A Human ACTOR row is never an ownership target — memory arm
/// mirroring `governance_initialize_never_targets_a_human_actor_row`.
#[tokio::test]
async fn initialize_never_targets_a_human_actor_row_memory() {
    use bcs_service_api::port::repo::BotAuthorityRepoPort;
    use bcs_service_api::types::bot_authority::{AuthorityActor, AuthorityMutation};

    let temp = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    repo.ensure_human_actor("22222222", "22222222")
        .await
        .expect("ensure human actor row");

    let error = repo
        .initialize_ownership(
            "human_22222222",
            "22222222",
            AuthorityMutation {
                mutation_id: "human-row".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "22222222".to_string(),
                },
            },
        )
        .await
        .expect_err("a Human actor row must never be initialized");
    assert!(
        matches!(error, bcs_service_api::ServiceError::Conflict(_)),
        "Conflict expected, got {error:?}"
    );
    assert_eq!(
        BotAuthorityRepoPort::role(&repo, "human_22222222", "human_22222222")
            .await
            .expect("role"),
        None,
        "no human→human owner edge"
    );
}

/// The composite may CREATE the owner's Human actor row,
/// but a pre-existing tombstone or mis-kinded row must
/// refuse the whole registration — no Bot row, no owner
/// edge, no resurrection of the dead Human.
#[tokio::test]
async fn memory_registration_refuses_tombstoned_or_miskinded_owner() {
    use bcs_service_api::types::bot_authority::{
        AuthorityActor, AuthorityMutation, OwnershipInitialization,
    };

    let temp = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    repo.ensure_human_actor("33333333", "Dead Owner")
        .await
        .expect("ensure human");
    assert!(
        BotRepoPort::soft_delete(&repo, "human_33333333").await,
        "tombstone the owner Human"
    );

    let error = repo
        .create_registration_if_absent_with_ownership(
            "new-bot".to_string(),
            bcs_service_api::BotCapabilities {
                name: Some("New Bot".to_string()),
                ..Default::default()
            },
            "33333333",
            "new-token",
            OwnershipInitialization {
                owner_user_id: "33333333".to_string(),
                mutation: AuthorityMutation {
                    mutation_id: "review-registration".to_string(),
                    actor: AuthorityActor::Human {
                        user_id: "33333333".to_string(),
                    },
                },
            },
        )
        .await
        .expect_err("tombstoned owner must refuse");
    assert!(
        matches!(error, bcs_service_api::ServiceError::Conflict(_)),
        "Conflict expected, got {error:?}"
    );
    assert!(
        BotRepoPort::try_get(&repo, "new-bot")
            .await
            .expect("try_get bot")
            .is_none(),
        "no Bot row after the refusal"
    );
    assert_eq!(
        BotAuthorityRepoPort::role(&repo, "human_33333333", "new-bot")
            .await
            .expect("role"),
        None,
        "no owner edge after the refusal"
    );
    assert!(
        BotRepoPort::try_get(&repo, "human_33333333")
            .await
            .expect("try_get human")
            .is_none(),
        "tombstoned Human was not resurrected by the refused registration"
    );
}

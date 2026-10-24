//! Memory-store atomicity pins (bot-authority spec §5.2/§7):
//! Human/Bot survival facts and the authority writes they gate
//! share ONE atomic boundary — the memory twin of the SQL
//! store's in-transaction guards.

use std::sync::Arc;

use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::memory_authority::{MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus};
use bcs_service_api::BotCapabilities;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, ManagerSync, human_actor_id,
};

fn mutation(id: &str, actor: AuthorityActor) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: id.to_string(),
        actor,
    }
}

fn human_operator(user: &str) -> AuthorityActor {
    AuthorityActor::Human { user_id: user.to_string() }
}

/// Seed `bot` initialized with `owner` as the approved owner and
/// ensure the owner Human actor (spec §3.1 production shape).
async fn seed_owned_bot(repo: &MemoryBotRepo, bot: &str, owner: &str) {
    repo.register(bot.to_string(), BotCapabilities::default())
        .await
        .expect("register fixture bot");
    repo.ensure_human_actor(owner, owner)
        .await
        .expect("ensure owner actor");
    let env = bcs_config::resolve_env_str();
    let mut state = repo.authority_state().write().await;
    state
        .ownership_versions
        .insert((env.clone(), bot.to_string()), 1);
    state.edges.insert(
        (
            env,
            bot.to_string(),
            human_actor_id(owner),
            MemoryAuthorityEdgeKind::Owner,
        ),
        (MemoryAuthorityEdgeStatus::Approved, 0),
    );
}

async fn grant_manager(repo: &MemoryBotRepo, bot: &str, manager: &str) {
    repo.ensure_human_actor(manager, manager)
        .await
        .expect("ensure manager actor");
    repo.authority_state().write().await.edges.insert(
        (
            bcs_config::resolve_env_str(),
            bot.to_string(),
            human_actor_id(manager),
            MemoryAuthorityEdgeKind::Manager,
        ),
        (MemoryAuthorityEdgeStatus::Approved, 0),
    );
}

/// spec §7: the operator's edge is re-verified inside the
/// retirement's atomic span — a revoked manager can neither
/// tombstone nor audit, and the already-tombstoned retry stays
/// idempotent (never a second audit).
#[tokio::test]
async fn memory_retirement_rechecks_the_operator_edge_in_transaction() {
    let repo = Arc::new(MemoryBotRepo::new());
    seed_owned_bot(&repo, "bot_mem_r1", "own").await;
    grant_manager(&repo, "bot_mem_r1", "alice").await;

    // Revoke alice BEFORE the retirement command: any stale
    // application-time check would still have passed.
    repo.authority_state().write().await.edges.insert(
        (
            bcs_config::resolve_env_str(),
            "bot_mem_r1".to_string(),
            human_actor_id("alice"),
            MemoryAuthorityEdgeKind::Manager,
        ),
        (MemoryAuthorityEdgeStatus::Revoked, 0),
    );

    let outcome = repo
        .retire_with_authority(
            "bot_mem_r1",
            mutation("retire_a1", human_operator("alice")),
        )
        .await;
    assert!(
        matches!(
            outcome,
            Err(bcs_service_api::ServiceError::Forbidden(_))
        ),
        "a revoked operator gets 403, got {outcome:?}"
    );
    assert!(
        repo.get("bot_mem_r1").await.is_some(),
        "a revoked operator must never tombstone"
    );

    let changed = repo
        .retire_with_authority(
            "bot_mem_r1",
            mutation("retire_a2", human_operator("own")),
        )
        .await
        .expect("owner retirement");
    assert!(changed, "owner transition reports changed");

    // Stranger retry on the tombstoned Bot: the idempotent
    // no-op path never demands the operator's edge.
    let again = repo
        .retire_with_authority(
            "bot_mem_r1",
            mutation("retire_a3", human_operator("stranger")),
        )
        .await
        .expect("idempotent retry");
    assert!(!again, "already-tombstoned retry reports no change");

    let audits = repo
        .authority_state()
        .read()
        .await
        .audit
        .iter()
        .filter(|row| row.action == "retire")
        .count();
    assert_eq!(audits, 1, "exactly one retire audit row");
}

/// Two racing retirements with DISTINCT mutation ids serialize
/// on the write span's in-span liveness re-check: exactly one
/// reports changed, exactly one retire audit commits.
#[tokio::test]
async fn concurrent_memory_retirement_reports_one_change_and_one_audit() {
    let repo = Arc::new(MemoryBotRepo::new());
    seed_owned_bot(&repo, "bot_mem_race", "own").await;

    // Hold the authority write guard: both retirements
    // complete their pre-reads, then queue on the guard — the
    // deterministic pre-fix race window.
    let guard = repo.authority_state().write().await;
    let first = {
        let repo = repo.clone();
        tokio::spawn(async move {
            repo.retire_with_authority(
                "bot_mem_race",
                mutation("race_a", human_operator("own")),
            )
            .await
        })
    };
    let second = {
        let repo = repo.clone();
        tokio::spawn(async move {
            repo.retire_with_authority(
                "bot_mem_race",
                mutation("race_b", human_operator("own")),
            )
            .await
        })
    };
    // Let both tasks park on the write guard (cooperative
    // scheduling — no clock dependency).
    for _ in 0..16 {
        tokio::task::yield_now().await;
    }
    drop(guard);

    let first = first.await.expect("first joins");
    let second = second.await.expect("second joins");
    let mut changes = vec![
        first.expect("first retires"),
        second.expect("second retires"),
    ];
    changes.sort();
    assert_eq!(
        changes,
        vec![false, true],
        "exactly one racing retirement reports the transition"
    );
    let audits = repo
        .authority_state()
        .read()
        .await
        .audit
        .iter()
        .filter(|row| row.action == "retire")
        .count();
    assert_eq!(audits, 1, "exactly one retire audit row");
}

/// The sync partition reads the Human registry INSIDE the
/// write span: a Human tombstoned while the command queues on
/// the authority guard is filtered out of the applied
/// remainder — never granted a fresh manager edge.
#[tokio::test]
async fn memory_sync_filters_humans_tombstoned_before_the_write_span() {
    let repo = Arc::new(MemoryBotRepo::new());
    seed_owned_bot(&repo, "bot_mem_sync", "own").await;
    grant_manager(&repo, "bot_mem_sync", "ghost").await;

    let guard = repo.authority_state().write().await;
    let sync = {
        let repo = repo.clone();
        tokio::spawn(async move {
            repo.sync_managers(ManagerSync {
                bot_id: "bot_mem_sync".to_string(),
                user_ids: vec![human_actor_id("ghost")],
                mutation: mutation(
                    "sync_a",
                    AuthorityActor::Service { app_id: 7 },
                ),
            })
            .await
        })
    };
    for _ in 0..16 {
        tokio::task::yield_now().await;
    }
    // Tombstone the Human actor while the sync waits on the
    // authority guard (deletion never waits on authority).
    repo.soft_delete(&human_actor_id("ghost")).await;
    drop(guard);

    let change = sync.await.expect("sync joins").expect("sync result");
    assert_eq!(
        (change.added, change.removed, change.total),
        (0, 1, 0),
        "the tombstoned Human is filtered from the remainder (never \
         re-added); the replace then clears their residual edge"
    );
    let approved = repo
        .authority_state()
        .read()
        .await
        .edges
        .iter()
        .any(|(key, (status, _))| {
            key.2 == human_actor_id("ghost")
                && *status == MemoryAuthorityEdgeStatus::Approved
        });
    assert!(
        !approved,
        "no approved edge may leak to a tombstoned Human"
    );
}

/// A runtime version-0 Bot carries no owner/manager register:
/// the ordinary lifecycle delete keeps its legacy contract.
#[tokio::test]
async fn memory_retirement_of_version0_runtime_keeps_working() {
    let repo = Arc::new(MemoryBotRepo::new());
    repo.register("bot_mem_v0".to_string(), BotCapabilities::default())
        .await
        .expect("register runtime bot");

    let changed = repo
        .retire_with_authority(
            "bot_mem_v0",
            mutation("retire_v0", human_operator("11111111")),
        )
        .await
        .expect("version-0 runtime retire");
    assert!(changed);
    let audits = repo
        .authority_state()
        .read()
        .await
        .audit
        .iter()
        .filter(|row| row.action == "retire")
        .count();
    assert_eq!(audits, 1, "exactly one retire audit row");
}

//! Shared memory authority contract fixture
//! (the REAL `MemoryBotRepo` shared atomic state) —
//! the SAME fixture body the memory suite drives,
//! published so the Rule 25 conformance entries run
//! it too (bot-authority Task 8 mechanical
//! publication).

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityActorKind, AuthorityMutation, BotAccessRelation,
    ManagerEdit, ManagerEditAction, ManagerSync,
    human_user_id_of_actor,
};
use bcs_test_support::contract::bot_authority_contract::{
    BotAuthorityContractFixture, HarnessAuditRow,
};
use bcs_test_support::contract::bot_transfer_contract::{
    BotTransferContractFixture, HarnessTransferSeed,
};

/// The memory projection resolves the process env per
/// call; seeds and reads share it.
pub async fn target_env() -> String {
    bcs_config::resolve_env_str()
}

pub struct MemoryAuthorityFixture {
    pub repo: Arc<MemoryBotRepo>,
}

impl MemoryAuthorityFixture {
    pub async fn state_write(&self) -> tokio::sync::RwLockWriteGuard<
        '_,
        bcs_bot_store::memory_authority::MemoryAuthorityState,
    > {
        self.repo.authority_state().write().await
    }

    pub async fn state_read(&self) -> tokio::sync::RwLockReadGuard<
        '_,
        bcs_bot_store::memory_authority::MemoryAuthorityState,
    > {
        self.repo.authority_state().read().await
    }


}

fn grant_kind(kind: BotAccessRelation) -> bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind {
    match kind {
        BotAccessRelation::Owner => {
            bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner
        }
        BotAccessRelation::Manager => {
            bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Manager
        }
    }
}

#[async_trait]
impl BotAuthorityContractFixture for MemoryAuthorityFixture {
    fn authority_repo(&self) -> Arc<dyn BotAuthorityRepoPort> {
        self.repo.clone()
    }

    async fn seed_bot(&self, env: &str, bot_id: &str, ownership_version: u64) {
        let _ = env;
        self.repo
            .register(
                bot_id.to_string(),
                BotCapabilities::default(),
            )
            .await
            .expect("register fixture bot");
        self.state_write()
            .await
            .ownership_versions
            .insert(
                (target_env().await, bot_id.to_string()),
                ownership_version,
            );
    }

    async fn delete_bot(&self, _env: &str, bot_id: &str) {
        self.repo.soft_delete(bot_id).await;
    }

    async fn grant_edge(&self, env: &str, bot_id: &str, actor_id: &str, kind: BotAccessRelation) {
        // Production-faithful: a granted edge implies its
        // Human actor row (spec §3.1), ensured BEFORE the
        // authority write guard per the lock order.
        if let Some(user) = human_user_id_of_actor(actor_id) {
            self.seed_human(env, &user).await;
        }
        self.state_write().await.edges.insert(
            (env.to_string(), bot_id.to_string(), actor_id.to_string(), grant_kind(kind)),
            (bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Approved, 0),
        );
    }

    async fn revoke_edge(&self, env: &str, bot_id: &str, actor_id: &str, kind: BotAccessRelation) {
        self.state_write().await.edges.insert(
            (env.to_string(), bot_id.to_string(), actor_id.to_string(), grant_kind(kind)),
            (bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Revoked, 0),
        );
    }

    async fn seed_runtime_edge(&self, env: &str, bot_id: &str, actor_id: &str) {
        self.state_write().await.edges.insert(
            (
                env.to_string(),
                bot_id.to_string(),
                actor_id.to_string(),
                bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::PermissionProfile,
            ),
            (bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Approved, 42),
        );
    }

    async fn seed_corrupt_duplicate_owner(&self, env: &str, bot_id: &str, actor_id: &str) {
        self.state_write().await.edges.insert(
            (
                env.to_string(),
                bot_id.to_string(),
                actor_id.to_string(),
                bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner,
            ),
            (bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Approved, 0),
        );
    }

    async fn seed_human(&self, env: &str, user_id: &str) {
        // Env is real: another env's Human lives in its own
        // actor row env, so the env-bound guards skip it.
        self.repo.seed_human_actor_in(env, user_id).await;
    }

    async fn remove_human(&self, _env: &str, user_id: &str) {
        self.repo.remove_human_actor_in(user_id).await;
    }

    async fn seed_audit_conflict(&self, env: &str, mutation_id: &str, sequence: u64) {
        let mut state = self.state_write().await;
        // The memory counterpart of the SQL UNIQUE
        // `(env, mutation_id, sequence)`: the write paths
        // must refuse the mutating command。
        state.audit.push(
            MemoryAuthorityAuditRow {
                env: env.to_string(),
                bot_id: "fixture-seed".to_string(),
                mutation_id: mutation_id.to_string(),
                sequence,
                action: "seed_conflict".to_string(),
                actor_kind: "system".to_string(),
                actor_id: "system_seed".to_string(),
                subject_id: "none".to_string(),
                before_state: None,
                after_state: "none".to_string(),
                created_ms: 0,
            },
        );
    }

    async fn audit_rows(&self, env: &str) -> Vec<HarnessAuditRow> {
        self.state_read()
            .await
            .audit
            .iter()
            .filter(|row| row.env == env)
            .map(|row| HarnessAuditRow {
                mutation_id: row.mutation_id.clone(),
                sequence: row.sequence,
                action: row.action.clone(),
                actor_kind: row.actor_kind.clone(),
                actor_id: row.actor_id.clone(),
                subject_id: row.subject_id.clone(),
                before_state: row.before_state.clone(),
                after_state: Some(row.after_state.clone()),
            })
            .collect()
    }
}

fn ms_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}


#[async_trait]
impl BotTransferContractFixture for MemoryAuthorityFixture {
    async fn seed_transfer(&self, env: &str, seed: HarnessTransferSeed) {
        let now = ms_now();
        let offset_ms = |offset_s: i64| {
            if offset_s >= 0 {
                now + (offset_s as u64) * 1_000
            } else {
                now.saturating_sub(((-offset_s) as u64) * 1_000)
            }
        };
        self.state_write()
            .await
            .transfers
            .insert(
                (
                    env.to_string(),
                    seed.bot_id.clone(),
                    seed.target_owner_actor_id.clone(),
                ),
                bcs_bot_store::memory_authority::MemoryTransferRow {
                    bot_id: seed.bot_id,
                    target_owner_actor_id: seed.target_owner_actor_id,
                    from_owner_actor_id: seed.from_owner_actor_id,
                    expected_ownership_version: seed.expected_ownership_version,
                    status: seed.status,
                    requested_at: offset_ms(seed.requested_at_offset_s),
                    expires_at: offset_ms(seed.expires_at_offset_s),
                    updated_at: offset_ms(seed.requested_at_offset_s),
                    decided_at: seed.decided_at_offset_s.map(offset_ms),
                    decided_by_kind: seed.decided_by_kind.map(|kind| match kind.as_str() {
                        "human" => AuthorityActorKind::Human,
                        "service" => AuthorityActorKind::Service,
                        "provider" => AuthorityActorKind::Provider,
                        _ => AuthorityActorKind::System,
                    }),
                    decided_by_id: seed.decided_by_id,
                    reason: seed.reason,
                    result_ownership_version: seed.result_ownership_version,
                    bot_name_snapshot: None,
                },
            );
    }
}

/// The staged clone/swap write path must leak nothing: a
/// failing sync (reserved audit sequence, the memory
/// counterpart of the audit UNIQUE conflict) discards the
/// staged state and the approved set stays untouched.
#[tokio::test]
async fn memory_failed_sync_leaks_nothing() {
    let env = target_env().await;
    let fixture = MemoryAuthorityFixture {
        repo: Arc::new(MemoryBotRepo::new()),
    };
    fixture.seed_bot(&env, "bot_leak", 1).await;
    fixture.seed_human(&env, "boss").await;
    fixture
        .grant_edge(&env, "bot_leak", "human_boss", BotAccessRelation::Owner)
        .await;
    fixture
        .grant_edge(&env, "bot_leak", "human_keeper", BotAccessRelation::Manager)
        .await;
    fixture.seed_human(&env, "keeper").await;
    fixture
        .revoke_edge(&env, "bot_leak", "human_boss", BotAccessRelation::Manager)
        .await;
    fixture.seed_audit_conflict(&env, "mutation-leak", 0).await;

    let result = fixture
        .authority_repo()
        .sync_managers(ManagerSync {
            bot_id: "bot_leak".to_string(),
            user_ids: vec![],
            mutation: AuthorityMutation {
                mutation_id: "mutation-leak".to_string(),
                actor: AuthorityActor::Service { app_id: 9 },
            },
        })
        .await;
    assert!(result.is_err(), "the audit conflict must fail the sync");
    // Nothing half-written leaked: the manager edge and
    // the audit list are unchanged.
    assert_eq!(
        fixture
            .authority_repo()
            .role("human_keeper", "bot_leak")
            .await
            .expect("role"),
        Some(BotAccessRelation::Manager)
    );
    {
        // Scoped read: the shared-state guard must not be
        // held across the second write command (same
        // single-RwLock boundary as production).
        let state = fixture.state_read().await;
        let audit_rows = state
            .audit
            .iter()
            .filter(|row| {
                row.mutation_id == "mutation-leak"
                    && row.action != "seed_conflict"
            })
            .count();
        assert_eq!(
            audit_rows, 0,
            "failed sync must not write audit rows (beyond the \
             seeded conflict marker)"
        );
    }

    // A second, non-conflicting sync succeeds and covers
    // manually seeded manager rows (mutation ids are
    // unique per command).
    let change = fixture
        .authority_repo()
        .sync_managers(ManagerSync {
            bot_id: "bot_leak".to_string(),
            user_ids: vec![],
            mutation: AuthorityMutation {
                mutation_id: "mutation-leak-2".to_string(),
                actor: AuthorityActor::Service { app_id: 9 },
            },
        })
        .await
        .expect("clean empty sync");
    assert_eq!(change.removed, 1);
    assert_eq!(
        fixture
            .authority_repo()
            .role("human_keeper", "bot_leak")
            .await
            .expect("role"),
        None
    );
}

/// Failed single-item edits (unknown Human, owner target)
/// must not leak staged edges either.
#[tokio::test]
async fn memory_failed_edit_leaks_nothing() {
    let env = target_env().await;
    let fixture = MemoryAuthorityFixture {
        repo: Arc::new(MemoryBotRepo::new()),
    };
    fixture.seed_bot(&env, "bot_edit_leak", 1).await;
    fixture.seed_human(&env, "boss").await;
    fixture
        .grant_edge(&env, "bot_edit_leak", "human_boss", BotAccessRelation::Owner)
        .await;

    // Unknown target.
    let result = fixture
        .authority_repo()
        .edit_manager(ManagerEdit {
            bot_id: "bot_edit_leak".to_string(),
            user_id: "human_ghost".to_string(),
            action: ManagerEditAction::Grant,
            mutation: AuthorityMutation {
                mutation_id: "mutation-ghost".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "boss".to_string(),
                },
            },
        })
        .await;
    assert!(result.is_err());
    // Owner target.
    let result = fixture
        .authority_repo()
        .edit_manager(ManagerEdit {
            bot_id: "bot_edit_leak".to_string(),
            user_id: "human_boss".to_string(),
            action: ManagerEditAction::Grant,
            mutation: AuthorityMutation {
                mutation_id: "mutation-owner-target".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "boss".to_string(),
                },
            },
        })
        .await;
    assert!(result.is_err());
    // No edges beyond the owner edge; no audit rows
    // (last read of the test — no later write awaits
    // on the same boundary).
    let state = fixture.state_read().await;
    let manager_edges = state
        .edges
        .iter()
        .filter(|(key, (status, _))| {
            key.1 == "bot_edit_leak"
                && key.3 == bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Manager
                && *status == bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Approved
        })
        .count();
    assert_eq!(manager_edges, 0, "failed edits must not leak edges");
    assert!(state.audit.is_empty(), "failed edits must not audit");
}

// ===========================================================================
// Task 4: trusted memory registration + shared profile state (4.1b / 4.3a).
// The composite runs on the SAME MemoryBotRepo state the runtime
// profile port serves; failures at profile staging and at the
// `initialize` audit abort before any durable or staged write
// publishes./fixtures drive the SAME fields production uses.
// ===========================================================================

use bcs_bot_store::MemoryAuthorityAuditRow;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::port::repo::PermissionProfileRepoPort;
use bcs_service_api::types::bot_authority::{
    BotAccessRelation as RoleRelation, OwnershipInitialization,
};
use bcs_service_api::{BotCapabilities, ServiceError};

/// A Task-4 memory repo with an ISOLATED durability
/// directory (fixed test bot IDs must not collide
/// with files from other runs).
fn owned_repo() -> Arc<MemoryBotRepo> {
    // Isolated durability directory (fixed test bot
    // IDs must not collide with files from other
    // runs); the TempDir is intentionally kept alive
    // for the whole test process.
    let dir: &'static tempfile::TempDir =
        Box::leak(Box::new(tempfile::tempdir().expect("tempdir")));
    Arc::new(MemoryBotRepo::with_base_dir(
        dir.path().to_path_buf(),
    ))
}

fn owned_mutation(mutation_id: &str, owner: &str) -> OwnershipInitialization {
    OwnershipInitialization {
        owner_user_id: owner.to_string(),
        mutation: AuthorityMutation {
            mutation_id: mutation_id.to_string(),
            actor: AuthorityActor::Human {
                user_id: owner.to_string(),
            },
        },
    }
}

fn trusted_capabilities() -> BotCapabilities {
    BotCapabilities {
        name: Some("Memory Trusted Bot".to_string()),
        visibility: "protected".to_string(),
        ..Default::default()
    }
}

/// The 4.3a runtime wiring contract: the SAME bot repo Arc is
/// injected as the profile port the runtime consumes.
fn profile_port(repo: &Arc<MemoryBotRepo>) -> Arc<dyn PermissionProfileRepoPort> {
    repo.clone()
}

#[tokio::test]
async fn memory_registration_profile_is_runtime_visible() {
    let repo = Arc::new(owned_repo());
    let env = target_env().await;
    // A preexisting Human ACTOR row (the registry the
    // guards read) that the failed/staged composites must
    // never touch.
    repo.ensure_human_actor("staff-8", "staff-8")
        .await
        .expect("preexisting fixture Human actor row");
    let runtime_profiles = profile_port(&repo);

    let created = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem1".to_string(),
            trusted_capabilities(),
            "staff-8",
            "token-mem-1",
            owned_mutation("m-mem-1", "staff-8"),
        )
        .await
        .expect("committed");
    assert!(created);

    // The profile the registration committed is exactly the
    // runtime port's active default — one shared state.
    let committed = runtime_profiles
        .get_active_default("bot_mem1", &env)
        .await
        .expect("profile committed by registration");
    assert_eq!(committed.revision, 1);
    assert!(committed.is_default);
    assert_eq!(
        committed.rules_template,
        serde_json::json!([
            {"tool": "*", "specifier": "*", "effect": "allow"}
        ])
    );
    // A later runtime `ensure` reuses the SAME record: same id,
    // revision/digest/rules untouched (D12 rule 2).
    let id = runtime_profiles
        .ensure_default_profile("bot_mem1", &env)
        .await
        .expect("ensure");
    assert_eq!(id, committed.permission_profile_id);
    let again = runtime_profiles
        .get_active_default("bot_mem1", &env)
        .await
        .expect("still there");
    assert_eq!(again.revision, 1);
    assert_eq!(again.digest, committed.digest);

    // Runtime `upsert_revision` moves the SAME record, keeping
    // the profile id.
    let updated = bump_revision(&committed);
    runtime_profiles
        .upsert_revision(updated)
        .await
        .expect("upsert");
    let after = runtime_profiles
        .get_active_default("bot_mem1", &env)
        .await
        .expect("default stays");
    assert_eq!(after.permission_profile_id, committed.permission_profile_id);
    assert_eq!(after.revision, 2);
    // And `ensure` after the revision still does not overwrite.
    let id_after = runtime_profiles
        .ensure_default_profile("bot_mem1", &env)
        .await
        .expect("ensure after revision");
    assert_eq!(id_after, committed.permission_profile_id);
    assert_eq!(
        runtime_profiles
            .get_active_default("bot_mem1", &env)
            .await
            .expect("default")
            .revision,
        2
    );

    // The registered owner is the strict owner of the bot.
    repo.role("human_staff-8", "bot_mem1")
        .await
        .expect("role")
        .expect("owner edge");
}

/// A revision bump replayed through the runtime
/// upsert path.
fn bump_revision(
    committed: &bcs_domain::edge_permission::PermissionProfile,
) -> bcs_domain::edge_permission::PermissionProfile {
    let mut next = committed.clone();
    next.revision = 2;
    next.digest = format!("updated-{}", next.digest);
    next.updated_by = Some("test".to_string());
    next
}

#[tokio::test]
async fn memory_registration_profile_or_audit_failure_is_atomic() {
    let repo = Arc::new(owned_repo());
    let env = target_env().await;
    // A preexisting Human ACTOR row (the registry the
    // guards read) that the failed/staged composites must
    // never touch.
    repo.ensure_human_actor("staff-8", "staff-8")
        .await
        .expect("preexisting fixture Human actor row");
    let runtime_profiles = profile_port(&repo);

    // (a) Failure at the temporary (staged) profile creation:
    // a corrupt active-default index makes the composite's
    // profile staging fail BEFORE anything is published.
    {
        let mut profiles = repo.profile_state().write().await;
        profiles.corrupt_active_default(&env, "bot_mem_fail", 9999);
    }
    let error = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_fail".to_string(),
            trusted_capabilities(),
            "staff-8",
            "token-fail",
            owned_mutation("m-fail-1", "staff-8"),
        )
        .await
        .expect_err("profile staging failure aborts");
    assert!(matches!(error, ServiceError::InvalidOperation { .. }));
    assert_unharmed(&repo).await;
    // Preexisting state (the seeded human, the profile
    // allocator) is untouched.
    repo.profile_state().write().await.repair();

    // (b) Failure at the audit after the temp profile staged:
    // pre-seed the audit row this mutation id would write.
    {
        let mut state = repo.authority_state().write().await;
        state.audit.push(MemoryAuthorityAuditRow {
            env: env.clone(),
            bot_id: "bot_mem_fail".to_string(),
            mutation_id: "m-fail-collision".to_string(),
            sequence: 0,
            action: "manager_grant".to_string(),
            actor_kind: "human".to_string(),
            actor_id: "fixture".to_string(),
            subject_id: "fixture".to_string(),
            before_state: None,
            after_state: "fixture".to_string(),
            created_ms: ms_now(),
        });
    }
    let error = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_fail".to_string(),
            trusted_capabilities(),
            "staff-8",
            "token-fail-2",
            owned_mutation("m-fail-collision", "staff-8"),
        )
        .await
        .expect_err("audit failure aborts the whole command");
    assert!(matches!(error, ServiceError::InternalError(_)));
    assert_unharmed(&repo).await;
    // The preexisting audit row survived intact.
    assert_eq!(
        repo.authority_state().read().await.audit.len(),
        1,
        "preexisting audit row unharmed"
    );

    // Retry (fresh mutation, honest state) yields exactly ONE
    // default profile and the SAME id the runtime port keeps
    // serving afterwards.
    let created = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_fail".to_string(),
            trusted_capabilities(),
            "staff-8",
            "token-ok",
            owned_mutation("m-fail-ok", "staff-8"),
        )
        .await
        .expect("retry commits");
    assert!(created);
    let committed = runtime_profiles
        .get_active_default("bot_mem_fail", &env)
        .await
        .expect("profile committed");
    let ensure_id = runtime_profiles
        .ensure_default_profile("bot_mem_fail", &env)
        .await
        .expect("ensure");
    assert_eq!(ensure_id, committed.permission_profile_id);
    assert_eq!(committed.revision, 1);
    assert_eq!(
        runtime_profiles
            .get_active_default("bot_mem_fail", &env)
            .await
            .expect("default")
            .rules_template,
        serde_json::json!([
            {"tool": "*", "specifier": "*", "effect": "allow"}
        ])
    );
    let profiles_read = repo.profile_state().read().await;
    assert_eq!(
        profiles_read.records_for_bot(&env, "bot_mem_fail"),
        1,
        "exactly one default profile"
    );
    drop(profiles_read);
    // upsert_revision keeps the id.
    runtime_profiles
        .upsert_revision(bump_revision(&committed))
        .await
        .expect("upsert");
    let after = runtime_profiles
        .get_active_default("bot_mem_fail", &env)
        .await
        .expect("default");
    assert_eq!(after.permission_profile_id, committed.permission_profile_id);
    assert_eq!(after.revision, 2);
}

async fn assert_unharmed(repo: &Arc<MemoryBotRepo>) {
    let state = repo.authority_state().read().await;
    assert!(
        state.ownership_versions.is_empty(),
        "no half-written ownership version"
    );
    assert!(
        state.edges.is_empty(),
        "no half-written owner edge"
    );
    let profiles = repo.profile_state().read().await;
    assert_eq!(
        profiles.record_count(),
        0,
        "no profile record published by a failed registration"
    );
    assert_eq!(
        profiles.next_id_for_check(), 1,
        "allocator untouched by a failed registration"
    );
    drop(profiles);
    assert!(repo.get("bot_mem_fail").await.is_none());
    assert!(repo.load_token("token-fail").await.is_none());
    let _ = RoleRelation::Owner; // keep the type import local
}

#[tokio::test]
async fn memory_registration_reused_bot_id_never_resets_owner() {
    let repo = Arc::new(owned_repo());
    let env = target_env().await;
    repo.ensure_human_actor("staff-8", "staff-8")
        .await
        .expect("preexisting fixture Human actor row");
    repo.ensure_human_actor("staff-9", "staff-9")
        .await
        .expect("preexisting fixture Human actor row");
    let created = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_stable".to_string(),
            trusted_capabilities(),
            "staff-8",
            "token-first",
            owned_mutation("m-first", "staff-8"),
        )
        .await
        .expect("first");
    assert!(created);
    // Re-register the SAME id through the runtime create path
    // (no Human): refused, nothing changes.
    let reused = repo
        .create_registration_if_absent(
            "bot_mem_stable".to_string(),
            trusted_capabilities(),
            "nobody",
            "token-second",
        )
        .await
        .expect("create-only");
    assert!(!reused);
    // Trusted re-registration of the same id is refused too,
    // and never resets owner/version/profile state.
    let reused = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_stable".to_string(),
            trusted_capabilities(),
            "staff-9",
            "token-attacker",
            owned_mutation("m-attacker", "staff-9"),
        )
        .await
        .expect("create-only re-registration");
    assert!(!reused);
    let relation = repo
        .role("human_staff-8", "bot_mem_stable")
        .await
        .expect("role");
    assert!(matches!(relation, Some(RoleRelation::Owner)));
    assert!(!matches!(
        repo.role("human_staff-9", "bot_mem_stable")
            .await
            .expect("role"),
        Some(RoleRelation::Owner)
    ));
    let version = repo
        .authority_state()
        .read()
        .await
        .ownership_versions
        .get(&(env, "bot_mem_stable".to_string()))
        .copied();
    assert_eq!(version, Some(1));

    // A tombstoned id never resurrects.
    assert!(repo.soft_delete("bot_mem_stable").await);
    let revived = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_stable".to_string(),
            trusted_capabilities(),
            "staff-9",
            "token-revive",
            owned_mutation("m-revive", "staff-9"),
        )
        .await
        .expect("tombstone refusal");
    assert!(!revived);
}

/// Version-0 runtime entry keeps its own non-Human path
/// (spec §10.3): the runtime create leaves no authority rows.
#[tokio::test]
async fn memory_runtime_create_stays_version_zero() {
    let repo = Arc::new(owned_repo());
    let created = repo
        .create_registration_if_absent(
            "bot_v0".to_string(),
            trusted_capabilities(),
            "11111111",
            "token-v0",
        )
        .await
        .expect("runtime create");
    assert!(created);
    let state = repo.authority_state().read().await;
    assert!(state.ownership_versions.is_empty());
    assert!(state.edges.is_empty());
    assert!(state.audit.is_empty());
    let _ = target_env().await;
}

#[tokio::test]
async fn memory_provider_registration_failure_leaves_authority_unharmed() {
    let repo = owned_repo();
    let memberships = Arc::new(bcs_bot_store::MemoryProviderStore::new());
    let metadata = Arc::new(bcs_bot_store::provider::MemoryBotProviderStore::new(
        repo.clone(),
        memberships,
    ));
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let _ = now;
    let record = |ref_value: &str| bcs_service_api::bot_provider::BotProviderRecord {
        bot_uuid: "bot_mem_provider".to_string(),
        provider_id: "provider-a".to_string(),
        provider_bot_ref: ref_value.to_string(),
        connection_mode: bcs_service_api::bot_provider::BotConnectionMode::Plugin,
        webhook_url: None,
        is_deleted: false,
    };

    // First registration commits everything.
    metadata
        .create_provider_bot_with_ownership(
            record("ref-1"),
            trusted_capabilities(),
            "staff-8",
            "token-prov",
            owned_mutation("m-prov-1", "staff-8"),
        )
        .await
        .expect("committed");
    assert!(
        metadata.get_provider_bot("bot_mem_provider")
            .await
            .expect("read")
            .is_some()
    );
    // The duplicate ref conflicts (fail BEFORE any validated write).
    let error = metadata
        .create_provider_bot_with_ownership(
            record("ref-1"),
            trusted_capabilities(),
            "staff-8",
            "token-prov-2",
            owned_mutation("m-prov-2", "staff-8"),
        )
        .await
        .expect_err("duplicate ref");
    assert!(matches!(error, ServiceError::Conflict(_)));
    // Owner/version/profile stay exactly the winner's.
    let relation = repo
        .role("human_staff-8", "bot_mem_provider")
        .await
        .expect("role");
    assert!(matches!(relation, Some(RoleRelation::Owner)));
    let profiles = repo.profile_state().read().await;
    assert_eq!(profiles.records_for_bot(&target_env().await, "bot_mem_provider"), 1);
    let audits = repo.authority_state().read().await;
    let initialize_rows = audits
        .audit
        .iter()
        .filter(|row| row.bot_id == "bot_mem_provider")
        .count();
    assert_eq!(initialize_rows, 1, "exactly one initialize audit");
}

#[tokio::test]
async fn memory_registration_creates_the_owner_actor_row_and_the_owner_can_manage() {
    // Ruling T4-1 memory mirror: one fresh
    // registration per owner ENSURES the Human ACTOR
    // row (the authority registry on the shared bots
    // map) in the SAME registration, so the Task-2
    // human-existence guard accepts that owner as a
    // manager target immediately after. No OAuth
    // identity row is required.
    let repo = owned_repo();
    let created = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_pair".to_string(),
            trusted_capabilities(),
            "owner-a",
            "token-a",
            owned_mutation("m-a", "owner-a"),
        )
        .await
        .expect("first owner commits");
    assert!(created);
    let created = repo
        .create_registration_if_absent_with_ownership(
            "bot_mem_pair2".to_string(),
            trusted_capabilities(),
            "owner-b",
            "token-b",
            owned_mutation("m-b", "owner-b"),
        )
        .await
        .expect("second owner commits");
    assert!(created);
    // Both Human ACTOR rows are present on the shared
    // registry (and nothing else was needed).
    for staff in ["owner-a", "owner-b"] {
        let actor = repo
            .get(&format!("human_{staff}"))
            .await
            .unwrap_or_else(|| panic!("human_{staff} actor row must exist"));
        assert!(
            matches!(actor.actor_kind, bcs_service_api::ActorKind::Human),
            "human_{staff} must be a Human actor row"
        );
    }
    // The FIRST owner (registered Human) grants the
    // second as a manager via the strict Task-2
    // command: the fresh registrations pass its
    // human-existence guards unchanged.
    let change = repo
        .edit_manager(ManagerEdit {
            bot_id: "bot_mem_pair".to_string(),
            user_id: "human_owner-b".to_string(),
            action: ManagerEditAction::Grant,
            mutation: AuthorityMutation {
                mutation_id: "m-grant".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "owner-a".to_string(),
                },
            },
        })
        .await
        .expect("manager grant by the registered owner");
    assert_eq!(change.added, 1);
    assert_eq!(change.total, 1);
}

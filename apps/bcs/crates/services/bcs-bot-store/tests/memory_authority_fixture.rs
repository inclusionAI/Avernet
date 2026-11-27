//! Memory authority atomicity and registration regressions.

#![allow(clippy::expect_used, clippy::unwrap_used)]

#[path = "support/memory_authority_fixture.rs"]
mod fixture;

use std::sync::Arc;

use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, BotAccessRelation, ManagerEdit, ManagerEditAction, ManagerSync,
};
use bcs_test_support::contract::bot_authority_contract::BotAuthorityContractFixture;
use fixture::{MemoryAuthorityFixture, target_env};

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
        initial_manager_user_ids: Vec::new(),
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
            created_ms: fixture::ms_now(),
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

#[path = "common/memory_registration_manager.rs"]
mod registration_manager;

//! Shared Group authorization-snapshot scenarios
//! (bot-authority plan Task 6.4/6.4a, spec §8.3).
//!
//! Scenario functions take the stores as parameters so the
//! SAME scenario set runs on the SQLite store (local
//! suites), the Memory projection, and — via the
//! `authority_mysql.rs` plan §6 gate — the MySQL store
//! over the real full MySQL migration chain.

use std::sync::Arc;

use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_domain::{
    ActorKind, GroupStrategy, MessageViewScope, ParticipantMode, ParticipantRole,
};
use bcs_group_store::{GroupBuilder, MySqlGroupStore};
use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::types::authorization_snapshot::GroupAuthorizationSnapshot;
use bcs_service_api::{Group, Participant, ServiceError};

pub const ENV: &str = "auth-snap";

pub fn human_participant(
    actor_id: &str,
    role: ParticipantRole,
    scope: MessageViewScope,
) -> Participant {
    Participant {
        bot_uuid: actor_id.to_string(),
        bot_name: None,
        kind: None,
        role,
        actor_kind: ActorKind::Human,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: scope,
    }
}

pub fn bot_participant(actor_id: &str, role: ParticipantRole) -> Participant {
    Participant::bot(actor_id, role)
}

fn strategy_group(strategy: GroupStrategy, extra: Participant) -> Group {
    let mut group = GroupBuilder::new("bot-driver")
        .id(format!("snap-{strategy:?}").to_lowercase())
        .build();
    group.group_strategy = strategy;
    group.participants = vec![
        bot_participant("bot-driver", lead_role(strategy)),
        extra,
    ];
    group
}

fn lead_role(strategy: GroupStrategy) -> ParticipantRole {
    match strategy {
        GroupStrategy::ManagerWorker => ParticipantRole::Manager,
        _ => ParticipantRole::Driver,
    }
}

pub async fn assert_strategy_gating(
    repo: &dyn GroupRepoPort,
    reader: &dyn GroupRepoPort,
) {
    // 6.4a `manager_role_is_strategy_gated`: the same-named extra
    // Manager participant becomes a management candidate ONLY
    // under ManagerWorker — driver/originator always count, and
    // Chat/StateMachine must NEVER elevate the extra Manager.
    for strategy in [
        GroupStrategy::Chat,
        GroupStrategy::ManagerWorker,
        GroupStrategy::StateMachine,
    ] {
        let group = strategy_group(
            strategy,
            human_participant(
                "human-extra-manager",
                ParticipantRole::Manager,
                MessageViewScope::Full,
            ),
        );
        repo.upsert(group).await.expect("upsert strategy group");

        let snapshot = reader
            .read_authorization_snapshot(&format!("snap-{strategy:?}").to_lowercase())
            .await
            .expect("read snapshot")
            .expect("group snapshot");
        let candidates = snapshot.management_actor_ids();
        assert_eq!(
            snapshot.group_strategy, strategy,
            "strategy projects as persisted"
        );
        assert!(
            candidates.contains(&"bot-driver"),
            "driver is always a management candidate"
        );
        if strategy == GroupStrategy::ManagerWorker {
            assert!(
                candidates.contains(&"human-extra-manager"),
                "ManagerWorker elevates the extra Manager"
            );
        } else {
            assert!(
                !candidates.contains(&"human-extra-manager"),
                "Chat/StateMachine must not pass the same-named Manager"
            );
        }
    }
}

pub async fn assert_scope_only_update_changes_snapshot(
    writer: &dyn GroupRepoPort,
    reader2: &dyn GroupRepoPort,
) {
    // Two independent store instances over the same state: the
    // reader must observe a scope-only change on the NEXT
    // projection with all actor fields stable.
    let mut group = GroupBuilder::new("bot-driver")
        .id("snap-scope-only")
        .build();
    group.participants = vec![human_participant(
        "human-view",
        ParticipantRole::Observer,
        MessageViewScope::Full,
    )];
    writer.upsert(group).await.expect("upsert");

    let before = reader2
        .read_authorization_snapshot("snap-scope-only")
        .await
        .expect("read before")
        .expect("snapshot");
    let participant_before = &before
        .participants
        .iter()
        .find(|p| p.actor_id == "human-view")
        .expect("participant seeded")
        .clone();
    assert_eq!(participant_before.message_view_scope, MessageViewScope::Full);

    writer
        .update_participant_message_view_scope(
            "snap-scope-only",
            "human-view",
            MessageViewScope::Participant,
        )
        .await
        .expect("scope-only update");

    let after = reader2
        .read_authorization_snapshot("snap-scope-only")
        .await
        .expect("read after")
        .expect("snapshot");
    let participant_after = after
        .participants
        .iter()
        .find(|p| p.actor_id == "human-view")
        .expect("participant after update");
    // Only the scope flips: actor/role/mode and all other
    // authorization facts stay stable.
    assert_eq!(participant_after.message_view_scope, MessageViewScope::Participant);
    assert_eq!(participant_after.actor_id, participant_before.actor_id);
    assert_eq!(participant_after.actor_kind, participant_before.actor_kind);
    assert_eq!(participant_after.role, participant_before.role);
    assert_eq!(participant_after.effective_mode, participant_before.effective_mode);
    assert_eq!(after.management_actor_ids(), before.management_actor_ids());
    let mut stripped = after.clone();
    stripped.participants = before.participants.clone();
    assert_eq!(stripped, before, "scope-only update changes ONLY scope");
}

pub async fn assert_group_seed_facts(repo: &dyn GroupRepoPort, reader: &dyn GroupRepoPort) {
    // The Group snapshot is the seed source (spec §8.3): its
    // participants carry role/mode/scope facts a Session creation
    // can seed from, and a scope change on the GROUP works on the
    // group row only (never retro-overwrites any Session; the
    // Session-side independence is pinned in the session-store
    // suite).
    let mut group = GroupBuilder::new("bot-driver")
        .id("snap-seed-source")
        .build();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        bot_participant("bot-lead", ParticipantRole::Manager),
        bot_participant("bot-worker", ParticipantRole::Worker),
        human_participant(
            "human-view",
            ParticipantRole::Observer,
            MessageViewScope::Participant,
        ),
    ];
    repo.upsert(group).await.expect("upsert");

    let snapshot = reader
        .read_authorization_snapshot("snap-seed-source")
        .await
        .expect("read")
        .expect("snapshot");
    assert_eq!(snapshot.participants.len(), 3);
    assert_eq!(snapshot.driver_actor_id, "bot-driver");
    // Existing default-driver rule: unset originator projects as
    // the driver.
    assert_eq!(snapshot.originator_actor_id, "bot-driver");
    assert!(snapshot.live);
    assert_eq!(snapshot.record_status, "active");
    let human = snapshot
        .participants
        .iter()
        .find(|p| p.actor_id == "human-view")
        .expect("human participant");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.effective_mode, ParticipantMode::Present);
    assert_eq!(human.message_view_scope, MessageViewScope::Participant);

    // A later Group scope change does NOT retroactively rewrite
    // the seed facts of anything already derived; it only changes
    // the Group's own projection.
    repo.update_participant_message_view_scope(
        "snap-seed-source",
        "human-view",
        MessageViewScope::Full,
    )
    .await
    .expect("group scope update");
    let updated = reader
        .read_authorization_snapshot("snap-seed-source")
        .await
        .expect("read")
        .expect("snapshot");
    assert_eq!(
        updated
            .participants
            .iter()
            .find(|p| p.actor_id == "human-view")
            .expect("human participant")
            .message_view_scope,
        MessageViewScope::Full
    );
}

pub async fn assert_nonexistent_group_is_none(repo: &dyn GroupRepoPort) {
    assert!(
        repo.read_authorization_snapshot("snap-never-created")
            .await
            .expect("missing group is Ok(None)")
            .is_none()
    );
}

/// Cache-poisoning proof through a SECOND, independent
/// store instance over the same database: the reader
/// caches the group through its ordinary `try_get`, the
/// writer flips the scope through its own instance, and
/// the snapshot read through the reader must observe the
/// MASTER value — never its poisoned cache.
pub async fn assert_cache_freshness_through_independent_reader(
    writer: &dyn GroupRepoPort,
    reader2: &dyn GroupRepoPort,
) {
    writer
        .update_participant_message_view_scope(
            "snap-scope-only",
            "human-view",
            MessageViewScope::Full,
        )
        .await
        .expect("reset scope to Full");
    let _ = reader2
        .try_get("snap-scope-only")
        .await
        .expect("poison reader cache")
        .expect("cached group");
    writer
        .update_participant_message_view_scope(
            "snap-scope-only",
            "human-view",
            MessageViewScope::Participant,
        )
        .await
        .expect("flip scope behind the cache");
    let snapshot = reader2
        .read_authorization_snapshot("snap-scope-only")
        .await
        .expect("snapshot after cache poisoning")
        .expect("snapshot");
    assert_eq!(
        snapshot
            .participants
            .iter()
            .find(|p| p.actor_id == "human-view")
            .expect("human participant")
            .message_view_scope,
        MessageViewScope::Participant,
        "snapshot must bypass the ordinary try_get display cache"
    );
}

/// Instance-level env binding: groups written through
/// another env are invisible through this store.
pub async fn assert_env_isolation(
    writer_env_a: &dyn GroupRepoPort,
    reader_env_b: &dyn GroupRepoPort,
) {
    let mut group = GroupBuilder::new("bot-driver").id("snap-env-iso").build();
    group.participants = vec![bot_participant("bot-worker", ParticipantRole::Worker)];
    writer_env_a.upsert(group).await.expect("upsert env-a");

    let snapshot = reader_env_b
        .read_authorization_snapshot("snap-env-iso")
        .await
        .expect("env-b read must be an Ok(None), not an error");
    assert!(snapshot.is_none(), "env isolation: env-b sees no env-a group");
}

/// `live` is the record_status projection (a provisioning
/// record is NOT a live resource yet) and deleted groups
/// are physically gone: `Ok(None)`, not a stale
/// live=false projection.
pub async fn assert_live_and_deleted_projection(writer: &dyn GroupRepoPort) {
    let mut provisioning = GroupBuilder::new("bot-driver")
        .id("snap-provisioning")
        .build();
    provisioning.record_status = "provisioning".to_string();
    writer.upsert(provisioning).await.expect("upsert provisioning");

    let snapshot = writer
        .read_authorization_snapshot("snap-provisioning")
        .await
        .expect("read")
        .expect("provisioning snapshot");
    assert!(!snapshot.live);
    assert_eq!(snapshot.record_status, "provisioning");

    writer.delete("snap-provisioning").await.expect("delete");
    assert!(
        writer
            .read_authorization_snapshot("snap-provisioning")
            .await
            .expect("read after delete")
            .is_none()
    );
}

/// SQL snapshots report the INSTANCE-bound env (never the
/// process env). Needs the `snap-scope-only` group from
/// [`assert_scope_only_update_changes_snapshot`].
pub async fn assert_snapshot_env_binding(
    reader2: &dyn GroupRepoPort,
    expected_env: &str,
) {
    let snapshot: GroupAuthorizationSnapshot = reader2
        .read_authorization_snapshot("snap-scope-only")
        .await
        .expect("read")
        .expect("snapshot");
    assert_eq!(snapshot.env, expected_env);
}

/// Snapshot reads are strict: a storage failure is
/// `Err` — never `Ok(None)`, never a cache fallback.
pub async fn assert_snapshot_failure_is_strict(failing_store: MySqlGroupStore) {
    let error = failing_store
        .read_authorization_snapshot("snap-any")
        .await
        .err()
        .expect("storage failure is Err");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "snapshot failures are strict internal errors: {error:?}"
    );
}

pub fn fail_db() -> Arc<dyn DbPlugin> {
    Arc::new(FailingDb)
}

struct FailingDb;

#[async_trait::async_trait]
impl DbPlugin for FailingDb {
    async fn query(&self, _statement: DbStatement) -> Result<Vec<DbRow>, DbError> {
        Err(DbError::Backend("forced group snapshot failure".to_string()))
    }

    async fn execute(&self, _statement: DbStatement) -> Result<DbExecuteResult, DbError> {
        Err(DbError::Backend("forced group snapshot failure".to_string()))
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> Result<Vec<DbTransactionStepResult>, DbError> {
        Err(DbError::Backend("forced group snapshot failure".to_string()))
    }

    async fn health_check(&self) -> Result<DbHealth, DbError> {
        Ok(DbHealth::healthy())
    }
}

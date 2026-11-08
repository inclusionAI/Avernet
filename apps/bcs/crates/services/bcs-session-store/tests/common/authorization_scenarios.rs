//! Shared Session authorization-snapshot scenarios
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
use bcs_domain::{ActorKind, GroupStrategy, MessageViewScope, ParticipantMode, ParticipantRole};
use bcs_service_api::port::repo::{
    GroupRepoPort, NewSessionParams, SessionRepoPort,
};
use bcs_service_api::{Group as DomainGroup, Participant, ServiceError, SessionStatus};
use bcs_session_store::MySqlSessionStore;

pub const ENV: &str = "auth-snap";

pub fn human_participant(
    actor_id: &str,
    role: ParticipantRole,
    scope: MessageViewScope,
) -> Participant {
    Participant {
        bot_uuid: actor_id.to_string(),
        bot_name: Some(actor_id.to_string()),
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

/// A seeded Group with one Bot and one scoped Human
/// participant (the Session seed source).
pub async fn seed_group(
    group_repo: &dyn GroupRepoPort,
    group_id: &str,
    strategy: GroupStrategy,
) -> Vec<Participant> {
    let mut group = DomainGroup::new(group_id, "bot-driver", Vec::new());
    group.group_strategy = strategy;
    group.participants = vec![
        bot_participant("bot-driver", ParticipantRole::Driver),
        bot_participant("bot-worker", ParticipantRole::Worker),
        human_participant(
            "human-view",
            ParticipantRole::Observer,
            MessageViewScope::Participant,
        ),
    ];
    let participants = group
        .participants
        .iter()
        .map(|participant| participant.clone())
        .collect();
    group_repo.upsert(group).await.expect("seed group upsert");

    let snapshot = group_repo
        .read_authorization_snapshot(group_id)
        .await
        .expect("group snapshot")
        .expect("group snapshot");
    assert_eq!(snapshot.group_strategy, strategy);

    participants
}

pub async fn assert_session_snapshot_facts(
    repo: &dyn SessionRepoPort,
    group_repo: &dyn GroupRepoPort,
) {
    // create-time (6.4a): the Group participants are the seed.
    // Whatever the launch flow seeds, the SESSION snapshot
    // projects the stored Session participants honestly.
    let participants = seed_group(
        group_repo,
        "seed-src",
        GroupStrategy::Chat,
    )
    .await;

    // 1. Inherited seed: session participants copied from the
    //    Group seed keep their scopes.
    let inherited = repo
        .create(
            "seed-src",
            NewSessionParams {
                session_kind: bcs_service_api::SessionKind::Chat,
                participants: participants.clone(),
                created_by: Some("human-creator".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("create inherited session");
    let snapshot = repo
        .read_authorization_snapshot(&inherited.id)
        .await
        .expect("read snapshot")
        .expect("snapshot");
    assert_eq!(snapshot.group_id, "seed-src");
    assert_eq!(snapshot.status, SessionStatus::Running);
    assert_eq!(
        snapshot.session_kind,
        bcs_service_api::SessionKind::Chat
    );
    // created_by is the REAL actor (may be non-Human); here a
    // Human actor id is preserved verbatim.
    assert_eq!(snapshot.created_by.as_deref(), Some("human-creator"));
    assert!(snapshot.live);
    // No join-sequence data yet: the absent semantics is kept.
    assert_eq!(snapshot.participant_join_seq, None);
    assert_eq!(
        snapshot
            .participants
            .iter()
            .find(|p| p.actor_id == "bot-worker")
            .expect("bot participant")
            .role,
        ParticipantRole::Worker
    );
    assert_eq!(
        snapshot
            .participants
            .iter()
            .find(|p| p.actor_id == "human-view")
            .expect("human participant")
            .message_view_scope,
        MessageViewScope::Participant
    );

    // 2. Legal explicit override covers the seed: a created-by
    //    non-Human actor and an explicit Human scope (the seed
    //    said Participant; the creator chose Full).
    let mut overridden = participants.clone();
    if let Some(participant) = overridden
        .iter_mut()
        .find(|participant| participant.bot_uuid == "human-view")
    {
        participant.message_view_scope = MessageViewScope::Full;
    }
    let explicit = repo
        .create(
            "seed-src",
            NewSessionParams {
                session_kind: bcs_service_api::SessionKind::ServiceInvocation,
                participants: overridden,
                created_by: Some("svc-key:0123456789abcdef".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("create explicit session");
    let snapshot = repo
        .read_authorization_snapshot(&explicit.id)
        .await
        .expect("read snapshot")
        .expect("snapshot");
    assert_eq!(
        snapshot.session_kind,
        bcs_service_api::SessionKind::ServiceInvocation
    );
    // created_by is NOT limited to Human actors (spec §8.3).
    assert_eq!(
        snapshot.created_by.as_deref(),
        Some("svc-key:0123456789abcdef")
    );
    assert_eq!(
        snapshot
            .participants
            .iter()
            .find(|p| p.actor_id == "human-view")
            .expect("human participant")
            .message_view_scope,
        MessageViewScope::Full,
        "legal explicit scope COVERS the seed"
    );

    // 3. Missing-seed fallback: a participant the Group had no
    //    seed for initializes with the existing default-Full
    //    scope (the existing participant decode default).
    let without_seed = vec![human_participant(
        "human-late",
        ParticipantRole::Consultant,
        MessageViewScope::Full,
    )];
    let late = repo
        .create(
            "seed-src",
            NewSessionParams {
                session_kind: bcs_service_api::SessionKind::Chat,
                message_visibility_version: 0,
                participants: without_seed,
                ..Default::default()
            },
        )
        .await
        .expect("create late session");
    let snapshot = repo
        .read_authorization_snapshot(&late.id)
        .await
        .expect("read snapshot")
        .expect("snapshot");
    assert_eq!(snapshot.message_visibility_version, 0);
    assert_eq!(
        snapshot
            .participants
            .iter()
            .find(|p| p.actor_id == "human-late")
            .expect("late participant")
            .message_view_scope,
        MessageViewScope::Full
    );

    // After creation the Session evolves INDEPENDENTLY: a
    // Group scope change must NOT overwrite the Session
    // projection, and missing Session members are NOT
    // back-filled via the Group seed.
    group_repo
        .update_participant_message_view_scope(
            "seed-src",
            "human-view",
            MessageViewScope::Full,
        )
        .await
        .expect("group scope change");
    group_repo
        .add_participant(
            "seed-src",
            bot_participant("bot-late-joiner", ParticipantRole::Observer),
        )
        .await
        .expect("group member added late");

    let snapshot = repo
        .read_authorization_snapshot(&inherited.id)
        .await
        .expect("read snapshot")
        .expect("snapshot");
    assert_eq!(
        snapshot
            .participants
            .iter()
            .find(|p| p.actor_id == "human-view")
            .expect("human participant")
            .message_view_scope,
        MessageViewScope::Participant,
        "Session scope is NOT retro-overwritten by the Group"
    );
    assert!(
        !snapshot
            .participants
            .iter()
            .any(|p| p.actor_id == "bot-late-joiner"),
        "a missing Session member is NOT back-filled from the Group seed"
    );

    // The Session's own scope update DOES change its
    // projection with all other actor fields stable
    // (6.4a scope-only update).
    repo.update_participant_message_view_scope(
        &inherited.id,
        "human-view",
        MessageViewScope::Full,
    )
    .await
    .expect("session scope update");
    let after = repo
        .read_authorization_snapshot(&inherited.id)
        .await
        .expect("read snapshot")
        .expect("snapshot");
    let member_after = after
        .participants
        .iter()
        .find(|p| p.actor_id == "human-view")
        .expect("member");
    assert_eq!(member_after.message_view_scope, MessageViewScope::Full);
    assert_eq!(member_after.actor_id, "human-view");
    assert_eq!(member_after.actor_kind, ActorKind::Human);
    assert_eq!(member_after.role, ParticipantRole::Observer);
    assert_eq!(member_after.effective_mode, ParticipantMode::Present);
    // Session members unchanged by the Group edit above.
    assert_eq!(after.participants.len(), snapshot.participants.len());
}

/// Fresh-env read through an INDEPENDENT store instance
/// shows the committed values (independent-instance
/// freshness). Needs the `seed-src` sessions created by
/// [`assert_session_snapshot_facts`].
pub async fn assert_independent_instance_freshness(
    writer: &MySqlSessionStore,
    reader2: &MySqlSessionStore,
) {
    let seeded = writer
        .list_by_group("seed-src", None, 0, 50, None, None)
        .await
        .first()
        .expect("seeded session row")
        .id
        .clone();
    let independent = reader2
        .read_authorization_snapshot(&seeded)
        .await
        .expect("independent read")
        .expect("independent snapshot");
    assert_eq!(independent.env, ENV);
    let via_writer = writer
        .read_authorization_snapshot(&seeded)
        .await
        .expect("writer read")
        .expect("writer snapshot");
    assert_eq!(independent, via_writer);
}

/// Deleted sessions are gone: Ok(None), not a live
/// snapshot.
pub async fn assert_session_lifecycle_projection(
    writer: &dyn SessionRepoPort,
) {
    writer.delete("gone-session").await.expect("delete");
    assert!(
        writer
            .read_authorization_snapshot("gone-session")
            .await
            .expect("read after delete")
            .is_none()
    );
}

pub async fn assert_missing_session_is_none(
    writer: &dyn SessionRepoPort,
    missing_id: &str,
) {
    assert!(
        writer
            .read_authorization_snapshot(missing_id)
            .await
            .expect("missing")
            .is_none()
    );
}

/// Instance-level env binding: sessions created through
/// env-b are invisible through env-a (needs the
/// `groups` group store for the env-b seeding).
pub async fn assert_env_isolation(
    groups_b: &dyn GroupRepoPort,
    writer_env_a: &dyn SessionRepoPort,
    env_b: &MySqlSessionStore,
) {
    groups_b
        .upsert(DomainGroup::new(
            "snap-env-session",
            "bot-driver",
            Vec::new(),
        ))
        .await
        .expect("upsert in b");
    let session_b = env_b
        .create("snap-env-session", NewSessionParams::default())
        .await
        .expect("create in b");
    assert!(
        writer_env_a
            .read_authorization_snapshot(&session_b.id)
            .await
            .expect("env-a read")
            .is_none(),
        "env isolation: env-a sees no env-b session"
    );
}

/// Snapshot reads are strict: a storage failure is
/// `Err` — never None and never a cache fallback
/// (MySqlSessionStore has no display cache, but the
/// strictness is pinned anyway).
pub async fn assert_snapshot_failure_is_strict(
    failing_store: MySqlSessionStore,
) {
    let error = failing_store
        .read_authorization_snapshot("seed-src:any")
        .await
        .err()
        .expect("storage failure is Err");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "snapshot failures are internal errors: {error:?}"
    );
}

pub fn fail_db() -> Arc<dyn DbPlugin> {
    Arc::new(FailingDb)
}

struct FailingDb;

#[async_trait::async_trait]
impl DbPlugin for FailingDb {
    async fn query(&self, _statement: DbStatement) -> Result<Vec<DbRow>, DbError> {
        Err(DbError::Backend("forced session snapshot failure".to_string()))
    }

    async fn execute(&self, _statement: DbStatement) -> Result<DbExecuteResult, DbError> {
        Err(DbError::Backend("forced session snapshot failure".to_string()))
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> Result<Vec<DbTransactionStepResult>, DbError> {
        Err(DbError::Backend("forced session snapshot failure".to_string()))
    }

    async fn health_check(&self) -> Result<DbHealth, DbError> {
        Ok(DbHealth::healthy())
    }
}

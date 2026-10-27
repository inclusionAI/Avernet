//! `BotControlPlaneRepoPort::list_controllable`
//! conformance harness (bot-authority plan Task 4.5,
//! spec §8.0 point 1).
//!
//! The SQL store (`PersistentBotRepo`) and the
//! memory projection (`MemoryBotRepo`) must satisfy
//! the SAME candidate semantics: the approved
//! `owner|manager` union with owner-wins role dedup,
//! byte-exact case/env scoping, revoked-edge and
//! tombstone exclusion, the persistent filters,
//! the stable id order and the legal Human self
//! entry (Owner DISPLAY tag). Assertion failures
//! panic (harness convention: panic on failure is
//! the contract).

#![allow(
    clippy::expect_used,
    reason = "test harness — panic on failure is the contract"
)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::types::bot_authority::BotAccessRelation;
use bcs_service_api::{
    ActorKind, ActorStatus,
    ControllableCandidateQuery, ControlledBotCandidate,
};

/// Storage access the harness uses to prepare/corrupt
/// state. Implementations seed through their own
/// store channel; the candidate read always goes
/// through `control_plane_repo`.
#[async_trait]
pub trait BotControllableContractFixture: Send + Sync {
    fn control_plane_repo(&self) -> Arc<dyn BotControlPlaneRepoPort>;

    /// Seed one LIVE Bot row (actor_kind = 'bot') with the
    /// given name and status in `env`. Idempotent or
    /// exclusive per query — the caller uses distinct ids.
    async fn seed_bot(
        &self,
        env: &str,
        bot_id: &str,
        name: &str,
        status: ActorStatus,
    );

    /// Tombstone a previously seeded Bot row.
    async fn delete_bot(&self, env: &str, bot_id: &str);

    /// Insert (or restore) an approved owner/manager edge
    /// `human_<user_id> → bot` in `env`
    /// (unified shape: `grant_ref_id = 0`, `rules` NULL).
    async fn grant_edge(
        &self,
        env: &str,
        bot_id: &str,
        user_id: &str,
        kind: BotAccessRelation,
    );

    /// Revoke (NOT delete) an approved owner/manager edge.
    async fn revoke_edge(
        &self,
        env: &str,
        bot_id: &str,
        user_id: &str,
        kind: BotAccessRelation,
    );

    /// Seed the Human ACTOR self row
    /// (`bot_uuid = human_<user_id>`,
    /// `actor_kind = 'human'`) in `env`.
    async fn seed_human_self(&self, env: &str, user_id: &str);
}

fn query(
    env: &str,
    user_id: &str,
    kind: Option<ActorKind>,
    name: Option<&str>,
    status: Option<ActorStatus>,
) -> ControllableCandidateQuery {
    ControllableCandidateQuery {
        user_id: user_id.to_string(),
        env: env.to_string(),
        kind,
        name: name.map(str::to_string),
        status,
        // The shared contract pins the strict post-migration
        // baseline (no §10.2a fallback arm); the compat arm
        // has its own scenario coverage at the store/app
        // layers.
        include_legacy_creator: false,
    }
}

/// The FULL shared candidate suite for one bound
/// environment. `env` is the query env of
/// `fixture.control_plane_repo()`; `other_env` must differ.
pub async fn run_bot_controllable_contract(
    fixture: &(impl BotControllableContractFixture + ?Sized),
    env: &str,
    other_env: &str,
) {
    assert_ne!(env, other_env, "harness needs a distinct second env");

    // Both stores seed through the SAME scenarios so the
    // mine-union candidate semantics can never drift.
    owner_manager_union_dedupes_to_owner(fixture, env).await;
    case_and_env_are_exact(fixture, env, other_env).await;
    revoked_edges_and_tombstones_are_absent(fixture, env).await;
    persistent_filters_constrain(fixture, env).await;
    human_self_entry_semantics(fixture, env).await;
    candidates_are_stably_ordered(fixture, env).await;
}

fn relations(
    candidates: &[ControlledBotCandidate],
    bot_id: &str,
) -> Option<BotAccessRelation> {
    candidates
        .iter()
        .find(|candidate| candidate.id == bot_id)
        .map(|candidate| candidate.access_relation)
}

async fn owner_manager_union_dedupes_to_owner(
    fixture: &(impl BotControllableContractFixture + ?Sized),
    env: &str,
) {
    fixture
        .seed_bot(env, "c_owner_both", "owner-both", ActorStatus::Online)
        .await;
    fixture
        .seed_bot(env, "c_manager_only", "manager-only", ActorStatus::Online)
        .await;
    fixture
        .grant_edge(env, "c_owner_both", "union-user", BotAccessRelation::Owner)
        .await;
    fixture
        .grant_edge(
            env,
            "c_owner_both",
            "union-user",
            BotAccessRelation::Manager,
        )
        .await;
    fixture
        .grant_edge(
            env,
            "c_manager_only",
            "union-user",
            BotAccessRelation::Manager,
        )
        .await;

    let candidates = fixture
        .control_plane_repo()
        .list_controllable(query(env, "union-user", None, None, None))
        .await
        .expect("list candidates");
    assert_eq!(
        relations(&candidates, "c_owner_both"),
        Some(BotAccessRelation::Owner),
        "a bot both owned and managed dedupes to Owner"
    );
    assert_eq!(
        relations(&candidates, "c_manager_only"),
        Some(BotAccessRelation::Manager),
        "manager-only keeps the Manager relation"
    );
    // One row per bot — the role dedup collapses the
    // owner|manager union.
    let owner_both = candidates
        .iter()
        .filter(|candidate| candidate.id == "c_owner_both")
        .count();
    assert_eq!(owner_both, 1, "role dedup yields ONE candidate per bot");
}

async fn case_and_env_are_exact(
    fixture: &(impl BotControllableContractFixture + ?Sized),
    env: &str,
    other_env: &str,
) {
    // Case-exact: `union-user` never matches `UNION-USER`
    // (or the bot of another case).
    fixture
        .seed_bot(env, "c_case_bot", "case-bot", ActorStatus::Online)
        .await;
    fixture
        .grant_edge(env, "c_case_bot", "UNION-USER", BotAccessRelation::Owner)
        .await;
    let candidates = fixture
        .control_plane_repo()
        .list_controllable(query(env, "union-user", None, None, None))
        .await
        .expect("list candidates");
    assert!(
        !candidates
            .iter()
            .any(|candidate| candidate.id == "c_case_bot"),
        "case-insensitive user matching must never leak candidates"
    );

    // Env-exact: an edge granted in another env is not a
    // candidate of this env.
    fixture
        .seed_bot(env, "c_env_bot", "env-bot", ActorStatus::Online)
        .await;
    fixture
        .grant_edge(other_env, "c_env_bot", "union-user", BotAccessRelation::Owner)
        .await;
    let candidates = fixture
        .control_plane_repo()
        .list_controllable(query(env, "union-user", None, None, None))
        .await
        .expect("list candidates");
    assert!(
        !candidates
            .iter()
            .any(|candidate| candidate.id == "c_env_bot"),
        "edges of another env never become candidates"
    );
}

async fn revoked_edges_and_tombstones_are_absent(
    fixture: &(impl BotControllableContractFixture + ?Sized),
    env: &str,
) {
    // Residual (revoked) edges never pollute mine
    // (plan §4.7).
    fixture
        .seed_bot(env, "c_revoked", "revoked", ActorStatus::Online)
        .await;
    fixture
        .grant_edge(env, "c_revoked", "residue-user", BotAccessRelation::Owner)
        .await;
    fixture
        .revoke_edge(
            env,
            "c_revoked",
            "residue-user",
            BotAccessRelation::Owner,
        )
        .await;
    let candidates = fixture
        .control_plane_repo()
        .list_controllable(query(env, "residue-user", None, None, None))
        .await
        .expect("list candidates");
    assert!(
        !candidates
            .iter()
            .any(|candidate| candidate.id == "c_revoked"),
        "a revoked edge is not a mine candidate"
    );

    // Tombstoned bots authorize nobody.
    fixture
        .seed_bot(env, "c_dead", "dead", ActorStatus::Online)
        .await;
    fixture
        .grant_edge(env, "c_dead", "residue-user", BotAccessRelation::Owner)
        .await;
    fixture.delete_bot(env, "c_dead").await;
    let candidates = fixture
        .control_plane_repo()
        .list_controllable(query(env, "residue-user", None, None, None))
        .await
        .expect("list candidates");
    assert!(
        !candidates
            .iter()
            .any(|candidate| candidate.id == "c_dead"),
        "a tombstoned bot is not a mine candidate"
    );
}

async fn persistent_filters_constrain(
    fixture: &(impl BotControllableContractFixture + ?Sized),
    env: &str,
) {
    fixture
        .seed_bot(env, "c_alpha", "Alpha One", ActorStatus::Online)
        .await;
    fixture
        .seed_bot(env, "c_hidden", "Hidden One", ActorStatus::Hidden)
        .await;
    for bot_id in ["c_alpha", "c_hidden"] {
        fixture
            .grant_edge(env, bot_id, "filter-user", BotAccessRelation::Owner)
            .await;
    }

    // Omitted filters do not constrain.
    let all = fixture
        .control_plane_repo()
        .list_controllable(query(env, "filter-user", None, None, None))
        .await
        .expect("list candidates");
    assert!(all.iter().any(|candidate| candidate.id == "c_alpha"));
    assert!(all.iter().any(|candidate| candidate.id == "c_hidden"));

    let online = fixture
        .control_plane_repo()
        .list_controllable(query(
            env,
            "filter-user",
            None,
            None,
            Some(ActorStatus::Online),
        ))
        .await
        .expect("list candidates");
    assert!(online.iter().any(|candidate| candidate.id == "c_alpha"));
    assert!(
        !online.iter().any(|candidate| candidate.id == "c_hidden"),
        "the status filter is a persistent condition"
    );

    let named = fixture
        .control_plane_repo()
        .list_controllable(query(
            env,
            "filter-user",
            None,
            Some("alpha"),
            None,
        ))
        .await
        .expect("list candidates");
    assert!(named.iter().any(|candidate| candidate.id == "c_alpha"));
    assert!(
        !named.iter().any(|candidate| candidate.id == "c_hidden"),
        "the name filter is a persistent condition"
    );
}

async fn human_self_entry_semantics(
    fixture: &(impl BotControllableContractFixture + ?Sized),
    env: &str,
) {
    fixture.seed_human_self(env, "self-user").await;
    // NO Human→Human owner edge exists for the self row;
    // the self entry still shows with the Owner display
    // tag (spec §8.0 point 3).
    let candidates = fixture
        .control_plane_repo()
        .list_controllable(query(env, "self-user", None, None, None))
        .await
        .expect("list candidates");
    assert_eq!(
        relations(&candidates, "human_self-user"),
        Some(BotAccessRelation::Owner),
        "the Human self entry shows the Owner display tag"
    );

    let bots_only = fixture
        .control_plane_repo()
        .list_controllable(query(
            env,
            "self-user",
            Some(ActorKind::Bot),
            None,
            None,
        ))
        .await
        .expect("list candidates");
    assert!(
        !bots_only
            .iter()
            .any(|candidate| candidate.id == "human_self-user"),
        "the kind=Bot filter removes the Human self entry"
    );
}

async fn candidates_are_stably_ordered(
    fixture: &(impl BotControllableContractFixture + ?Sized),
    env: &str,
) {
    for index in ["z-cand", "a-cand", "m-cand"] {
        fixture
            .seed_bot(env, index, index, ActorStatus::Online)
            .await;
        fixture
            .grant_edge(env, index, "order-user", BotAccessRelation::Owner)
            .await;
    }
    let first = fixture
        .control_plane_repo()
        .list_controllable(query(env, "order-user", None, None, None))
        .await
        .expect("list candidates");
    let second = fixture
        .control_plane_repo()
        .list_controllable(query(env, "order-user", None, None, None))
        .await
        .expect("list candidates");
    let ids = |candidates: &[ControlledBotCandidate]| {
        candidates
            .iter()
            .map(|candidate| candidate.id.clone())
            .collect::<Vec<_>>()
    };
    assert_eq!(
        ids(&first),
        vec![
            "a-cand".to_string(),
            "m-cand".to_string(),
            "z-cand".to_string()
        ],
        "candidates are stably ordered by id"
    );
    assert_eq!(ids(&first), ids(&second), "the order is repeatable");
}

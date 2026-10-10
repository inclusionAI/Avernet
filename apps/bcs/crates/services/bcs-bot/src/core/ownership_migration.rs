//! Historical ownership migration Core implementation (plan Task 17,
//! spec §16.1).
//!
//! The Core holds ONLY port references: the Bot repo owns the candidate
//! scan, the per-Bot migration facts, the batched Task 5 initialization and
//! the batch recovery ledger; the relation repo owns the legacy creator
//! claims. Every classification (ready/conflict/skip) is re-derived from a
//! CURRENT read — a candidate snapshot, a confirm-file entry or a legacy
//! creator value can never authorize anything by themselves (spec
//! §16.1.2–16.1.4). The only path to an owner edge is the governed Task 5
//! lane with a fresh `Ready` classification.

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::core::ownership_migration::OwnershipMigrationCoreService;
use bcs_service_api::core::error::ServiceResult;
use bcs_service_api::port::repo::bot_authority::user_id_from_actor;
use bcs_service_api::port::repo::{BotRepoPort, RelationRepoPort};
use bcs_service_api::types::bot_authority::{
    LegacyCreatorClaim, MIGRATION_MAX_BATCH_SIZE, MIGRATION_SYSTEM_ACTOR,
    OwnershipCandidate, OwnershipCandidatePage, OwnershipMigrationBotOutcome,
    OwnershipMigrationBotState, OwnershipMigrationReason, OwnershipMigrationReport,
};
use bcs_service_api::types::bot_authority::OwnershipInitialization;
use bcs_service_api::types::AuditActor;

/// Governed backfill Core of the one-shot authority cutover.
pub struct OwnershipMigrationCore {
    bots: Arc<dyn BotRepoPort>,
    relations: Arc<dyn RelationRepoPort>,
}

impl OwnershipMigrationCore {
    /// Assemble the migration Core over the same-datasource repos the
    /// composition root selects (the wiring never mixes datasources).
    pub fn new(bots: Arc<dyn BotRepoPort>, relations: Arc<dyn RelationRepoPort>) -> Self {
        Self { bots, relations }
    }

    /// The env every store of this assembly reads in. Kept in one place so
    /// the claim cross-check and the scans can never diverge on scoping.
    fn env() -> String {
        bcs_config::resolve_env_str()
    }

    /// Current owner edge claimants as bare `Option<&str>` user ids (the D11
    /// `human_<user_id>` prefix mapping); `None` keeps an illegible claim
    /// visible so the classifier can fail it closed instead of silently
    /// ``decoding'' a corrupt row into a user.
    fn owner_user_ids(state: &OwnershipMigrationBotState) -> Vec<Option<String>> {
        state
            .owner_edge_claimants
            .iter()
            .map(|claimant| user_id_from_actor(claimant).map(str::to_string))
            .collect()
    }

    /// The machine classification of one CURRENT bot state: the single
    /// decision point every path (dry-run page and execution
    /// re-verification) shares. Precedence is fixed and documented; every
    /// non-`Ready` outcome excludes the Bot from initialization without
    /// touching its authority state.
    ///
    /// Precedence:
    /// 1. missing/deleted row → [`BotNotLive`] (initialization-era anchor);
    /// 2. Human self row → [`HumanRowExcluded`] (never migrate Human rows);
    /// 3. version 0:
    ///    a. an approved owner edge on an uninitialized row is corrupt →
    ///       [`AuthorityInconsistent`];
    ///    b. absent/blank `created_by` (bare runtime) → [`MissingCreator`];
    ///    c. multiple distinct legacy creator claims, or a single claim
    ///       contradicting `created_by` → [`ConflictingCreators`];
    ///    d. no live Human actor row for the creator → [`MissingHuman`];
    ///    e. otherwise [`Ready`] with `created_by` as candidate owner;
    /// 4. version > 0 (already initialized — verified and skipped on rerun):
    ///    a. not exactly one legible owner edge → [`AuthorityInconsistent`];
    ///    b. the owner edge diverges from `created_by` (transfer happened
    ///       after the initialization) → [`OwnershipTransferred`];
    ///    c. otherwise [`AlreadyInitialized`].
    fn classify(state: &OwnershipMigrationBotState, claims: &[LegacyCreatorClaim]) -> OwnershipMigrationReason {
        if !state.live {
            return OwnershipMigrationReason::BotNotLive;
        }
        if !state.physical {
            return OwnershipMigrationReason::HumanRowExcluded;
        }
        let owner_ids = Self::owner_user_ids(state);
        if state.ownership_version == 0 {
            // An approved owner edge on an uninitialized row is a corrupt
            // authority shape: never auto-merge, never auto-claim.
            if !state.owner_edge_claimants.is_empty() {
                return OwnershipMigrationReason::AuthorityInconsistent;
            }
            let Some(created_by) = state.created_by.as_deref() else {
                // Bare runtime ownership: no suffix/relation/stat re-derivation.
                return OwnershipMigrationReason::MissingCreator;
            };
            // Cross-check the legacy creator relation: distinct claimant
            // values (human user ids; an illegible claim keeps its raw text
            // so it can never match a clean created_by). An absent relation
            // is legitimate — `created_by` is the candidate source, claims
            // only corroborate.
            let mut distinct: Vec<String> = Vec::new();
            for claim in claims.iter().filter(|claim| claim.bot_id == state.bot_id) {
                let value = user_id_from_actor(&claim.claimant_actor_id)
                    .map(str::to_string)
                    .unwrap_or_else(|| claim.claimant_actor_id.clone());
                if !distinct.contains(&value) {
                    distinct.push(value);
                }
            }
            if distinct.len() > 1 {
                return OwnershipMigrationReason::ConflictingCreators;
            }
            if distinct.len() == 1 && distinct[0] != created_by {
                return OwnershipMigrationReason::ConflictingCreators;
            }
            if !state.creator_human_live {
                return OwnershipMigrationReason::MissingHuman;
            }
            OwnershipMigrationReason::Ready
        } else {
            match owner_ids.as_slice() {
                [] => OwnershipMigrationReason::AuthorityInconsistent,
                [single] => match single.as_deref() {
                    Some(owner) if state.created_by.as_deref() == Some(owner) => {
                        OwnershipMigrationReason::AlreadyInitialized
                    }
                    // The current owner is not created_by: the authority was
                    // transferred after an earlier initialization — skip and
                    // never reset it.
                    _ => OwnershipMigrationReason::OwnershipTransferred,
                },
                _ => OwnershipMigrationReason::AuthorityInconsistent,
            }
        }
    }

    /// One outcome row.
    fn outcome(bot_id: &str, reason: OwnershipMigrationReason, detail: Option<&str>) -> OwnershipMigrationBotOutcome {
        OwnershipMigrationBotOutcome {
            bot_id: bot_id.to_string(),
            reason,
            detail: detail.map(str::to_string),
        }
    }

    /// The commit attempted for a fresh `Ready` candidate: the Task 5
    /// governed initialization, System-actored and grouped under the batch's
    /// operation id, with the batch tagged on the initialization ledger.
    fn initialization(created_by: &str, batch_id: &str) -> OwnershipInitialization {
        OwnershipInitialization {
            owner_user_id: created_by.to_string(),
            actor: AuditActor::System {
                name: MIGRATION_SYSTEM_ACTOR.to_string(),
            },
            operation_id: format!("{MIGRATION_SYSTEM_ACTOR}:{batch_id}"),
        }
    }

    /// Claim cross-check rows for a bounded id list (one batched read per
    /// page/execution set, never per Bot).
    async fn creator_claims(
        &self,
        env: &str,
        bot_ids: &[String],
    ) -> ServiceResult<BTreeMap<String, Vec<LegacyCreatorClaim>>> {
        let claims = self.relations.list_creator_claims(env, bot_ids).await?;
        let mut grouped: BTreeMap<String, Vec<LegacyCreatorClaim>> = BTreeMap::new();
        for claim in claims {
            grouped.entry(claim.bot_id.clone()).or_default().push(claim);
        }
        Ok(grouped)
    }
}

#[async_trait]
impl OwnershipMigrationCoreService for OwnershipMigrationCore {
    async fn inspect_batch(
        &self,
        after_bot_id: Option<String>,
        limit: u32,
    ) -> ServiceResult<OwnershipCandidatePage> {
        if limit == 0 || limit as usize > MIGRATION_MAX_BATCH_SIZE {
            return Err(invalid_migration_page(limit));
        }
        let env = Self::env();
        let states = self.bots.list_migration_candidates(after_bot_id.as_deref(), limit).await?;
        let bot_ids: Vec<String> = states.iter().map(|state| state.bot_id.clone()).collect();
        let claims = self.creator_claims(&env, &bot_ids).await?;
        let candidates = states
            .iter()
            .map(|state| {
                let reason = Self::classify(state, claims.get(&state.bot_id).map(Vec::as_slice).unwrap_or(&[]));
                OwnershipCandidate {
                    bot_id: state.bot_id.clone(),
                    env: state.env.clone(),
                    candidate_user_id: state.created_by.clone(),
                    reason,
                }
            })
            .collect();
        // Keyset continuation: while the scan returned a full page more rows
        // may follow; a short page ends the walk.
        let next_bot_id = if states.len() == limit as usize {
            states.last().map(|state| state.bot_id.clone())
        } else {
            None
        };
        Ok(OwnershipCandidatePage {
            candidates,
            next_bot_id,
        })
    }

    async fn initialize_batch(
        &self,
        confirmed_candidate_ids: Vec<String>,
        batch_id: String,
    ) -> ServiceResult<OwnershipMigrationReport> {
        if batch_id.trim().is_empty() {
            return Err(invalid_migration_batch_id());
        }
        if confirmed_candidate_ids.len() > MIGRATION_MAX_BATCH_SIZE {
            return Err(invalid_migration_batch_len(&confirmed_candidate_ids));
        }
        // Anchor semantics on a page-shaped repeat set: preserve the
        // operator's confirmation order and account each Bot once.
        let mut ordered: Vec<String> = Vec::with_capacity(confirmed_candidate_ids.len());
        let mut seen: HashSet<&str> = HashSet::new();
        for id in &confirmed_candidate_ids {
            if id.trim().is_empty() {
                return Err(invalid_migration_batch_len(&confirmed_candidate_ids));
            }
            if seen.insert(id.as_str()) {
                ordered.push(id.clone());
            }
        }
        let env = Self::env();
        let claims = self.creator_claims(&env, &ordered).await?;
        // Recovery read BEFORE any execution: rows this batch already
        // committed are reported as initialized (the replay of the original
        // counts) without re-executing.
        let committed = self.bots.list_batch_initializations(&batch_id).await?;
        let committed_ids: HashSet<String> = committed.iter().map(|row| row.bot_id.clone()).collect();

        let mut initialized = Vec::new();
        let mut skipped = Vec::new();
        let mut conflicted = Vec::new();
        let mut failed = Vec::new();
        for bot_id in &ordered {
            if committed_ids.contains(bot_id) {
                let entry = Self::outcome(bot_id, OwnershipMigrationReason::Ready, Some("batch_replay"));
                initialized.push(entry);
                continue;
            }
            let Some(state) = self.bots.migration_bot_state(bot_id).await? else {
                skipped.push(Self::outcome(bot_id, OwnershipMigrationReason::BotNotLive, None));
                continue;
            };
            let state_claims = claims.get(bot_id).map(Vec::as_slice).unwrap_or(&[]);
            match Self::classify(&state, state_claims) {
                OwnershipMigrationReason::Ready => {
                    // A CURRENT freshly-verified candidate: the Task 5 lane
                    // re-proves version 0 and slot emptiness inside the
                    // write transaction (CAS 0 -> 1), so a candidate that
                    // raced stale simply lands in its business branch.
                    let init = Self::initialization(
                        state.created_by.as_deref().unwrap_or_default(),
                        batch_id.trim(),
                    );
                    match self
                        .bots
                        .initialize_existing_ownership_in_batch(bot_id, init, batch_id.trim().to_string())
                        .await
                    {
                        Ok(_) => initialized.push(Self::outcome(bot_id, OwnershipMigrationReason::Ready, None)),
                        Err(bcs_service_api::ServiceError::BotNotFound(_)) => {
                            skipped.push(Self::outcome(bot_id, OwnershipMigrationReason::BotNotLive, None));
                        }
                        Err(bcs_service_api::ServiceError::Authority(
                            bcs_service_api::types::error::AuthorityError::Conflict(_),
                        )) => {
                            // Someone initialized (or transferred) between the
                            // re-verification read and the CAS: re-read and
                            // classify the settled state; never re-claim.
                            let settled = self.bots.migration_bot_state(bot_id).await?;
                            let reason = settled
                                .as_ref()
                                .map(|state| Self::classify(state, state_claims))
                                .unwrap_or(OwnershipMigrationReason::BotNotLive);
                            match reason {
                                OwnershipMigrationReason::BotNotLive
                                | OwnershipMigrationReason::HumanRowExcluded
                                | OwnershipMigrationReason::OwnershipTransferred
                                | OwnershipMigrationReason::AlreadyInitialized => {
                                    skipped.push(Self::outcome(bot_id, reason, Some("raced_initialization")));
                                }
                                conflict => {
                                    conflicted.push(Self::outcome(bot_id, conflict, Some("raced_initialization")));
                                }
                            }
                        }
                        Err(_) => {
                            // Per-bot storage failure: the batch continues so
                            // the committed prefix stays recoverable by
                            // replaying the same batch id; the command exits
                            // nonzero because `failed` is non-empty. No
                            // driver text reaches the report.
                            failed.push(Self::outcome(bot_id, OwnershipMigrationReason::StorageFailure, None));
                        }
                    }
                }
                reason @ (OwnershipMigrationReason::BotNotLive
                | OwnershipMigrationReason::HumanRowExcluded
                | OwnershipMigrationReason::AlreadyInitialized
                | OwnershipMigrationReason::OwnershipTransferred) => {
                    skipped.push(Self::outcome(bot_id, reason, None));
                }
                conflict => {
                    conflicted.push(Self::outcome(bot_id, conflict, None));
                }
            }
        }
        Ok(OwnershipMigrationReport {
            batch_id: batch_id.trim().to_string(),
            initialized,
            skipped,
            conflicted,
            failed,
        })
    }
}

fn invalid_migration_page(limit: u32) -> bcs_service_api::ServiceError {
    bcs_service_api::ServiceError::InvalidOperation {
        message: format!(
            "ownership migration pages are bounded to 1..={MIGRATION_MAX_BATCH_SIZE}; got {limit}"
        ),
        request_id: None,
    }
}

fn invalid_migration_batch_id() -> bcs_service_api::ServiceError {
    bcs_service_api::ServiceError::InvalidOperation {
        message: "ownership migration requires a non-blank batch id".into(),
        request_id: None,
    }
}

fn invalid_migration_batch_len(ids: &[String]) -> bcs_service_api::ServiceError {
    bcs_service_api::ServiceError::InvalidOperation {
        message: format!(
            "ownership migration batches carry at most {MIGRATION_MAX_BATCH_SIZE} confirmed candidates; got {}",
            ids.len()
        ),
        request_id: None,
    }
}
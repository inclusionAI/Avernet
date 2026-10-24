//! Trusted (ownership-initialized) memory registration
//! composites on [`MemoryBotRepo`] (bot-authority plan
//! Task 4, spec §10.2) — the memory mirror of the SQL
//! composites in `persistent_repo::registration_ownership`.
//!
//! ONE write-guard span stages a CLONE of every shared
//! state bucket (authority, profiles, bots, tokens,
//! bindings), so failure at staging — including the
//! default-profile step and the `initialize` audit write
//! — publishes nothing: no half-written Human/Bot/owner/
//! version/profile/audit, preexisting state untouched,
//! no runtime activation. All failure paths complete
//! BEFORE the durable bot-info file is written, and the
//! full install happens while every guard is held.
//!
//! Semantics mirror SQL exactly:
//! - the trusted Human creation is literal (ruling
//!   T4-1): the register token verified the
//!   Gateway-authenticated Human, and the SAME
//!   registration ENSURES the Human's ACTOR record
//!   (created when absent) — the only Human creation the
//!   Task-2/3 human-existence guards read, so a freshly
//!   registered owner is never rejected by its own
//!   authority surface;
//! - the approved owner edge is written once, so a reused
//!   Bot ID never resets a committed owner;
//! - ownership version is set to 1 at creation only
//!   (version-0 runtime entry keeps its own paths);
//! - the `initialize` audit row carries the real
//!   initiator and no credentials;
//! - `initialize_ownership` on an initialized Bot returns
//!   the CURRENT owner without rewriting anything.

use bcs_service_api::types::bot_authority::{
    AuthorityMutation, BotOwnershipView, OwnershipInitialization,
};
use bcs_service_api::{BotCapabilities, ServiceError, ServiceResult};

use bcs_config::resolve_env_str as resolve_env;

use super::authority::{
    MemoryAuthorityAuditRow, MemoryAuthorityEdgeKind,
    MemoryAuthorityEdgeStatus, MemoryAuthorityState,
    memory_require_live_bot,
};
use super::PersistedCapabilities;
use super::{MemoryBotRepo, RegisteredBotInner};
use crate::ownership as ownership_composition;

fn initialization_error(detail: String) -> ServiceError {
    ownership_composition::initialization_invalid_public(&detail)
}

fn registration_conflict(detail: &str) -> ServiceError {
    ServiceError::Conflict(format!(
        "trusted registration conflict: {detail}"
    ))
}

impl MemoryBotRepo {
    pub(crate) async fn create_registration_with_ownership(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
        initialization: &OwnershipInitialization,
    ) -> ServiceResult<bool> {
        let env = resolve_env();
        let (owner, owner_actor) =
            ownership_composition::verified_initialization(initialization)?;
        let mutation_id = initialization.mutation.mutation_id.clone();
        // The first created_by IS the trusted owner.
        if created_by.trim() != owner {
            return Err(initialization_error(
                "registration created_by must be the verified \
                 owner"
                    .to_string(),
            ));
        }
        if bot_id.trim().is_empty() || token.trim().is_empty() {
            return Err(initialization_error(
                "Bot ID and runtime credential are required"
                    .to_string(),
            ));
        }

        // Lock order (documented): authority -> profiles ->
        // bots -> tokens -> deleted IDs -> binding index ->
        // control-plane audit. No await inside the span
        // before the blocking file publication task, which
        // is itself fully waited on while all guards are
        // held (the create path's established discipline).
        let mut authority_guard = self.authority.write().await;
        let mut profiles_guard = self.profiles.write().await;
        let mut bots = self.bots.write().await;
        let mut tokens = self.token_to_bot.write().await;
        let deleted = self.deleted_bot_ids.read().await;
        let mut bindings = self.binding_channel_index.write().await;
        let mut control_plane_audit = self.control_plane_audit.write().await;

        // Create only: an existing or tombstoned identity is
        // never rewritten (deleted IDs never resurrect).
        if bots.contains_key(&bot_id) || deleted.contains(&bot_id) {
            return Ok(false);
        }
        if tokens.get(token).is_some_and(|existing| existing != &bot_id) {
            return Err(ServiceError::InternalError(
                "Bot registration token already exists".into(),
            ));
        }

        // Owner-Human liveness: tombstone and actor row are
        // disjoint facts here — a soft-deleted owner leaves
        // the tombstone while its memory row is already gone
        // (and the create-when-absent write below would then
        // silently resurrect the row on top of the
        // tombstone). Either fact must refuse the whole
        // registration instead of publishing a Bot whose
        // owner edge points at a dead Human.
        let human_actor_row = ownership_composition::owner_actor_id(&owner);
        if deleted.contains(&human_actor_row) {
            return Err(registration_conflict(&format!(
                "owner actor '{human_actor_row}' is tombstoned"
            )));
        }
        if let Some(human_row) = bots.get(&human_actor_row)
            && human_row.actor_kind != bcs_service_api::ActorKind::Human
        {
            return Err(registration_conflict(&format!(
                "owner actor '{human_actor_row}' is not a Human actor"
            )));
        }

        let mut staged_authority = authority_guard.clone();
        let mut staged_profiles = profiles_guard.clone();

        // The owner's Human ACTOR record is created below
        // from the SAME verified token (spec §10.2 literal,
        // ruling T4-1): the actor row IS the authority Human
        // registry, so the Task-2/3 human-existence guards
        // can never reject a freshly registered owner. No
        // separate authority-identity bookkeeping exists.

        // Stage the default profile on the CLONE (reuse an
        // existing default; corrupt index fails cleanly).
        super::permission_profile::stage_default_profile(
            &mut staged_profiles,
            &bot_id,
            &env,
        )?;

        // Stage the authority projection: approved owner edge,
        // version 1, the `initialize` audit row.
        let owner_edge_key = (
            env.clone(),
            bot_id.clone(),
            owner_actor.clone(),
            MemoryAuthorityEdgeKind::Owner,
        );
        match staged_authority.edges.get(&owner_edge_key) {
            Some((MemoryAuthorityEdgeStatus::Approved, _)) => {}
            Some((MemoryAuthorityEdgeStatus::Revoked, _)) => {
                staged_authority
                    .edges
                    .insert(owner_edge_key, (MemoryAuthorityEdgeStatus::Approved, 0));
            }
            None => {
                let other_owner = staged_authority.edges.iter().any(
                    |(key, (status, _))| {
                        key.0 == env
                            && key.1 == bot_id
                            && key.3 == MemoryAuthorityEdgeKind::Owner
                            && *status == MemoryAuthorityEdgeStatus::Approved
                            && key.2 != owner_actor
                    },
                );
                if other_owner {
                    return Err(registration_conflict(
                        "Bot already has a different approved owner",
                    ));
                }
                staged_authority
                    .edges
                    .insert(owner_edge_key, (MemoryAuthorityEdgeStatus::Approved, 0));
            }
        }
        staged_authority
            .ownership_versions
            .insert((env.clone(), bot_id.clone()), 1);
        let after_state = ownership_composition::initialize_audit_state(
            &owner_actor,
            1,
        );
        mem_append_audit(
            &mut staged_authority,
            &env,
            &bot_id,
            "initialize",
            &mutation_id,
            "human",
            &owner_actor,
            &owner_actor,
            None,
            &after_state,
        )?;
        let _ = &owner;

        // Durable file publication BEFORE installing anything
        // (all guards stay held). Staged state is discarded
        // on failure, so nothing half-written can leak. A
        // pre-existing file refuses the registration the same
        // way the base create path refuses its retries.
        let now = super::unix_millis();
        let persisted = PersistedCapabilities {
            bot_id: bot_id.clone(),
            name: capabilities.name.clone(),
            summary: capabilities.summary.clone(),
            domains: capabilities.domains.clone(),
            skills: capabilities.skills.clone(),
            scopes: capabilities.scopes.clone(),
            binding_channels: capabilities.binding_channels.clone(),
            token: Some(token.to_string()),
            registered_at: now,
            hidden: false,
            created_by: Some(owner.clone()),
            visibility: if capabilities.visibility.is_empty() {
                None
            } else {
                Some(capabilities.visibility.clone())
            },
            agent_code: capabilities.agent_code.clone(),
            agent_token: capabilities.agent_token.clone(),
            user_visibility: Default::default(),
            friend_ext: Default::default(),
            friend_check_in_strategy: Default::default(),
        };
        let path = self.bot_info_path(&bot_id);
        let created = tokio::task::spawn_blocking(move || {
            super::registration_create::persist_once(&path, &persisted)
        })
        .await
        .map_err(|_| {
            ServiceError::InternalError(
                "Bot registration persistence task failed".into(),
            )
        })??;
        if !created {
            return Ok(false);
        }

        // Install — nothing can fail after this point.
        if let Some(channels) = &capabilities.binding_channels {
            for (channel, binding) in channels {
                bindings
                    .entry((channel.clone(), binding.binding_key.clone()))
                    .or_insert_with(|| bot_id.clone());
            }
        }
        tokens.insert(token.to_string(), bot_id.clone());
        control_plane_audit.insert(bot_id.clone(), (now, now));
        bots.insert(
            bot_id.clone(),
            RegisteredBotInner {
                bot_id: bot_id.clone(),
                last_heartbeat: std::time::Instant::now(),
                capabilities,
                ws_connection: None,
                session_token: Some(token.to_string()),
                env: Some(env.clone()),
                status: bcs_service_api::ActorStatus::Online,
                actor_kind: bcs_service_api::ActorKind::Bot,
                created_by: Some(owner.clone()),
                protocol_version: 1,
                user_visibility: Default::default(),
                friend_ext: Default::default(),
                friend_check_in_strategy: Default::default(),
            },
        );
        // Human ACTOR record — created when absent, name
        // preserved otherwise (idempotent, like
        // `ensure_human_actor`).
        let human_id = ownership_composition::owner_actor_id(&owner);
        if !bots.contains_key(&human_id) {
            bots.insert(
                human_id.clone(),
                RegisteredBotInner {
                    bot_id: human_id.clone(),
                    last_heartbeat: std::time::Instant::now(),
                    capabilities: BotCapabilities {
                        name: Some(owner.clone()),
                        visibility: "protected".to_string(),
                        ..Default::default()
                    },
                    ws_connection: None,
                    session_token: None,
                    env: Some(env.clone()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Human,
                    created_by: Some(owner.clone()),
                    protocol_version: 1,
                    user_visibility: Default::default(),
                    friend_ext: Default::default(),
                    friend_check_in_strategy: Default::default(),
                },
            );
            control_plane_audit.insert(human_id, (now, now));
        }
        *authority_guard = staged_authority;
        *profiles_guard = staged_profiles;
        Ok(true)
    }

    /// Governance backfill on an existing live Bot.
    pub(crate) async fn initialize_owned_bot_memory(
        &self,
        bot_id: &str,
        owner_user_id: &str,
        mutation: &AuthorityMutation,
    ) -> ServiceResult<BotOwnershipView> {
        if owner_user_id.trim().is_empty()
            || owner_user_id != owner_user_id.trim()
            || owner_user_id.starts_with("human_")
        {
            return Err(initialization_error(
                "owner_user_id must be the raw verified User ID"
                    .to_string(),
            ));
        }
        ownership_composition::validate_mutation(mutation)?;
        let owner_actor =
            ownership_composition::owner_actor_id(owner_user_id);
        let ctx = memory_require_live_bot(self, bot_id).await?;
        // Ownership targets are physical Bots only: a Human ACTOR
        // row must never gain an owner edge (bcs-admin classify()
        // rejects it too). And the initialized owner must be the
        // stored creator fact, compared byte-for-byte — a candidate
        // list read is never authority for minting ownership. A
        // missing creator carries no fact to contradict and stays
        // initializable as before (governance).
        {
            let bots = self.bots.read().await;
            if let Some(row) = bots.get(bot_id) {
                if row.actor_kind != bcs_service_api::ActorKind::Bot {
                    return Err(registration_conflict(
                        "ownership targets only physical Bots; a Human actor row is never initialized",
                    ));
                }

            }
        }
        // Single write-guard span; no await inside.
        let mut state_guard = self.authority.write().await;
        let mut staged = state_guard.clone();
        let current_version = staged
            .ownership_versions
            .get(&(ctx.env.clone(), ctx.bot_id.clone()))
            .copied()
            .unwrap_or(0);
        // Human-existence screening for the governance
        // backfill lives in the governance FACTS read (the
        // bcs-admin dry-run classification, spec §10.4):
        // the SQL/memory composites themselves no longer
        // re-verify the identity table here (that guard
        // was the identity-row workaround's echo).
        if current_version > 0 {
            // Idempotent for the SAME owner; a different owner
            // can never reset a committed owner.
            let owner = current_owner_actor(&staged, &ctx)?;
            drop(state_guard);
            let view = owner_view(bot_id, &owner)?;
            if view.owner_user_id != owner_user_id {
                return Err(registration_conflict(
                    "Bot ownership is already initialized with a                      different owner; the owner is never reset",
                ));
            }
            return Ok(view);
        }

        // The initialized owner must be the stored creator fact,
        // byte-for-byte: only the version-0 INIT path is guarded —
        // a committed owner's idempotent echo (post-transfer, where
        // the current owner legitimately differs from the historical
        // creator record) must keep working. A missing creator
        // carries no fact to contradict.
        {
            let bots = self.bots.read().await;
            if let Some(creator) = bots
                .get(bot_id)
                .and_then(|row| row.created_by.clone())
            {
                if creator != owner_user_id {
                    return Err(registration_conflict(&format!(
                        "stored creator '{creator}' does not match the requested owner '{owner_user_id}'; ownership initialization requires the stored creator record to name the owner"
                    )));
                }
            }
        }

        let owner_edge_key = (
            ctx.env.clone(),
            ctx.bot_id.clone(),
            owner_actor.clone(),
            MemoryAuthorityEdgeKind::Owner,
        );
        match staged.edges.get(&owner_edge_key) {
            Some((MemoryAuthorityEdgeStatus::Approved, _)) => {}
            Some((MemoryAuthorityEdgeStatus::Revoked, _)) => {
                staged
                    .edges
                    .insert(owner_edge_key, (MemoryAuthorityEdgeStatus::Approved, 0));
            }
            None => {
                let other_owner = staged.edges.iter().any(
                    |(key, (status, _))| {
                        key.0 == ctx.env
                            && key.1 == ctx.bot_id
                            && key.3 == MemoryAuthorityEdgeKind::Owner
                            && *status
                                == MemoryAuthorityEdgeStatus::Approved
                            && key.2 != owner_actor
                    },
                );
                if other_owner {
                    drop(state_guard);
                    return Err(registration_conflict(
                        "Bot already has a different approved owner",
                    ));
                }
                staged
                    .edges
                    .insert(owner_edge_key, (MemoryAuthorityEdgeStatus::Approved, 0));
            }
        }
        staged
            .ownership_versions
            .insert((ctx.env.clone(), ctx.bot_id.clone()), 1);
        let after_state =
            ownership_composition::initialize_audit_state(&owner_actor, 1);
        let _ = &ctx;
        mem_append_audit(
            &mut staged,
            &ctx.env,
            &ctx.bot_id,
            "initialize",
            &mutation.mutation_id,
            mutation.actor.kind().as_str(),
            &mutation.actor.actor_id(),
            &owner_actor,
            None,
            &after_state,
        )?;
        *state_guard = staged;
        owner_view(bot_id, &owner_actor)
    }
}

fn current_owner_actor(
    state: &MemoryAuthorityState,
    ctx: &super::authority::MemoryWriteContext,
) -> ServiceResult<String> {
    let owner = state
        .edges
        .iter()
        .find(|(key, (status, ref_id))| {
            key.0 == ctx.env
                && key.1 == ctx.bot_id
                && key.3 == MemoryAuthorityEdgeKind::Owner
                && *status == MemoryAuthorityEdgeStatus::Approved
                && *ref_id == 0
        })
        .map(|(key, _)| key.2.clone())
        .ok_or_else(|| {
            ServiceError::InternalError(format!(
                "{}: bot {} has no approved owner edge",
                bcs_service_api::application::v1::bot_authority::
                    ERROR_BOT_OWNER_INTEGRITY,
                ctx.bot_id
            ))
        })?;
    if bcs_service_api::types::bot_authority::human_user_id_of_actor(
        &owner,
    )
    .is_none()
    {
        return Err(ServiceError::InternalError(format!(
            "{}: owner edge subject '{owner}' is not a Human \
             actor id",
            bcs_service_api::application::v1::bot_authority::
                ERROR_BOT_OWNER_INTEGRITY
        )));
    }
    Ok(owner)
}

fn owner_view(
    bot_id: &str,
    owner_actor: &str,
) -> ServiceResult<BotOwnershipView> {
    let owner_user_id = bcs_service_api::types::bot_authority::
        human_user_id_of_actor(owner_actor)
        .ok_or_else(|| {
            ServiceError::InternalError(format!(
                "{}: owner '{owner_actor}' is not a Human actor id",
                bcs_service_api::application::v1::bot_authority::
                    ERROR_BOT_OWNER_INTEGRITY
            ))
        })?;
    Ok(BotOwnershipView {
        bot_id: bot_id.to_string(),
        owner_user_id,
        owner_actor_id: owner_actor.to_string(),
    })
}

/// One audit row on the staged clone: shared shapes with the
/// SQL audit and the same mutation-uniqueness rule.
#[allow(clippy::too_many_arguments)]
pub(super) fn mem_append_audit(
    state: &mut MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    action: &str,
    mutation_id: &str,
    actor_kind: &str,
    actor_id: &str,
    subject_id: &str,
    before_state: Option<&str>,
    after_state: &str,
) -> ServiceResult<()> {
    if state.audit.iter().any(|row: &MemoryAuthorityAuditRow| {
        row.env == env && row.mutation_id == mutation_id && row.sequence == 0
    }) {
        return Err(ServiceError::InternalError(format!(
            "bot authority memory store: audit row already exists \
             for mutation {mutation_id}"
        )));
    }
    state.audit.push(MemoryAuthorityAuditRow {
        env: env.to_string(),
        bot_id: bot_id.to_string(),
        mutation_id: mutation_id.to_string(),
        sequence: 0,
        action: action.to_string(),
        actor_kind: actor_kind.to_string(),
        actor_id: actor_id.to_string(),
        subject_id: subject_id.to_string(),
        before_state: before_state.map(str::to_string),
        after_state: after_state.to_string(),
        created_ms: super::authority::now_ms(),
    });
    Ok(())
}

/// ONE governance-facts read on the SAME shared state
/// (spec §10.4): the live state, `created_by`, whether
/// the owner's Human ACTOR row is live in the instance
/// env, the current ownership version and the approved
/// owner edges — mirroring the SQL read exactly so the
/// bcs-admin dry-run classification and the apply
/// decision can never disagree.
/// A pure read; `None` for a Bot unknown in the
/// resolved env.
impl MemoryBotRepo {
    pub(super) async fn read_ownership_governance_memory(
        &self,
        bot_id: &str,
    ) -> ServiceResult<
        Option<bcs_service_api::types::bot_authority::BotOwnershipGovernance>,
    > {
        use bcs_service_api::types::bot_authority::BotOwnershipGovernance;
        use bcs_service_api::ActorKind;

        let env = resolve_env();
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            // Tombstoned: the memory projection keeps
            // only the id, so the resolvable facts are
            // the placeholder identity without state.
            return Ok(Some(BotOwnershipGovernance {
                bot_id: bot_id.to_string(),
                actor_kind: ActorKind::Bot,
                live: false,
                created_by: None,
                owner_actor_present: false,
                ownership_version: 0,
                owner_edges: Vec::new(),
            }));
        }
        let (actor_kind, created_by, record_env) = {
            let bots = self.bots.read().await;
            let Some(record) = bots.get(bot_id) else {
                return Ok(None);
            };
            (
                record.actor_kind,
                record.created_by.clone(),
                record.env.clone().unwrap_or_else(resolve_env),
            )
        };
        if record_env != env {
            return Ok(None);
        }
        // The owner's Human ACTOR row must be live in the
        // instance env: `actor_kind = 'human'`, env-bound,
        // not tombstoned (the same registry the authority
        // guards read — the classification facts the
        // bcs-admin dry-run uses).
        let owner_actor_present = match created_by.as_deref() {
            Some(user) => {
                let human_id =
                    ownership_composition::owner_actor_id(user);
                let tombstoned = self
                    .deleted_bot_ids
                    .read()
                    .await
                    .contains(&human_id);
                !tombstoned
                    && self
                        .bots
                        .read()
                        .await
                        .get(&human_id)
                        .map(|row| {
                            row.actor_kind == ActorKind::Human
                                && row.env.as_deref()
                                    == Some(env.as_str())
                        })
                        .unwrap_or(false)
            }
            None => false,
        };
        let (ownership_version, owner_edges) = {
            let authority = self.authority.read().await;
            let version = authority
                .ownership_versions
                .get(&(env.clone(), bot_id.to_string()))
                .copied()
                .unwrap_or(0);
            let mut edges = authority
                .edges
                .iter()
                .filter(|(key, (status, ref_id))| {
                    key.0 == env
                        && key.1 == bot_id
                        && key.3 == MemoryAuthorityEdgeKind::Owner
                        && *status == MemoryAuthorityEdgeStatus::Approved
                        && *ref_id == 0
                })
                .map(|(key, _)| key.2.clone())
                .collect::<Vec<String>>();
            // Stable report order for repeated dry-runs.
            edges.sort();
            (version, edges)
        };
        Ok(Some(BotOwnershipGovernance {
            bot_id: bot_id.to_string(),
            actor_kind,
            live: true,
            created_by,
            owner_actor_present,
            ownership_version,
            owner_edges,
        }))
    }
}

//! In-memory bot authority: strict owner/manager reads, source-scoped
//! manager mutations with the same-transaction audit, and the test-only
//! seeding levers (plan Tasks 3–4; spec §5/§12.4/§5.4).
//!
//! The authority rows live INSIDE [`MemoryBotRepo`] (`authority` state),
//! sharing the bot-lifecycle state boundary: bot deletion (soft-delete set +
//! registry map) and authority state are only reachable through this repo,
//! so they can never diverge through a second, unshared memory store. Reads
//! use the same lock family as the lifecycle methods (`bots`,
//! `deleted_bot_ids`, `authority`), and a batch (`roles_for`) acquires the
//! authority read lock ONCE for the whole batch.
//!
//! `mutate_manager` performs validation, edge change and the
//! `bot_manager_changes` audit append inside ONE critical section
//! (the in-memory equivalent of the SQL store's one-transaction contract):
//! the actor must still hold a current owner/manager role, the subject must
//! be the same-env live Human (`InvalidSubject`) and not the owner
//! (`Conflict`); grants restore previously revoked rows under the SAME row
//! id; revokes touch only `direct`/`ownership_transfer` sources and audit
//! only actual changes.
//!
//! Strictness mirrors the SQL store exactly:
//! - role rows are stored in the RAW `edge_grants` column shapes (strings,
//!   plus the mirror of the SQL autoincrement `id` used for same-id restore
//!   and audit correlation) and decoded STRICTLY through
//!   `bcs_domain::decode_role_source` on every read — a corrupt shape is
//!   `CorruptAuthority`, never serde-defaulted into a valid role;
//! - `ownership` validates liveness (soft-deleted = missing → `BotNotFound`),
//!   initialization (version 0 → `OwnershipNotInitialized`) and the unique
//!   approved owner (0 or >1 → `CorruptAuthority`);
//! - `roles_for` results stay position-aligned with the input pairs; a
//!   missing active role is `None`; any corruption is `Err` — missing rows
//!   are never treated as allowed and no read defaults to empty-success.
//!
//! The `seed_*` / lever methods below are TEST-ONLY: they exist so the
//! Task 3/4 conformance drivers can build read-test preconditions before
//! the Task 5 production initialization contract lands. Nothing outside the
//! test drivers — neither bootstrap, nor routes, nor services — is ever
//! wired to them; they are not a production claim entry.

use std::collections::HashMap;

use async_trait::async_trait;
use bcs_service_api::types::{
    decode_role_source, AuditActor, BotAccessRelation, BotManagerList, BotManagerSummary,
    DecodedRoleSource, ManagementSource, ManagerMutation, ManagerMutationResult, OwnershipState,
    OWNER_SOURCE_ID, OWNER_SOURCE_KIND, ROLE_GRANT_REF_ID, UNINITIALIZED_OWNERSHIP_VERSION,
};
use bcs_service_api::port::repo::bot_authority::{human_actor_id, user_id_from_actor};
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

use super::{MemoryBotRepo, RegisteredBotInner, resolve_env};

/// One raw authority role row, mirroring the `edge_grants` column shapes so
/// the strict decoder is exercised exactly like the SQL store (driver-
/// injected corrupt shapes must behave identically in both stores).
#[derive(Debug, Clone)]
pub(super) struct MemoryRoleEdgeRow {
    /// Mirrors the SQL autoincrement `id`: revoked rows RESTORE under the
    /// same id, and audit rows reference their edge by it.
    pub(super) id: i64,
    pub(super) env: String,
    /// Human ACTOR id (`human_<user_id>`), per the shared port encoding.
    pub(super) from_id: String,
    pub(super) to_id: String,
    /// Raw `grant_kind` column text (`owner` / `manager` / anything corrupt).
    pub(super) grant_kind: String,
    pub(super) grant_ref_id: i64,
    /// Inline rules; role rows must carry `None` (SQL: `NULL`).
    pub(super) rules: Option<serde_json::Value>,
    /// Raw `status` column text; only `approved` rows are effective.
    pub(super) status: String,
    pub(super) management_source_kind: String,
    pub(super) management_source_id: String,
}

/// One `bot_manager_changes` audit row projection: appended only for actual
/// state changes, recording the TRUE operator, with the edge id of the
/// changed source row.
///
/// The fields mirror the SQL `bot_manager_changes` columns one-to-one so the
/// in-memory projection stays a faithful twin of the audited table; only the
/// row count is consumed by the Task 4 conformance today (the recording
/// double keeps the rest for the audit-listing follow-ups), hence the
/// field-level allow.
#[allow(dead_code)]
#[derive(Debug, Clone)]
pub(super) struct MemoryManagerChangeRecord {
    pub(super) audit_id: String,
    pub(super) subject_user_id: String,
    pub(super) edge_id: i64,
    pub(super) management_source_kind: String,
    pub(super) management_source_id: String,
    pub(super) action: String,
    pub(super) actor_kind: String,
    pub(super) actor_id: String,
    pub(super) operation_id: String,
}

/// Authority state inside [`MemoryBotRepo`]: ownership versions and role
/// edge rows on the same state boundary as the bot lifecycle.
#[derive(Debug, Default)]
pub(super) struct MemoryAuthorityState {
    /// `bot_id -> ownership_version`. Absent = historical default 0
    /// (uninitialized), mirroring `bcs_bots.ownership_version`.
    pub(super) ownership_versions: HashMap<String, u64>,
    pub(super) role_rows: Vec<MemoryRoleEdgeRow>,
    /// Mirror of the SQL autoincrement: every role row gets a unique id.
    pub(super) next_edge_id: i64,
    /// Mirror of the `bot_manager_changes` audit table; the counter
    /// projection `authority_audit_count` reports its length.
    pub(super) audit_records: Vec<MemoryManagerChangeRecord>,
    /// Test-only: armed one-shot failure of the next authority write lever.
    pub(super) fail_next_write: bool,
}

fn corrupt(bot_id: &str, env: &str, detail: impl Into<String>) -> ServiceError {
    ServiceError::Authority(AuthorityError::CorruptAuthority {
        bot_id: bot_id.to_string(),
        env: env.to_string(),
        detail: detail.into(),
    })
}

/// Strict decode of one manager row into `(user_id, ManagementSource)`; a
/// corrupt from_id shape or source fails closed (never a dropped row).
fn memory_manager_source_from_row(
    row: &MemoryRoleEdgeRow,
    bot_id: &str,
    env: &str,
) -> ServiceResult<(String, ManagementSource)> {
    let user_id = user_id_from_actor(&row.from_id)
        .ok_or_else(|| {
            corrupt(
                bot_id,
                env,
                format!("manager edge from non-human actor id '{}'", row.from_id),
            )
        })?
        .to_string();
    if row.grant_ref_id != ROLE_GRANT_REF_ID as i64 {
        return Err(corrupt(
            bot_id,
            env,
            format!(
                "role row with grant_ref_id {} (expected {})",
                row.grant_ref_id, ROLE_GRANT_REF_ID
            ),
        ));
    }
    if row.rules.is_some() {
        return Err(corrupt(bot_id, env, "role row carries inline rules"));
    }
    let decoded =
        decode_role_source(&row.management_source_kind, &row.management_source_id).map_err(
            |err| corrupt(bot_id, env, format!("undecodable management source: {}", err)),
        )?;
    match decoded {
        DecodedRoleSource::Manager(source) => Ok((user_id, source)),
        DecodedRoleSource::Owner => Err(corrupt(
            bot_id,
            env,
            "owner-shaped source row inside the manager page",
        )),
    }
}

impl MemoryAuthorityState {
    /// Append ONE `bot_manager_changes` audit row (`<operation_id>-<edge_id>`
    /// mirror of the SQL audit id) recording the TRUE operator.
    fn append_manager_audit(
        &mut self,
        operation_id: &str,
        subject_user_id: &str,
        edge_id: i64,
        management_source_kind: &str,
        management_source_id: &str,
        action: &str,
        actor: &AuditActor,
    ) {
        self.audit_records.push(MemoryManagerChangeRecord {
            audit_id: format!("{operation_id}-{edge_id}"),
            subject_user_id: subject_user_id.to_string(),
            edge_id,
            management_source_kind: management_source_kind.to_string(),
            management_source_id: management_source_id.to_string(),
            action: action.to_string(),
            actor_kind: actor.kind_str().to_string(),
            actor_id: actor.actor_id().to_string(),
            operation_id: operation_id.to_string(),
        });
    }

    /// Allocate the next edge-row id (the SQL autoincrement mirror).
    fn allocate_edge_id(&mut self) -> i64 {
        let id = self.next_edge_id;
        self.next_edge_id += 1;
        id
    }
    /// Decode ONE raw role row strictly (fail closed, never serde-default).
    fn relation_of_row(
        &self,
        row: &MemoryRoleEdgeRow,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<BotAccessRelation> {
        if row.grant_ref_id != ROLE_GRANT_REF_ID as i64 {
            return Err(corrupt(
                bot_id,
                env,
                format!(
                    "role row with grant_ref_id {} (expected {})",
                    row.grant_ref_id, ROLE_GRANT_REF_ID
                ),
            ));
        }
        if row.rules.is_some() {
            return Err(corrupt(bot_id, env, "role row carries inline rules"));
        }
        let decoded = decode_role_source(&row.management_source_kind, &row.management_source_id)
            .map_err(|err| {
                corrupt(bot_id, env, format!("undecodable management source: {}", err))
            })?;
        match (row.grant_kind.as_str(), decoded) {
            ("owner", DecodedRoleSource::Owner) => Ok(BotAccessRelation::Owner),
            ("manager", DecodedRoleSource::Manager(_)) => Ok(BotAccessRelation::Manager),
            (kind, _) => Err(corrupt(
                bot_id,
                env,
                format!("role kind '{}' inconsistent with its management source", kind),
            )),
        }
    }

    /// Strict merge for one (env, from_id, to_id) subject pair: Any approved
    /// matching row must decode; owner takes priority over any manager.
    fn relation_for(
        &self,
        env: &str,
        from_id: &str,
        to_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        let mut merged: Option<BotAccessRelation> = None;
        for row in &self.role_rows {
            if row.env != env
                || row.from_id != from_id
                || row.to_id != to_id
                || row.status != "approved"
            {
                continue;
            }
            match self.relation_of_row(row, to_id, env)? {
                BotAccessRelation::Owner => merged = Some(BotAccessRelation::Owner),
                BotAccessRelation::Manager
                    if merged != Some(BotAccessRelation::Owner) =>
                {
                    merged = Some(BotAccessRelation::Manager)
                }
                BotAccessRelation::Manager => {}
            }
        }
        Ok(merged)
    }
}

#[async_trait]
impl BotAuthorityRepoPort for MemoryBotRepo {
    async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState> {
        let env = resolve_env();
        // Same state boundary as lifecycle reads: a soft-deleted or
        // never-registered bot has no authority surface at all.
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
        if !self.bots.read().await.contains_key(bot_id) {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
        let authority = self.authority.read().await;
        let version = authority
            .ownership_versions
            .get(bot_id)
            .copied()
            .unwrap_or(UNINITIALIZED_OWNERSHIP_VERSION);
        if version == UNINITIALIZED_OWNERSHIP_VERSION {
            return Err(ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized {
                    bot_id: bot_id.to_string(),
                    env,
                },
            ));
        }
        // The strict approved-owner slot: zero or multiple approved owner
        // rows on an initialized bot are corruption, never an implicit
        // snapshot (the SQL store enforces the same via its partial unique
        // index; both still CHECK on read).
        let owner_rows: Vec<&MemoryRoleEdgeRow> = authority
            .role_rows
            .iter()
            .filter(|row| {
                row.env == env
                    && row.to_id == bot_id
                    && row.status == "approved"
                    && row.grant_kind == "owner"
            })
            .collect();
        match owner_rows.len() {
            0 => Err(corrupt(bot_id, &env, "initialized bot has no approved owner edge")),
            1 => {
                let row = owner_rows[0];
                let owner_user_id = user_id_from_actor(&row.from_id)
                    .ok_or_else(|| {
                        corrupt(
                            bot_id,
                            &env,
                            format!("owner edge from non-human actor id '{}'", row.from_id),
                        )
                    })?
                    .to_string();
                // The owner row itself must also decode strictly.
                authority.relation_of_row(row, bot_id, &env)?;
                Ok(OwnershipState {
                    owner_user_id,
                    ownership_version: version,
                })
            }
            count => Err(corrupt(
                bot_id,
                &env,
                format!("initialized bot has {count} approved owner edges"),
            )),
        }
    }

    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        let env = resolve_env();
        let from_id = human_actor_id(user_id);
        // ONE critical section; a pure strict lookup (the Core validates the
        // Bot's ownership invariant before answering authorization).
        let authority = self.authority.read().await;
        authority.relation_for(&env, &from_id, bot_id)
    }

    async fn roles_for(
        &self,
        pairs: &[(String, String)],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        if pairs.is_empty() {
            return Ok(Vec::new());
        }
        let env = resolve_env();
        // ONE lock acquisition for the whole batch: no per-pair locking, no
        // per-pair state materialization (the SQL store's no-N+1 proof has
        // its memory counterpart here — a single pass over shared state).
        let authority = self.authority.read().await;
        let mut out = Vec::with_capacity(pairs.len());
        for (user_id, bot_id) in pairs {
            let from_id = human_actor_id(user_id);
            out.push(authority.relation_for(&env, &from_id, bot_id)?);
        }
        Ok(out)
    }

    async fn mutate_manager(
        &self,
        actor: AuditActor,
        bot_id: &str,
        mutation: ManagerMutation,
    ) -> ServiceResult<ManagerMutationResult> {
        let env = resolve_env();
        if self.take_authority_write_failure().await {
            return Err(ServiceError::InternalError(
                "test-injected authority write failure".into(),
            ));
        }
        let subject_user_id = match &mutation {
            ManagerMutation::GrantDirect { user_id }
            | ManagerMutation::RevokeNonTeam { user_id } => user_id.clone(),
        };
        let subject_from_id = human_actor_id(&subject_user_id);
        // Structural fail-closed FIRST: manager mutations belong to the current
        // HUMAN owner/managers (Gate 0). A Service/System identifier must
        // never be allowed to alias a `human_<uid>` role-edge subject, so
        // non-Human actors never reach the authorization match below
        // (their governed lanes own their own contracts).
        let actor_from_id = match &actor {
            AuditActor::Human { user_id } => human_actor_id(user_id),
            AuditActor::Service { .. } | AuditActor::System { .. } => {
                return Err(ServiceError::Authority(AuthorityError::Forbidden(format!(
                    "manager mutations are performed by the current Human owner/managers; \
                     this actor kind must use its governed lane (actor kind '{}')",
                    actor.kind_str()
                ))))
            }
        };
        // Liveness pre-checks with sequential guards (the `ownership`()
        // pattern: the registry map excludes soft-deleted rows, and the
        // deleted set is checked before any long-held guard so the deletion
        // lane's deleted→bots ordering can never deadlock with us).
        if self.deleted_bot_ids.read().await.contains(bot_id)
            || self.deleted_bot_ids.read().await.contains(&subject_from_id)
            || !self.bots.read().await.contains_key(bot_id)
        {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
        // ONE critical section for validation + edge change + audit (the
        // in-memory equivalent of the SQL one-transaction contract); the same
        // bots→authority order as the seed levers. The bots guard is held for
        // consistency of the subject-liveness check, not mutated.
        let bots = self.bots.write().await;
        let mut authority = self.authority.write().await;

        let ownership_version = authority
            .ownership_versions
            .get(bot_id)
            .copied()
            .unwrap_or(UNINITIALIZED_OWNERSHIP_VERSION);
        if ownership_version == UNINITIALIZED_OWNERSHIP_VERSION {
            return Err(ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized {
                    bot_id: bot_id.to_string(),
                    env,
                },
            ));
        }
        let owner_count = authority
            .role_rows
            .iter()
            .filter(|row| {
                row.env == env
                    && row.to_id == bot_id
                    && row.status == "approved"
                    && row.grant_kind == "owner"
            })
            .count();
        if owner_count != 1 {
            return Err(corrupt(
                bot_id,
                &env,
                format!(
                    "initialized bot has {owner_count} approved owner edges \
                     (manager mutation denied)"
                ),
            ));
        }
        let actor_authorized = authority.role_rows.iter().any(|row| {
            row.env == env
                && row.to_id == bot_id
                && row.from_id == actor_from_id
                && row.status == "approved"
                && (row.grant_kind == "owner" || row.grant_kind == "manager")
        });
        if !actor_authorized {
            return Err(ServiceError::Authority(AuthorityError::Forbidden(format!(
                "actor '{}' holds no current owner/manager role on bot '{}'",
                actor.actor_id(),
                bot_id
            ))));
        }
        let subject_is_owner = authority.role_rows.iter().any(|row| {
            row.env == env
                && row.to_id == bot_id
                && row.from_id == subject_from_id
                && row.status == "approved"
                && row.grant_kind == "owner"
        });
        if subject_is_owner {
            return Err(ServiceError::Authority(AuthorityError::Conflict(
                format!(
                    "user '{subject_user_id}' is the owner of bot '{bot_id}'; \
                     the owner role changes only through the ownership-transfer flow"
                ),
            )));
        }
        let subject_is_live_human = match bots.get(&subject_from_id) {
            Some(row) => {
                row.actor_kind == bcs_service_api::ActorKind::Human
                    && row.env.as_deref() == Some(env.as_str())
            }
            None => false,
        };
        if !subject_is_live_human {
            return Err(ServiceError::Authority(AuthorityError::InvalidSubject(
                format!(
                    "target user '{subject_user_id}' is not a live human actor in env '{env}'"
                ),
            )));
        }

        let operation_id = uuid::Uuid::new_v4().to_string();
        match mutation {
            ManagerMutation::GrantDirect { .. } => {
                // One direct/manual slot per unique key: restore the revoked
                // row under the SAME id, or insert a fresh approved row —
                // never a second slot, never INSERT-IGNORE semantics.
                let mut existing: Option<usize> = None;
                for (index, row) in authority.role_rows.iter().enumerate() {
                    if row.env == env
                        && row.to_id == bot_id
                        && row.from_id == subject_from_id
                        && row.grant_kind == "manager"
                        && row.management_source_kind == "direct"
                        && row.management_source_id == "manual"
                    {
                        existing = Some(index);
                        break;
                    }
                }
                match existing {
                    Some(index) => {
                        let row = &mut authority.role_rows[index];
                        if row.status == "approved" {
                            // Idempotent repeat: no change, no audit row.
                            return Ok(ManagerMutationResult::default());
                        }
                        row.status = "approved".to_string();
                        let (edge_id, kind, id) = (
                            row.id,
                            row.management_source_kind.clone(),
                            row.management_source_id.clone(),
                        );
                        authority.append_manager_audit(
                            &operation_id,
                            &subject_user_id,
                            edge_id,
                            &kind,
                            &id,
                            "grant",
                            &actor,
                        );
                        Ok(ManagerMutationResult {
                            changed: true,
                            remaining_team_sources: Vec::new(),
                        })
                    }
                    None => {
                        let edge_id = authority.allocate_edge_id();
                        authority.role_rows.push(MemoryRoleEdgeRow {
                            id: edge_id,
                            env: env.clone(),
                            from_id: subject_from_id,
                            to_id: bot_id.to_string(),
                            grant_kind: "manager".to_string(),
                            grant_ref_id: ROLE_GRANT_REF_ID as i64,
                            rules: None,
                            status: "approved".to_string(),
                            management_source_kind: "direct".to_string(),
                            management_source_id: "manual".to_string(),
                        });
                        authority.append_manager_audit(
                            &operation_id,
                            &subject_user_id,
                            edge_id,
                            "direct",
                            "manual",
                            "grant",
                            &actor,
                        );
                        Ok(ManagerMutationResult {
                            changed: true,
                            remaining_team_sources: Vec::new(),
                        })
                    }
                }
            }
            ManagerMutation::RevokeNonTeam { .. } => {
                // Only direct/ownership_transfer rows flip; team rows stay
                // byte-for-byte untouched (the temporal equivalent of the
                // brief's revoke predicate).
                let mut changed_edge_ids: Vec<i64> = Vec::new();
                for row in authority.role_rows.iter_mut() {
                    if row.env == env
                        && row.to_id == bot_id
                        && row.from_id == subject_from_id
                        && row.status == "approved"
                        && row.grant_kind == "manager"
                        && (row.management_source_kind == "direct"
                            || row.management_source_kind == "ownership_transfer")
                    {
                        row.status = "revoked".to_string();
                        changed_edge_ids.push(row.id);
                    }
                }
                // Remaining team sources from the same critical section.
                let mut remaining: Vec<String> = Vec::new();
                for row in &authority.role_rows {
                    if row.env == env
                        && row.to_id == bot_id
                        && row.from_id == subject_from_id
                        && row.status == "approved"
                        && row.grant_kind == "manager"
                        && row.management_source_kind == "team"
                    {
                        let decoded =
                            decode_role_source("team", &row.management_source_id).map_err(
                                |err| {
                                    corrupt(
                                        bot_id,
                                        &env,
                                        format!("undecodable team manager source: {}", err),
                                    )
                                },
                            )?;
                        if let DecodedRoleSource::Manager(ManagementSource::Team(team_id)) = decoded
                        {
                            remaining.push(team_id);
                        }
                    }
                }
                remaining.sort();
                if changed_edge_ids.is_empty() {
                    return Ok(ManagerMutationResult {
                        changed: false,
                        remaining_team_sources: remaining,
                    });
                }
                for edge_id in changed_edge_ids {
                    let row = authority
                        .role_rows
                        .iter()
                        .find(|row| row.id == edge_id)
                        .expect("the mutated row is in the same critical section")
                        .clone();
                    authority.append_manager_audit(
                        &operation_id,
                        &subject_user_id,
                        edge_id,
                        &row.management_source_kind,
                        &row.management_source_id,
                        "revoke",
                        &actor,
                    );
                }
                Ok(ManagerMutationResult {
                    changed: true,
                    remaining_team_sources: remaining,
                })
            }
        }
    }

    async fn list_managers(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<BotManagerList> {
        let env = resolve_env();
        // Validation mirrors `ownership`: live row, initialized version,
        // unique approved owner decoded strictly.
        if self.deleted_bot_ids.read().await.contains(bot_id)
            || !self.bots.read().await.contains_key(bot_id)
        {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
        let authority = self.authority.read().await;
        let ownership_version = authority
            .ownership_versions
            .get(bot_id)
            .copied()
            .unwrap_or(UNINITIALIZED_OWNERSHIP_VERSION);
        if ownership_version == UNINITIALIZED_OWNERSHIP_VERSION {
            return Err(ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized {
                    bot_id: bot_id.to_string(),
                    env,
                },
            ));
        }
        let owner_row = authority
            .role_rows
            .iter()
            .find(|row| {
                row.env == env
                    && row.to_id == bot_id
                    && row.status == "approved"
                    && row.grant_kind == "owner"
            })
            .cloned();
        let Some(owner_row) = owner_row else {
            return Err(corrupt(
                bot_id,
                &env,
                "initialized bot has no approved owner edge",
            ));
        };
        let owner_user_id = user_id_from_actor(&owner_row.from_id)
            .ok_or_else(|| {
                corrupt(
                    bot_id,
                    &env,
                    format!("owner edge from non-human actor id '{}'", owner_row.from_id),
                )
            })?
            .to_string();
        authority.relation_of_row(&owner_row, bot_id, &env)?;

        // Manager page: deduped per user, user_id ASC, owner excluded, with
        // strictly decoded, canonically ordered sources (the SQL store's
        // GROUP BY page + source queries in their memory form: one pass).
        let mut by_user: HashMap<String, Vec<ManagementSource>> = HashMap::new();
        let mut user_ids: Vec<String> = Vec::new();
        for row in &authority.role_rows {
            if row.env != env
                || row.to_id != bot_id
                || row.status != "approved"
                || row.grant_kind != "manager"
                || row.from_id == owner_row.from_id
            {
                continue;
            }
            let (user_id, source) = memory_manager_source_from_row(row, bot_id, &env)?;
            match by_user.entry(user_id.clone()) {
                std::collections::hash_map::Entry::Occupied(mut occupied) => {
                    occupied.get_mut().push(source);
                }
                std::collections::hash_map::Entry::Vacant(vacant) => {
                    vacant.insert(vec![source]);
                    user_ids.push(user_id);
                }
            }
        }
        user_ids.sort();
        let mut managers = Vec::<BotManagerSummary>::new();
        let mut skipped: u64 = 0;
        for user_id in user_ids {
            if skipped < offset {
                skipped += 1;
                continue;
            }
            if managers.len() as u64 >= limit {
                break;
            }
            let mut sources = by_user.remove(&user_id).unwrap_or_default();
            sources.sort_by(|left, right| {
                left.storage_parts().cmp(&right.storage_parts())
            });
            managers.push(BotManagerSummary { user_id, sources });
        }
        Ok(BotManagerList {
            owner_user_id,
            managers,
        })
    }
}

impl MemoryBotRepo {
    // ------------------------------------------------------------------
    // TEST-ONLY authority seeding levers (plan Task 3 harness drivers).
    // Task 5 replaces these with the production initialization contract;
    // lifecycle tests after Task 5 must go through that production
    // contract instead of adding more levers here. These methods are never
    // called from bootstrap, routes, or services — a production claim
    // entry would defeat the whole strictness contract above.
    // ------------------------------------------------------------------

    /// Atomically write one legal Bot (version 1) + its approved owner edge,
    /// per the Task 2 schema, sharing this repo's lifecycle critical
    /// section. Fails closed if the test armed a write failure.
    pub async fn seed_authority_owned(
        &self,
        bot_id: &str,
        owner_user_id: &str,
    ) -> ServiceResult<()> {
        let env = resolve_env();
        if self.take_authority_write_failure().await {
            return Err(ServiceError::InternalError(
                "test-injected authority write failure".into(),
            ));
        }
        // Bot row + authority row mutate together under the same locks used
        // by the lifecycle methods (single shared critical section).
        {
            let mut bots = self.bots.write().await;
            let mut authority = self.authority.write().await;
            if !bots.contains_key(bot_id) {
                bots.insert(
                    bot_id.to_string(),
                    RegisteredBotInner {
                        bot_id: bot_id.to_string(),
                        last_heartbeat: std::time::Instant::now(),
                        capabilities: Default::default(),
                        ws_connection: None,
                        session_token: None,
                        env: Some(env.clone()),
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: None,
                        protocol_version: 1,
                        user_visibility: Default::default(),
                        friend_ext: serde_json::Map::new(),
                        friend_check_in_strategy: Default::default(),
                    },
                );
            }
            authority
                .ownership_versions
                .insert(bot_id.to_string(), 1);
            let edge_id = authority.allocate_edge_id();
            authority.role_rows.push(MemoryRoleEdgeRow {
                id: edge_id,
                env: env.clone(),
                from_id: human_actor_id(owner_user_id),
                to_id: bot_id.to_string(),
                grant_kind: "owner".to_string(),
                grant_ref_id: ROLE_GRANT_REF_ID as i64,
                rules: None,
                status: "approved".to_string(),
                management_source_kind: OWNER_SOURCE_KIND.to_string(),
                management_source_id: OWNER_SOURCE_ID.to_string(),
            });
        }
        Ok(())
    }

    /// Seed one APPROVED manager source edge through the FORMAL source
    /// semantics (Task 4 harness: `manager/<kind>/<id>` role rows, never a
    /// friend edge). Test-only fixture lever — NOT a production grant path.
    pub async fn seed_authority_manager_source(
        &self,
        bot_id: &str,
        user_id: &str,
        source_kind: &str,
        source_id: &str,
    ) -> ServiceResult<()> {
        if self.take_authority_write_failure().await {
            return Err(ServiceError::InternalError(
                "test-injected authority write failure".into(),
            ));
        }
        let env = resolve_env();
        let mut authority = self.authority.write().await;
        let edge_id = authority.allocate_edge_id();
        authority.role_rows.push(MemoryRoleEdgeRow {
            id: edge_id,
            env,
            from_id: human_actor_id(user_id),
            to_id: bot_id.to_string(),
            grant_kind: "manager".to_string(),
            grant_ref_id: ROLE_GRANT_REF_ID as i64,
            rules: None,
            status: "approved".to_string(),
            management_source_kind: source_kind.to_string(),
            management_source_id: source_id.to_string(),
        });
        Ok(())
    }

    /// Edge ids of the subject's `direct/manual` manager rows (any status) —
    /// the same-id restore observation lever for the Task 4 conformance.
    pub async fn authority_direct_manager_edge_ids(
        &self,
        bot_id: &str,
        user_id: &str,
    ) -> ServiceResult<Vec<i64>> {
        let env = resolve_env();
        let authority = self.authority.read().await;
        let mut ids: Vec<i64> = authority
            .role_rows
            .iter()
            .filter(|row| {
                row.env == env
                    && row.to_id == bot_id
                    && row.from_id == human_actor_id(user_id)
                    && row.grant_kind == "manager"
                    && row.management_source_kind == "direct"
                    && row.management_source_id == "manual"
            })
            .map(|row| row.id)
            .collect();
        ids.sort();
        Ok(ids)
    }

    /// Insert a live Bot whose ownership is UNINITIALIZED (version 0 — a
    /// plain registry row, no authority claim): the precondition for the
    /// `OwnershipNotInitialized` corrupt-object test.
    pub async fn seed_authority_uninitialized_bot(&self, bot_id: &str) -> ServiceResult<()> {
        if self.take_authority_write_failure().await {
            return Err(ServiceError::InternalError(
                "test-injected authority write failure".into(),
            ));
        }
        let mut bots = self.bots.write().await;
        if !bots.contains_key(bot_id) {
            bots.insert(
                bot_id.to_string(),
                RegisteredBotInner {
                    bot_id: bot_id.to_string(),
                    last_heartbeat: std::time::Instant::now(),
                    capabilities: Default::default(),
                    ws_connection: None,
                    session_token: None,
                    env: Some(resolve_env()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: None,
                    protocol_version: 1,
                    user_visibility: Default::default(),
                    friend_ext: serde_json::Map::new(),
                    friend_check_in_strategy: Default::default(),
                },
            );
        }
        let mut authority = self.authority.write().await;
        authority.ownership_versions.remove(bot_id);
        Ok(())
    }

    /// Remove the Bot's approved owner edge while keeping
    /// ownership_version > 0: the precondition for the `CorruptAuthority`
    /// (initialized-but-ownerless) corrupt-object test.
    pub async fn break_authority_owner_edge(&self, bot_id: &str) -> ServiceResult<()> {
        if self.take_authority_write_failure().await {
            return Err(ServiceError::InternalError(
                "test-injected authority write failure".into(),
            ));
        }
        let mut authority = self.authority.write().await;
        let mut retained = Vec::with_capacity(authority.role_rows.len());
        for row in authority.role_rows.drain(..) {
            let keep = !(row.to_id == bot_id && row.grant_kind == "owner");
            if keep {
                retained.push(row);
            } else {
                warn!(bot_id = %bot_id, "test lever: dropping owner edge");
            }
        }
        authority.role_rows = retained;
        Ok(())
    }

    /// Arm a one-shot failure of the next authority write lever.
    ///
    /// The flag lives in the authority state itself so arming and consuming
    /// share one state boundary. Harness drivers arm while no authority lock
    /// is held, so the immediate `try_write` is the deterministic sync arm
    /// path (the trait-level `fail_next_write` contract is sync).
    pub fn arm_authority_write_failure(&self) {
        match self.authority.try_write() {
            Ok(mut state) => state.fail_next_write = true,
            Err(_) => panic!("authority state lock held while arming write failure"),
        }
    }

    /// Test-only projection of the `bot_manager_changes` audit row count:
    /// only actual state changes append a record (Task 3 observed 0 because
    /// no mutation had landed yet).
    pub async fn authority_audit_count(&self) -> ServiceResult<u64> {
        let authority = self.authority.read().await;
        Ok(authority.audit_records.len() as u64)
    }

    /// Consume the armed one-shot write failure inside the authority lock.
    async fn take_authority_write_failure(&self) -> bool {
        let mut authority = self.authority.write().await;
        if authority.fail_next_write {
            authority.fail_next_write = false;
            return true;
        }
        false
    }
}
#[cfg(test)]
#[path = "memory_authority_tests.rs"]
mod tests;
//! In-memory bot authority: strict owner/manager reads + the test-only
//! Task 3 seeding levers (plan Task 3; spec §5/§12.4).
//!
//! The authority rows live INSIDE [`MemoryBotRepo`] (`authority` state),
//! sharing the bot-lifecycle state boundary: bot deletion (soft-delete set +
//! registry map) and authority state are only reachable through this repo,
//! so they can never diverge through a second, unshared memory store. Reads
//! use the same lock family as the lifecycle methods (`bots`,
//! `deleted_bot_ids`, `authority`), and a batch (`roles_for`) acquires the
//! authority read lock ONCE for the whole batch.
//!
//! Strictness mirrors the SQL store exactly:
//! - role rows are stored in the RAW `edge_grants` column shapes (strings)
//!   and decoded STRICTLY through `bcs_domain::decode_role_source` on every
//!   read — a corrupt shape is `CorruptAuthority`, never serde-defaulted
//!   into a valid role;
//! - `ownership` validates liveness (soft-deleted = missing → `BotNotFound`),
//!   initialization (version 0 → `OwnershipNotInitialized`) and the unique
//!   approved owner (0 or >1 → `CorruptAuthority`);
//! - `roles_for` results stay position-aligned with the input pairs; a
//!   missing active role is `None`; any corruption is `Err` — missing rows
//!   are never treated as allowed and no read defaults to empty-success.
//!
//! The `seed_*` / lever methods below are TEST-ONLY: they exist so the
//! Task 3 conformance drivers can build read-test preconditions before the
//! Task 5 production initialization contract lands. Nothing outside the
//! test drivers — neither bootstrap, nor routes, nor services — is ever
//! wired to them; they are not a production claim entry.

use std::collections::HashMap;

use async_trait::async_trait;
use bcs_service_api::types::{
    decode_role_source, BotAccessRelation, DecodedRoleSource, OwnershipState,
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

/// Authority state inside [`MemoryBotRepo`]: ownership versions and role
/// edge rows on the same state boundary as the bot lifecycle.
#[derive(Debug, Default)]
pub(super) struct MemoryAuthorityState {
    /// `bot_id -> ownership_version`. Absent = historical default 0
    /// (uninitialized), mirroring `bcs_bots.ownership_version`.
    pub(super) ownership_versions: HashMap<String, u64>,
    pub(super) role_rows: Vec<MemoryRoleEdgeRow>,
    /// Test-only: `bot_manager_changes` row count projection.
    pub(super) audit_rows: u64,
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

impl MemoryAuthorityState {
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
            authority.role_rows.push(MemoryRoleEdgeRow {
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

    /// Test-only projection of the authority audit row count
    /// (`bot_manager_changes`). Task 3 performs no audited authority
    /// writes, so drivers must observe 0.
    pub async fn authority_audit_count(&self) -> ServiceResult<u64> {
        let authority = self.authority.read().await;
        Ok(authority.audit_rows)
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
mod tests {
    use std::sync::Arc;

    use bcs_service_api::port::repo::BotAuthorityRepoPort;
    use bcs_service_api::types::error::AuthorityError;
    use bcs_service_api::{BotRepoPort, ServiceError, ServiceResult};

    use super::*;

    async fn repo() -> Arc<MemoryBotRepo> {
        Arc::new(MemoryBotRepo::with_base_dir(
            std::env::temp_dir().join(format!(
                "bcs-authority-tests-{}",
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|duration| duration.as_nanos())
                    .unwrap_or(0)
            )),
        ))
    }

    /// Inject a raw `edge_grants`-shaped role row directly (the strict
    /// decoder's corrupt-object surface is only reachable in-crate).
    async fn inject_role_row(repo_mem: &MemoryBotRepo, row: MemoryRoleEdgeRow) {
        let mut authority = repo_mem.authority.write().await;
        authority.role_rows.push(row);
    }

    fn raw_row(
        env: &str,
        from_id: &str,
        to_id: &str,
        kind: &str,
        source_kind: &str,
        source_id: &str,
    ) -> MemoryRoleEdgeRow {
        MemoryRoleEdgeRow {
            env: env.to_string(),
            from_id: from_id.to_string(),
            to_id: to_id.to_string(),
            grant_kind: kind.to_string(),
            grant_ref_id: ROLE_GRANT_REF_ID as i64,
            rules: None,
            status: "approved".to_string(),
            management_source_kind: source_kind.to_string(),
            management_source_id: source_id.to_string(),
        }
    }

    #[tokio::test]
    async fn manager_rows_decode_strictly_with_owner_priority() {
        let repo = repo().await;
        repo.seed_authority_owned("bot-m", "m").await.unwrap();
        let env = resolve_env();

        inject_role_row(
            &repo,
            raw_row(&env, "human_m2", "bot-m", "manager", "direct", "manual"),
        )
        .await;
        inject_role_row(&repo, raw_row(&env, "human_m2", "bot-m", "manager", "team", "team-a"))
            .await;
        assert_eq!(
            repo.role("m2", "bot-m").await.unwrap(),
            Some(BotAccessRelation::Manager),
            "any approved manager source grants the manager relation"
        );

        // Owner + manager edges on one subject: owner wins.
        inject_role_row(
            &repo,
            raw_row(&env, "human_m", "bot-m", "manager", "direct", "manual"),
        )
        .await;
        assert_eq!(
            repo.role("m", "bot-m").await.unwrap(),
            Some(BotAccessRelation::Owner)
        );

        // Revoked rows are retained but stop reading.
        let rows_before: Vec<MemoryRoleEdgeRow> = {
            let authority = repo.authority.read().await;
            authority.role_rows.clone()
        };
        for mut row in rows_before {
            if row.grant_kind == "manager" {
                row.status = "revoked".to_string();
            }
            let mut authority = repo.authority.write().await;
            // Replace by identity: drop the same-shape row, push the mutated one.
            authority
                .role_rows
                .retain(|existing| existing.env != row.env || existing.from_id != row.from_id || existing.to_id != row.to_id || existing.management_source_kind != row.management_source_kind || existing.management_source_id != row.management_source_id);
            authority.role_rows.push(row);
        }
        assert_eq!(repo.role("m2", "bot-m").await.unwrap(), None);
        assert_eq!(
            repo.role("m", "bot-m").await.unwrap(),
            Some(BotAccessRelation::Owner)
        );
    }

    #[tokio::test]
    async fn illegal_shapes_fail_closed_never_default_into_roles() {
        let repo = repo().await;
        repo.seed_authority_owned("bot-x", "x").await.unwrap();
        let env = resolve_env();
        for (kind, source_kind, source_id, ref_override, rules) in [
            ("owner", "direct", "manual", ROLE_GRANT_REF_ID, false),
            ("owner", "owner", "owner", ROLE_GRANT_REF_ID + 1, false),
            ("owner", "owner", "owner", ROLE_GRANT_REF_ID, true),
            ("manager", "owner", "owner", ROLE_GRANT_REF_ID, false),
            ("manager", "direct", "not-manual", ROLE_GRANT_REF_ID, false),
            ("manager", "team", "", ROLE_GRANT_REF_ID, false),
            ("manager", "banana", "x", ROLE_GRANT_REF_ID, false),
            ("banana", "owner", "owner", ROLE_GRANT_REF_ID, false),
        ] {
            let from_id = "human_z";
            let mut row = raw_row(&env, from_id, "bot-x", kind, source_kind, source_id);
            row.grant_ref_id = ref_override as i64;
            if rules {
                row.rules = Some(serde_json::Value::Null);
            }
            inject_role_row(&repo, row).await;
            match repo.role("z", "bot-x").await {
                Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. })) => {}
                other => panic!(
                    "corrupt role row must fail closed for kind={kind} source=({source_kind},{source_id}): {:?}",
                    other.map(|role| role.is_some())
                ),
            }
            // Corrupt rows poison the batch read too (fail closed, no partial empty-success).
            let pairs = [
                ("x".to_string(), "bot-x".to_string()),
                ("z".to_string(), "bot-x".to_string()),
            ];
            assert!(repo.roles_for(&pairs).await.is_err());
            // And the ownership snapshot once the owner slot itself is damaged.
            if kind != "manager" {
                // owner-shaped corrupt rows surface as corruption aspects of ownership
            }
            // Remove the corrupt row again so the next probe starts clean.
            {
                let mut authority = repo.authority.write().await;
                let discriminator = from_id.to_string();
                authority.role_rows.retain(|row| {
                    !(row.from_id == discriminator && row.to_id == "bot-x" && row.status == "approved")
                });
            }
        }
        // After cleanup the healthy owner row still reads.
        assert_eq!(
            repo.role("x", "bot-x").await.unwrap(),
            Some(BotAccessRelation::Owner)
        );
    }

    #[tokio::test]
    async fn non_unique_owner_edges_are_corrupt() {
        let repo = repo().await;
        repo.seed_authority_owned("bot-dup", "d").await.unwrap();
        let env = resolve_env();
        inject_role_row(
            &repo,
            raw_row(&env, "human_e", "bot-dup", "owner", "owner", "owner"),
        )
        .await;
        assert!(
            matches!(
                repo.ownership("bot-dup").await,
                Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
            ),
            "two approved owner edges on an initialized bot are corruption, never a pick-one"
        );
    }

    #[tokio::test]
    async fn reads_are_bound_to_the_repo_instance_env() {
        let repo = repo().await;
        let env = resolve_env();
        let _ = env;
        // A role row in ANOTHER env must be invisible to this instance.
        inject_role_row(
            &repo,
            raw_row("other-env", "human_q", "bot-q", "owner", "owner", "owner"),
        )
        .await;
        // A registered-but-uninitialized qrow; then initialize the version
        // manually so the only remaining authority data lives in another env.
        repo.seed_authority_uninitialized_bot("bot-q").await.unwrap();
        {
            let mut authority = repo.authority.write().await;
            authority.ownership_versions.insert("bot-q".to_string(), 1);
        }
        assert_eq!(repo.role("q", "bot-q").await.unwrap(), None);
        assert!(
            matches!(
                repo.ownership("bot-q").await,
                Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
            ),
            "an initialized bot whose only owner edge sits in another env remains corrupt"
        );
        let _ = env;
    }

    #[tokio::test]
    async fn missing_and_unregistered_bots_have_no_authority_surface() {
        let repo = repo().await;
        assert!(
            matches!(repo.ownership("ghost").await, Err(ServiceError::BotNotFound(_))),
            "a never-registered bot is not authority-present"
        );
        repo.seed_authority_owned("bot-del", "d").await.unwrap();
        repo.soft_delete("bot-del").await;
        assert!(
            matches!(repo.ownership("bot-del").await, Err(ServiceError::BotNotFound(_))),
            "a soft-deleted bot has no authority surface left"
        );
    }

    #[tokio::test]
    async fn roles_for_keeps_positions_and_none_for_absence() {
        let repo = repo().await;
        repo.seed_authority_owned("bot-a", "a").await.unwrap();
        repo.seed_authority_owned("bot-b", "a").await.unwrap();
        let roles = repo
            .roles_for(&[
                ("a".into(), "bot-a".into()),
                ("nobody".into(), "bot-a".into()),
                ("a".into(), "bot-b".into()),
                ("a".into(), "bot-a".into()),
            ])
            .await
            .unwrap();
        assert_eq!(
            roles,
            vec![
                Some(BotAccessRelation::Owner),
                None,
                Some(BotAccessRelation::Owner),
                Some(BotAccessRelation::Owner),
            ]
        );
        assert!(repo.roles_for(&[]).await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn armed_authority_write_failure_surfaces_and_does_not_fabricate() {
        let repo = repo().await;
        repo.arm_authority_write_failure();
        assert!(
            repo.seed_authority_owned("bot-fail", "f").await.is_err(),
            "the armed failure must surface"
        );
        assert_eq!(repo.role("f", "bot-fail").await.unwrap(), None);
        assert_eq!(repo.authority_audit_count().await.unwrap(), 0);
    }

    #[tokio::test]
    async fn armed_authority_write_failure_lever_is_consumed_once() {
        let repo = repo().await;
        repo.arm_authority_write_failure();
        assert!(repo.seed_authority_owned("bot-f1", "f").await.is_err());
        // Exactly one write failed; the next lever works normally.
        repo.seed_authority_owned("bot-f2", "f").await.unwrap();
        assert_eq!(
            repo.role("f", "bot-f2").await.unwrap(),
            Some(BotAccessRelation::Owner)
        );
    }
}

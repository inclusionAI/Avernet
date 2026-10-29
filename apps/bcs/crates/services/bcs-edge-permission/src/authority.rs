//! `BotAuthorityCoreService` implementation (bot-authority plan Task 1).
//!
//! Forwards strict authority reads to the [`BotAuthorityRepoPort`]
//! and applies the Core glue policy that must not live in storage:
//!
//! - **Identity mapping**: the service layers speak User IDs; the
//!   persisted edges speak Human actor ids (`human_<user_id>`,
//!   spec §5.2). This Core owns the mapping, so the Repo port's
//!   `user_id` parameters receive the persisted actor id.
//! - **Role policy**: `can_manage` is the Owner/Manager union, but
//!   the Owner role is exclusive; `require_owner`/`require_manage`
//!   fail as authorization errors (`Forbidden`) — never silently.
//! - **Env binding**: the Repo port implementation carries the
//!   env (it scopes every statement); this Core keeps the bound
//!   env for wiring diagnostics only — it never propagates a
//!   user-supplied env.
//!
//! No SQL lives here.
//!
//! Write commands: `edit_manager`/`sync_managers` are the
//! Task-2 manager commands — this Core maps the verified
//! User IDs to Human actor ids, applies the actor-kind and
//! input-cap policies (edit is Human-only; sync is
//! trusted-service only; spec §5.1/§5.2), stores the
//! manager error codes' policy input limits, and forwards to
//! the Repo. `change_transfer` forwards verbatim: the
//! Store owns the transfer state machine (Task 3).

use std::sync::Arc;

use async_trait::async_trait;

use bcs_service_api::ServiceError;
use bcs_service_api::core::bot_authority::BotAuthorityCoreService;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    AuthorityActorKind, BotAccessRelation, BotOwnershipView, BotRoleQuery,
    ManagerEdit, ManagerListQuery, ManagerPage, ManagerSetChange, ManagerSync,
    OwnershipChange, OwnershipSync, TransferCommand, TransferKey,
    TransferListQuery, TransferOutcome, TransferPage, TransferView,
    human_actor_id,
};

pub struct BotAuthorityCoreServiceImpl {
    repo: Arc<dyn BotAuthorityRepoPort>,
    /// Env bound at composition time; user parameters cannot
    /// widen this scope (plan §3).
    env: String,
}

impl BotAuthorityCoreServiceImpl {
    /// Bind the authority Repo port and the env this Core serves.
    pub fn new(repo: Arc<dyn BotAuthorityRepoPort>, env: impl Into<String>) -> Self {
        Self {
            repo,
            env: env.into(),
        }
    }

    /// The bound environment (for wiring diagnostics only — callers
    /// cannot change it).
    pub fn env(&self) -> &str {
        &self.env
    }

    /// Core-level role with the User-ID → Human-actor mapping.
    async fn mapped_role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
        self.repo.role(&human_actor_id(user_id), bot_id).await
    }
}

#[async_trait]
impl BotAuthorityCoreService for BotAuthorityCoreServiceImpl {
    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
        self.mapped_role(user_id, bot_id).await
    }

    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> bcs_service_api::ServiceResult<Vec<Option<BotAccessRelation>>> {
        // Map each input; chunking stays the Repo's job so the
        // bounded-collection rule is enforced in exactly one layer.
        let mapped: Vec<BotRoleQuery> = queries
            .iter()
            .map(|query| BotRoleQuery {
                user_id: human_actor_id(&query.user_id),
                bot_id: query.bot_id.clone(),
            })
            .collect();
        self.repo.roles_for(&mapped).await
    }

    async fn ownership(
        &self,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<BotOwnershipView> {
        self.repo.ownership(bot_id).await
    }

    async fn managers(
        &self,
        query: ManagerListQuery,
    ) -> bcs_service_api::ServiceResult<ManagerPage> {
        self.repo.managers(query).await
    }

    async fn transfer(
        &self,
        key: &TransferKey,
    ) -> bcs_service_api::ServiceResult<Option<TransferView>> {
        self.repo.transfer(key).await
    }

    async fn transfers(
        &self,
        query: TransferListQuery,
    ) -> bcs_service_api::ServiceResult<TransferPage> {
        self.repo.transfers(query).await
    }

    async fn can_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        Ok(self.mapped_role(user_id, bot_id).await?.is_some())
    }

    async fn require_owner(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        match self.mapped_role(user_id, bot_id).await? {
            Some(BotAccessRelation::Owner) => Ok(()),
            Some(BotAccessRelation::Manager) => Err(ServiceError::Forbidden(format!(
                "owner role required for bot {bot_id}"
            ))),
            None => Err(ServiceError::Forbidden(format!(
                "no authority role for bot {bot_id}"
            ))),
        }
    }

    async fn require_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        if self.can_manage(user_id, bot_id).await? {
            return Ok(());
        }
        Err(ServiceError::Forbidden(format!(
            "owner or manager role required for bot {bot_id}"
        )))
    }

    async fn edit_manager(
        &self,
        command: ManagerEdit,
    ) -> bcs_service_api::ServiceResult<ManagerSetChange> {
        // Polity (spec §5.1): single-item manager
        // PUT/DELETE is a Human command. The trusted
        // service path must use the dedicated sync
        // command. The Store re-validates the Human
        // operator's current edge inside the write
        // transaction.
        if command.mutation.actor.kind() != AuthorityActorKind::Human {
            return Err(operator_forbidden(
                "single-item manager edit requires a Human actor;                  trusted services must use the dedicated sync command",
            ));
        }
        // Store 收到的是 Human actor id (port contract).
        let command = ManagerEdit {
            user_id: human_actor_id(&command.user_id),
            ..command
        };
        self.repo.edit_manager(command).await
    }

    async fn sync_managers(
        &self,
        command: ManagerSync,
    ) -> bcs_service_api::ServiceResult<ManagerSetChange> {
        // Полitics (spec §5.2): sync is the trusted
        // service command.
        if command.mutation.actor.kind() != AuthorityActorKind::Service {
            return Err(operator_forbidden(
                "full-set manager sync is trusted-service only;                  Human operators use the single-item manager commands",
            ));
        }
        // Dedup + input cap live here as the Command
        // policy layer; the Store re-validates
        // byte-exactly (harness parity).
        let mut deduped: Vec<String> = Vec::with_capacity(command.user_ids.len());
        for user in &command.user_ids {
            let actor = human_actor_id(user);
            if !deduped.contains(&actor) {
                deduped.push(actor);
            }
        }
        if deduped.len() > MANAGER_INPUT_CAP {
            return Err(input_limit());
        }
        self.repo
            .sync_managers(ManagerSync {
                user_ids: deduped,
                ..command
            })
            .await
    }

    async fn sync_ownership(
        &self,
        command: OwnershipSync,
    ) -> bcs_service_api::ServiceResult<OwnershipChange> {
        // Policy (design §2): sync is the trusted service command.
        if command.mutation.actor.kind() != AuthorityActorKind::Service {
            return Err(operator_forbidden(
                "ownership sync is trusted-service only; Human operators use the ownership transfer commands",
            ));
        }
        // Shape gate (design §5.1 step 4a): an empty/whitespace
        // target is a malformed REQUEST value (invalid_request),
        // rejected before any identity resolution.
        let user = command.owner_user_id.as_str();
        if user.is_empty() || user.trim() != user || user.chars().any(char::is_whitespace) {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "invalid_request: ownership sync target '{user}' is not a valid user id"
                ),
                request_id: None,
            });
        }
        // Store 收到的是 Human actor id (port contract).
        self.repo
            .sync_ownership(OwnershipSync {
                owner_user_id: human_actor_id(user),
                ..command
            })
            .await
    }

    async fn change_transfer(
        &self,
        command: TransferCommand,
    ) -> bcs_service_api::ServiceResult<TransferOutcome> {
        // Forwarding Core (plan §3): the Store owns the
        // owner/version/time snapshots and the state
        // machine; there is no body input to map — the
        // caller supplies Bot/target ids and the action
        // only. Conflict outcomes pass through as
        // committed business results (never Err).
        self.repo.change_transfer(command).await
    }
}

/// Trusted-service sync input cap (spec §5.2: 1,000
/// deduped users per request). The Store re-checks.
const MANAGER_INPUT_CAP: usize = 1_000;
/// `bot_authority_operator_forbidden` Forbidden (403).
fn operator_forbidden(detail: &str) -> ServiceError {
    use bcs_service_api::application::v1::bot_authority::
        ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN;
    ServiceError::Forbidden(format!(
        "{ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN}: {detail}"
    ))
}

/// `bot_manager_input_limit` InvalidOperation (400-ish).
fn input_limit() -> ServiceError {
    use bcs_service_api::application::v1::bot_authority::ERROR_BOT_MANAGER_INPUT_LIMIT;
    ServiceError::InvalidOperation {
        message: format!(
            "{ERROR_BOT_MANAGER_INPUT_LIMIT}: a synced manager set              exceeds the 1,000 deduped-user input cap"
        ),
        request_id: None,
    }
}

/// The `BotAuthorityPort` is the narrow
/// authorization read surface business entrypoints
/// consult (spec §8). It IS the composed authority
/// Core: the same instance exposes both the full
/// `BotAuthorityCoreService` and this narrowed hook
/// view, so composition roots wire ONE Core and both
/// consumers see the same authoritative reads.
#[async_trait]
impl bcs_service_api::port::bot_authority::BotAuthorityPort
    for BotAuthorityCoreServiceImpl
{
    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
        BotAuthorityCoreService::role(
            self, user_id, bot_id,
        )
        .await
    }

    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> bcs_service_api::ServiceResult<Vec<Option<BotAccessRelation>>> {
        BotAuthorityCoreService::roles_for(
            self, queries,
        )
        .await
    }

    async fn can_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        BotAuthorityCoreService::can_manage(
            self, user_id, bot_id,
        )
        .await
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> bcs_service_api::ServiceResult<()> {
        BotAuthorityCoreService::require_owner(
            self, user_id, bot_id,
        )
        .await
    }

    async fn require_manage(&self, user_id: &str, bot_id: &str) -> bcs_service_api::ServiceResult<()> {
        BotAuthorityCoreService::require_manage(
            self, user_id, bot_id,
        )
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::{
        port::repo::BotAuthorityRepoPort,
        types::bot_authority::{
            AuthorityActor, AuthorityMutation, ManagerEdit, ManagerEditAction,
            ManagerSync, OwnershipChange, OwnershipSync, TransferAction,
            TransferCommand, TransferKey,
        },
    };
    use std::collections::HashMap;

    /// Minimal in-memory Repo stub: `role` feeds the Core
    /// glue policy; edit/sync record the actor ids the Core
    /// forwarded; change_transfer records whole commands.
    struct StubRepo {
        roles: HashMap<(String, String), Option<BotAccessRelation>>,
        actor_inputs: std::sync::Mutex<Vec<String>>,
        transfer_commands: std::sync::Mutex<Vec<TransferCommand>>,
        sync_ownership_calls: std::sync::Mutex<Vec<OwnershipSync>>,
        sync_ownership_result: std::sync::Mutex<Option<OwnershipChange>>,
    }

    fn canned_transfer_view() -> TransferView {
        TransferView {
            bot_id: "bot1".to_string(),
            target_owner_actor_id: "human_bob".to_string(),
            from_owner_actor_id: "human_alice".to_string(),
            status: bcs_service_api::types::bot_authority::TransferStatus::Pending,
            requested_at: 0,
            expires_at: 0,
            updated_at: 0,
            decided_at: None,
            decided_by_kind: None,
            decided_by_id: None,
            reason: None,
            result_ownership_version: None,
            bot_name_snapshot: None,
        }
    }

    #[async_trait]
    impl BotAuthorityRepoPort for StubRepo {
        async fn role(
            &self,
            user_id: &str,
            bot_id: &str,
        ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
            Ok(self
                .roles
                .get(&(user_id.to_string(), bot_id.to_string()))
                .copied()
                .flatten())
        }

        async fn roles_for(
            &self,
            queries: &[BotRoleQuery],
        ) -> bcs_service_api::ServiceResult<Vec<Option<BotAccessRelation>>> {
            let mut out = Vec::with_capacity(queries.len());
            for query in queries {
                out.push(self.role(&query.user_id, &query.bot_id).await?);
            }
            Ok(out)
        }

        async fn ownership(
            &self,
            _bot_id: &str,
        ) -> bcs_service_api::ServiceResult<BotOwnershipView> {
            unimplemented!("not needed for Core policy tests")
        }

        async fn managers(
            &self,
            _query: ManagerListQuery,
        ) -> bcs_service_api::ServiceResult<ManagerPage> {
            unimplemented!("not needed for Core policy tests")
        }

        async fn transfer(
            &self,
            _key: &TransferKey,
        ) -> bcs_service_api::ServiceResult<Option<TransferView>> {
            unimplemented!("not needed for Core policy tests")
        }

        async fn transfers(
            &self,
            _query: TransferListQuery,
        ) -> bcs_service_api::ServiceResult<TransferPage> {
            unimplemented!("not needed for Core policy tests")
        }

        async fn edit_manager(
            &self,
            command: ManagerEdit,
        ) -> bcs_service_api::ServiceResult<ManagerSetChange> {
            self.actor_inputs
                .lock()
                .expect("core test recorder")
                .push(command.user_id);
            Ok(ManagerSetChange { added: 1, removed: 0, total: 1 })
        }

        async fn sync_managers(
            &self,
            command: ManagerSync,
        ) -> bcs_service_api::ServiceResult<ManagerSetChange> {
            self.actor_inputs
                .lock()
                .expect("core test recorder")
                .extend(command.user_ids.iter().cloned());
            Ok(ManagerSetChange {
                added: command.user_ids.len() as u64,
                removed: 0,
                total: command.user_ids.len() as u64,
            })
        }

        async fn sync_ownership(
            &self,
            command: OwnershipSync,
        ) -> bcs_service_api::ServiceResult<OwnershipChange> {
            self.sync_ownership_calls
                .lock()
                .expect("core test recorder")
                .push(command);
            let change = self
                .sync_ownership_result
                .lock()
                .expect("core test recorder")
                .clone();
            change.ok_or_else(|| {
                ServiceError::InternalError("no canned ownership change".to_string())
            })
        }

        async fn change_transfer(
            &self,
            command: TransferCommand,
        ) -> bcs_service_api::ServiceResult<TransferOutcome> {
            self.transfer_commands
                .lock()
                .expect("core test recorder")
                .push(command);
            Ok(TransferOutcome::Applied {
                transfer: canned_transfer_view(),
                change: bcs_service_api::types::bot_authority::TransferChange::Created,
            })
        }
    }

    fn core_with(roles: Vec<(&str, &str, BotAccessRelation)>) -> BotAuthorityCoreServiceImpl {
        core_with_repo(roles).0
    }

    fn core_with_repo(
        roles: Vec<(&str, &str, BotAccessRelation)>,
    ) -> (BotAuthorityCoreServiceImpl, Arc<StubRepo>) {
        let repo = Arc::new(StubRepo {
            roles: roles
                .into_iter()
                .map(|(user, bot, role)| {
                    ((user.to_string(), bot.to_string()), Some(role))
                })
                .collect(),
            actor_inputs: std::sync::Mutex::new(Vec::new()),
            transfer_commands: std::sync::Mutex::new(Vec::new()),
            sync_ownership_calls: std::sync::Mutex::new(Vec::new()),
            sync_ownership_result: std::sync::Mutex::new(None),
        });
        (
            BotAuthorityCoreServiceImpl::new(repo.clone(), "core-env"),
            repo,
        )
    }

    #[tokio::test]
    async fn core_maps_user_ids_to_human_actor_ids() {
        // The repo stub keys by the RAW `user_id` param — so the
        // Core must have composed `human_<id>` before calling.
        let core = core_with(vec![("human_alice", "bot1", BotAccessRelation::Owner)]);
        assert_eq!(
            core.role("alice", "bot1").await.unwrap(),
            Some(BotAccessRelation::Owner)
        );
        assert_eq!(core.role("Alice", "bot1").await.unwrap(), None);
        assert_eq!(core.role("alice", "bot2").await.unwrap(), None);
    }

    #[tokio::test]
    async fn core_role_policy_owner_is_not_manager() {
        let core = core_with(vec![
            ("human_owner", "bot1", BotAccessRelation::Owner),
            ("human_manager", "bot1", BotAccessRelation::Manager),
        ]);
        assert!(core.can_manage("owner", "bot1").await.unwrap());
        assert!(core.can_manage("manager", "bot1").await.unwrap());
        assert!(!core.can_manage("stranger", "bot1").await.unwrap());
        assert!(core.require_owner("owner", "bot1").await.is_ok());
        assert!(core.require_owner("manager", "bot1").await.is_err());
        assert!(core.require_manage("manager", "bot1").await.is_ok());
        assert!(core.require_manage("owner", "bot1").await.is_ok());
        assert!(core.require_manage("stranger", "bot1").await.is_err());
        assert_eq!(core.env(), "core-env");
    }

    #[tokio::test]
    async fn core_write_commands_map_and_gate_actors() {
        // Human mutation actor; Repo stub keys by the
        // RAW user_id param *it receives* — the Core must
        // compose `human_<id>` before forwarding.
        let (core, repo) =
            core_with_repo(vec![("human_alice", "bot1", BotAccessRelation::Owner)]);
        let human = AuthorityMutation {
            mutation_id: "m1".into(),
            actor: AuthorityActor::Human { user_id: "alice".into() },
        };
        let edit = core
            .edit_manager(ManagerEdit {
                bot_id: "bot1".into(),
                user_id: "bob".into(),
                action: ManagerEditAction::Grant,
                mutation: human.clone(),
            })
            .await
            .expect("Human edit forwards");
        assert_eq!(edit.added, 1);
        assert_eq!(
            repo.actor_inputs.lock().unwrap().last().map(String::as_str),
            Some("human_bob"),
            "Core must map the User ID to a Human actor id"
        );

        // Service actor may not use the single-item edit.
        let service = AuthorityMutation {
            mutation_id: "m2".into(),
            actor: AuthorityActor::Service { app_id: 5 },
        };
        let err = core
            .edit_manager(ManagerEdit {
                bot_id: "bot1".into(),
                user_id: "bob".into(),
                action: ManagerEditAction::Grant,
                mutation: service.clone(),
            })
            .await
            .expect_err("edit is Human-only");
        assert!(err.to_string().contains("bot_authority_operator_forbidden"));

        // Sync is trusted-service only; Human actors are
        // rejected (sync 显式使用 App).
        let err = core
            .sync_managers(ManagerSync {
                bot_id: "bot1".into(),
                user_ids: vec!["bob".into()],
                mutation: human,
            })
            .await
            .expect_err("sync is service-only");
        assert!(err.to_string().contains("bot_authority_operator_forbidden"));

        // Sync input: dedup + byte-stable map + cap.
        let ok = core
            .sync_managers(ManagerSync {
                bot_id: "bot1".into(),
                user_ids: vec!["bob".into(), "bob".into(), "cyd".into()],
                mutation: service.clone(),
            })
            .await
            .expect("service sync");
        assert_eq!(ok.added, 2, "deduped input forwarded");
        let inputs = repo.actor_inputs.lock().unwrap();
        assert_eq!(
            &inputs[inputs.len() - 2..],
            &["human_bob".to_string(), "human_cyd".to_string()]
        );

        let over: Vec<String> = (0..1_001).map(|index| format!("u{index}")).collect();
        let err = core
            .sync_managers(ManagerSync {
                bot_id: "bot1".into(),
                user_ids: over,
                mutation: service,
            })
            .await
            .expect_err("1,001 deduped users exceed the input cap");
        assert!(err.to_string().contains("bot_manager_input_limit"));

        // change_transfer forwards verbatim (Task 3): the
        // Store owns the owner/version/time snapshot reads,
        // the Core invents nothing from the command.
        let command = TransferCommand {
            key: TransferKey {
                bot_id: "bot1".into(),
                target_owner_actor_id: "human_bob".into(),
            },
            action: TransferAction::RequestOrRefresh,
            mutation: AuthorityMutation {
                mutation_id: "m3".into(),
                actor: AuthorityActor::Human { user_id: "alice".into() },
            },
        };
        let outcome = core
            .change_transfer(command.clone())
            .await
            .expect("transfer forwards to the repo");
        assert!(matches!(
            outcome,
            TransferOutcome::Applied {
                change: bcs_service_api::types::bot_authority::TransferChange::Created,
                ..
            }
        ));
        assert_eq!(
            repo.transfer_commands.lock().unwrap().as_slice(),
            &[command],
            "the Core must forward the command unchanged"
        );
    }
}

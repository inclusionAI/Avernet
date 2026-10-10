//! `TeamManagerSyncService` production implementation (plan Task 13,
//! spec §5.4/§6.1).
//!
//! Trust boundary composition (Gate 0 record, spec §1.3):
//! - [`TeamManagerSyncServiceImpl::verify_service_credential`] verifies
//!   the platform credential through the injected
//!   [`TeamManagerCredentialVerifierPort`] port; a blank credential is
//!   401-equivalent and an unverifiable one 403 `invalid_manager_sync_source`.
//!   The credential value itself is dropped here — it never enters
//!   business logs, audit rows, or persisted commands;
//! - [`Self::sync`] builds the Core command
//!   (`TeamManagerSync`, which carries the VERIFIED service + scopes),
//!   structurally validates it, and re-checks the command's scopes
//!   fail-closed before reaching the Core (the store re-proves them
//!   inside its one-transaction authority: Task 7);
//! - the single-member repairs derive ONE full-snapshot reconcile of the
//!   team's member set (current set ± the single user, read through the
//!   Core's manager list) and reuse the same sync lane — identical
//!   source semantics, audit, durable idempotency, and credential lane.
//!   They are operations-repair entries, never a normal sync path.
//!
//! Scope semantics note: the credential's operation scope constrains the
//! operation KIND (`sync`/`move`); the repairs are snapshot reconciles
//! and therefore require the `sync` kind.

use std::collections::BTreeSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, TeamManagerMemberRepair, TeamManagerSyncCommand, TeamManagerSyncService,
};
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::port::TeamManagerCredentialVerifierPort;
use bcs_service_api::types::error::{AuthorityError, ServiceError};
use bcs_service_api::types::team_manager_sync::{
    TeamManagerOperation, TeamManagerSync, TeamSyncReceipt, VerifiedTeamManagerService,
    validate_team_sync_command,
};
use bcs_service_api::types::{BotManagerList, ManagementSource};

/// Hard bound while reading the current team snapshot inside one repair:
/// the derived reconcile reuses the same Gate 0 snapshot cap as any sync.
const REPAIR_SNAPSHOT_READ_PAGE: u64 = 100;

/// Environment binding of this application facade (the store re-proves
/// it inside the transaction; the application validates first).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TeamManagerSyncServiceConfig {
    pub env: String,
}

/// Trusted team-manager synchronization facade.
pub struct TeamManagerSyncServiceImpl {
    core: Arc<dyn BotAuthorityCoreService>,
    verifier: Arc<dyn TeamManagerCredentialVerifierPort>,
    config: TeamManagerSyncServiceConfig,
}

impl TeamManagerSyncServiceImpl {
    pub fn new(
        core: Arc<dyn BotAuthorityCoreService>,
        verifier: Arc<dyn TeamManagerCredentialVerifierPort>,
        config: TeamManagerSyncServiceConfig,
    ) -> Self {
        Self {
            core,
            verifier,
            config,
        }
    }

    /// Credential-verification failures map fail-closed: a blank value is
    /// the transport's missing-header 401, everything else is the 403
    /// `invalid_manager_sync_source` family. No branch ever embeds the
    /// credential or the signing key.
    fn map_verify_error(error: ServiceError) -> ApplicationError {
        match error {
            ServiceError::Authority(authority) => match authority {
                AuthorityError::Forbidden(message) => {
                    ApplicationError::invalid_manager_sync_source(message)
                }
                // The verifier is pure: any non-forbidden branch is a
                // fail-closed internal, never a silently accepted one.
                other => ApplicationError::internal(other.to_string()),
            },
            other => ApplicationError::internal(other.to_string()),
        }
    }

    /// The repaired snapshot's source read: page through the Core's whole
    /// manager list of the Bot and select the members of ONE team.
    async fn current_team_members(
        &self,
        bot_id: &str,
        team_id: &str,
    ) -> Result<BTreeSet<String>, ApplicationError> {
        let mut members = BTreeSet::new();
        let mut scanned: u64 = 0;
        loop {
            let page: BotManagerList = self
                .core
                .list_managers(bot_id, scanned, REPAIR_SNAPSHOT_READ_PAGE)
                .await
                .map_err(map_team_error)?;
            let len = page.managers.len();
            for manager in page.managers {
                let in_team = manager
                    .sources
                    .iter()
                    .any(|source| matches!(source, ManagementSource::Team(id) if id == team_id));
                if in_team {
                    members.insert(manager.user_id);
                }
            }
            if (len as u64) < REPAIR_SNAPSHOT_READ_PAGE {
                break;
            }
            scanned += len as u64;
        }
        // The derived reconcile reuses the store's snapshot cap: an
        // oversized current snapshot fails at the store anyway; fail
        // early here without pretending the read fits.
        if members.len() > bcs_service_api::types::team_manager_sync::TEAM_SYNC_MAX_SNAPSHOT {
            return Err(ApplicationError::payload_too_large(
                "invalid_request",
                "current team snapshot exceeds the repair bound",
            ));
        }
        Ok(members)
    }

    /// The shared sync pipeline of every write this facade offers.
    async fn run_sync(
        &self,
        command: TeamManagerSync,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        // Structural validation is pure and pre-transactional here (the
        // store re-validates; this is the fast fail for move-target
        // shape defects).
        validate_team_sync_command(&command).map_err(|error| {
            ApplicationError::invalid("invalid_request", error.to_string())
        })?;
        // Application-side scope proof (env/bot/team/operation): the
        // store remains the authority and re-proves inside the write.
        command
            .service
            .authorize_sync(&self.config.env, &command)
            .map_err(|error| {
                ApplicationError::invalid_manager_sync_source(error.to_string())
            })?;
        self.core.sync_team(command).await.map_err(map_team_error)
    }
}

/// Lane-specific fixed error mapping (spec §6.1):
/// - `Forbidden` (scope/credential/actor) → 403 `invalid_manager_sync_source`;
/// - `InvalidSubject` (snapshot member unresolvable) → 400 `invalid_membership_snapshot`;
/// - `Conflict` (same idempotency key, different canonical payload) → 409 `manager_sync_conflict`;
/// - shared branches (`ownership_not_initialized`, `corrupt_authority`) keep
///   their spec §11.2 codes; storage/internal failures stay 500.
fn map_team_error(error: ServiceError) -> ApplicationError {
    match &error {
        ServiceError::Authority(authority) => match authority {
            AuthorityError::Forbidden(_) => {
                ApplicationError::invalid_manager_sync_source(error.to_string())
            }
            AuthorityError::InvalidSubject(_) => ApplicationError::invalid_membership_snapshot(
                error.to_string(),
            ),
            AuthorityError::Conflict(_) => ApplicationError::manager_sync_conflict(
                error.to_string(),
            ),
            _ => ApplicationError::authority(authority.clone()),
        },
        ServiceError::InvalidOperation { .. } => {
            ApplicationError::invalid("invalid_request", error.to_string())
        }
        ServiceError::BotNotFound(bot_id) => ApplicationError::not_found(
            "bot_not_found",
            format!("Bot '{bot_id}' was not found"),
        ),
        other => ApplicationError::internal(other.to_string()),
    }
}

fn validate_identity(field: &'static str, value: &str) -> Result<(), ApplicationError> {
    if value.trim().is_empty() {
        return Err(ApplicationError::invalid(
            "invalid_request",
            format!("team sync use case field '{field}' must be a non-empty identity"),
        ));
    }
    Ok(())
}

#[async_trait]
impl TeamManagerSyncService for TeamManagerSyncServiceImpl {
    async fn verify_service_credential(
        &self,
        credential: &str,
    ) -> Result<VerifiedTeamManagerService, ApplicationError> {
        // A blank credential is indistinguishable from a missing one:
        // authentication required, never a scope decision.
        if credential.trim().is_empty() {
            return Err(ApplicationError::Unauthenticated);
        }
        // The verified result is the ONLY actor shape flowing onward.
        // The raw credential is dropped here — never logged, audited,
        // or persisted with the command.
        self.verifier.verify(credential).map_err(Self::map_verify_error)
    }

    async fn sync(
        &self,
        command: TeamManagerSyncCommand,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        validate_identity("bot_id", &command.bot_id)?;
        validate_identity("team_id", &command.team_id)?;
        validate_identity("idempotency_key", &command.idempotency_key)?;
        let core_command = TeamManagerSync {
            service: command.service,
            bot_id: command.bot_id,
            team_id: command.team_id,
            operation: command.operation,
            manager_user_ids: command.manager_user_ids,
            idempotency_key: command.idempotency_key,
        };
        self.run_sync(core_command).await
    }

    async fn repair_add_team_member(
        &self,
        command: TeamManagerMemberRepair,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        validate_identity("bot_id", &command.bot_id)?;
        validate_identity("team_id", &command.team_id)?;
        validate_identity("user_id", &command.user_id)?;
        validate_identity("idempotency_key", &command.idempotency_key)?;
        let mut desired = self
            .current_team_members(&command.bot_id, &command.team_id)
            .await?;
        desired.insert(command.user_id.clone());
        let core_command = TeamManagerSync {
            service: command.service,
            bot_id: command.bot_id,
            team_id: command.team_id,
            // A repair is a full-snapshot reconcile of this team source.
            operation: TeamManagerOperation::Sync,
            manager_user_ids: desired.into_iter().collect(),
            idempotency_key: command.idempotency_key,
        };
        self.run_sync(core_command).await
    }

    async fn repair_remove_team_member(
        &self,
        command: TeamManagerMemberRepair,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        validate_identity("bot_id", &command.bot_id)?;
        validate_identity("team_id", &command.team_id)?;
        validate_identity("user_id", &command.user_id)?;
        validate_identity("idempotency_key", &command.idempotency_key)?;
        let mut desired = self
            .current_team_members(&command.bot_id, &command.team_id)
            .await?;
        desired.remove(&command.user_id);
        let core_command = TeamManagerSync {
            service: command.service,
            bot_id: command.bot_id,
            team_id: command.team_id,
            operation: TeamManagerOperation::Sync,
            manager_user_ids: desired.into_iter().collect(),
            idempotency_key: command.idempotency_key,
        };
        self.run_sync(core_command).await
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::sync::{Arc, Mutex};

    use async_trait::async_trait;
    use bcs_service_api::application::v1::ApplicationError;
    use bcs_service_api::core::BotAuthorityCoreService;
    use bcs_service_api::port::TeamManagerCredentialVerifierPort;
    use bcs_service_api::types::ownership_transfer::{
        CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult,
        ListOwnershipTransfers, OwnershipTransfer, OwnershipTransferPage,
    };
    use bcs_service_api::types::team_manager_sync::{
        TeamManagerOperation, TeamManagerSync, TeamSyncReceipt, VerifiedTeamManagerService,
    };
    use bcs_service_api::types::{
        AuditActor, BotAccessRelation, BotManagerList, BotManagerSummary, ManagerMutation,
        ManagerMutationResult, ManagementSource, OwnershipState, ServiceResult, TransferAction,
    };
    use bcs_service_api::ServiceError;

    use super::*;

    const ENV: &str = "test-env";

    /// Credential table double: proves the facade resolves trust through
    /// the INJECTED verifier port, never through a body value.
    struct TableVerifier {
        services: BTreeMap<String, VerifiedTeamManagerService>,
    }

    impl TeamManagerCredentialVerifierPort for TableVerifier {
        fn verify(
            &self,
            credential: &str,
        ) -> ServiceResult<VerifiedTeamManagerService> {
            match self.services.get(credential) {
                Some(service) => Ok(service.clone()),
                None => Err(ServiceError::Authority(
                    AuthorityError::Forbidden(
                        "unknown team-manager service credential".to_string(),
                    ),
                )),
            }
        }
    }

    /// Recording Core: captures sync commands and serves the manager pages
    /// the repair lane reads from. Every non-team method is unreachable.
    #[derive(Default)]
    struct RecordingCore {
        syncs: Mutex<Vec<TeamManagerSync>>,
        lists: Mutex<Vec<(String, u64, u64)>>,
        next_error: Mutex<Option<ServiceError>>,
    }

    fn members_page(managers: Vec<BotManagerSummary>) -> BotManagerList {
        BotManagerList {
            owner_user_id: "user-a".to_string(),
            managers,
        }
    }

    #[async_trait]
    impl BotAuthorityCoreService for RecordingCore {
        async fn ownership(&self, _bot_id: &str) -> ServiceResult<OwnershipState> {
            unreachable!("team sync lane never reads ownership directly")
        }

        async fn role(
            &self,
            _user_id: &str,
            _bot_id: &str,
        ) -> ServiceResult<Option<BotAccessRelation>> {
            unreachable!("team sync lane never answers hook role questions")
        }

        async fn roles_for(
            &self,
            _pairs: &[(String, String)],
        ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
            unreachable!("team sync lane never batch-reads roles")
        }

        async fn mutate_manager(
            &self,
            _actor: AuditActor,
            _bot_id: &str,
            _mutation: ManagerMutation,
        ) -> ServiceResult<ManagerMutationResult> {
            unreachable!("team sync lane never mutates direct managers")
        }

        async fn list_managers(
            &self,
            bot_id: &str,
            offset: u64,
            limit: u64,
        ) -> ServiceResult<BotManagerList> {
            self.lists
                .lock()
                .expect("lists lock")
                .push((bot_id.to_string(), offset, limit));
            if let Some(error) = self.next_error.lock().expect("error lock").take() {
                return Err(error);
            }
            // A short page ends the repair's read loop.
            Ok(members_page(vec![
                BotManagerSummary {
                    user_id: "user-x".to_string(),
                    sources: vec![
                        ManagementSource::Team("team-a".to_string()),
                        ManagementSource::Direct,
                    ],
                },
                BotManagerSummary {
                    user_id: "user-y".to_string(),
                    sources: vec![ManagementSource::Team("team-a".to_string())],
                },
                BotManagerSummary {
                    user_id: "user-z".to_string(),
                    sources: vec![ManagementSource::Team("team-b".to_string())],
                },
            ]))
        }

        async fn sync_team(&self, command: TeamManagerSync) -> ServiceResult<TeamSyncReceipt> {
            self.syncs.lock().expect("syncs lock").push(command.clone());
            if let Some(error) = self.next_error.lock().expect("error lock").take() {
                return Err(error);
            }
            Ok(TeamSyncReceipt {
                operation_id: "op-1".to_string(),
                bot_id: command.bot_id,
                team_id: command.team_id,
                operation: command.operation,
                granted_count: 1,
                revoked_count: 1,
            })
        }

        async fn create_transfer(
            &self,
            _command: CreateOwnershipTransfer,
        ) -> ServiceResult<CreateTransferResult> {
            unreachable!("team sync lane never creates transfers")
        }

        async fn decide_transfer(
            &self,
            _actor_user_id: &str,
            _transfer_id: &str,
            _action: TransferAction,
        ) -> ServiceResult<CommittedTransferOutcome> {
            unreachable!("team sync lane never decides transfers")
        }

        async fn get_transfer(
            &self,
            _viewer_user_id: &str,
            _transfer_id: &str,
        ) -> ServiceResult<OwnershipTransfer> {
            unreachable!("team sync lane never reads transfers")
        }

        async fn list_transfers(
            &self,
            _query: ListOwnershipTransfers,
        ) -> ServiceResult<OwnershipTransferPage> {
            unreachable!("team sync lane never lists transfers")
        }
    }

    fn verified(
        service_id: &str,
        allowed_bots: Option<Vec<&str>>,
        allowed_teams: Option<Vec<&str>>,
        allowed_operations: Option<Vec<TeamManagerOperation>>,
    ) -> VerifiedTeamManagerService {
        VerifiedTeamManagerService {
            service_id: service_id.to_string(),
            env: ENV.to_string(),
            allowed_bots: allowed_bots.map(|bots| bots.into_iter().map(str::to_string).collect()),
            allowed_teams: allowed_teams
                .map(|teams| teams.into_iter().map(str::to_string).collect()),
            allowed_operations,
        }
    }

    struct Fixture {
        core: Arc<RecordingCore>,
        facade: TeamManagerSyncServiceImpl,
    }

    fn fixture() -> Fixture {
        let core = Arc::new(RecordingCore::default());
        let verifier: Arc<dyn TeamManagerCredentialVerifierPort> = Arc::new(TableVerifier {
            services: BTreeMap::from([
                (
                    "credential-good".to_string(),
                    verified("team-sync-1", None, None, None),
                ),
                (
                    "credential-restricted".to_string(),
                    verified(
                        "team-sync-2",
                        Some(vec!["bot-a"]),
                        Some(vec!["team-a"]),
                        None,
                    ),
                ),
                (
                    "credential-move-only".to_string(),
                    verified(
                        "team-sync-3",
                        None,
                        None,
                        Some(vec![TeamManagerOperation::Move {
                            new_team_id: String::new(),
                        }]),
                    ),
                ),
            ]),
        });
        let facade = TeamManagerSyncServiceImpl::new(
            core.clone(),
            verifier,
            TeamManagerSyncServiceConfig { env: ENV.to_string() },
        );
        Fixture { core, facade }
    }

    fn sync_command(service: VerifiedTeamManagerService) -> TeamManagerSyncCommand {
        TeamManagerSyncCommand {
            service,
            bot_id: "bot-a".to_string(),
            team_id: "team-a".to_string(),
            operation: TeamManagerOperation::Sync,
            manager_user_ids: vec!["user-x".to_string()],
            idempotency_key: "key-1".to_string(),
        }
    }

    fn repair(service: VerifiedTeamManagerService) -> TeamManagerMemberRepair {
        TeamManagerMemberRepair {
            service,
            bot_id: "bot-a".to_string(),
            team_id: "team-a".to_string(),
            user_id: "user-y".to_string(),
            idempotency_key: "repair-1".to_string(),
        }
    }

    #[tokio::test]
    async fn verify_resolves_trust_through_the_injected_port() {
        let fixture = fixture();
        let blank = fixture
            .facade
            .verify_service_credential("  ")
            .await
            .expect_err("blank credential is 401");
        assert!(matches!(blank, ApplicationError::Unauthenticated));
        let verified_service = fixture
            .facade
            .verify_service_credential("credential-good")
            .await
            .expect("trusted credential verifies");
        assert_eq!(verified_service, verified("team-sync-1", None, None, None));
        let err = fixture
            .facade
            .verify_service_credential("credential-bogus")
            .await
            .expect_err("unknown credential is forbidden");
        assert_eq!(err.code(), "invalid_manager_sync_source");
    }

    #[tokio::test]
    async fn sync_forwards_the_verified_service_and_revalidates_scopes() {
        let fixture = fixture();
        let receipt = fixture
            .facade
            .sync(sync_command(verified("team-sync-1", None, None, None)))
            .await
            .expect("sync succeeds");
        assert_eq!(receipt.operation_id, "op-1");
        let syncs = fixture.core.syncs.lock().expect("syncs lock");
        assert_eq!(syncs.len(), 1);
        // The Core command carries the VERIFIED service, not a body forgery.
        assert_eq!(syncs[0].service, verified("team-sync-1", None, None, None));
        assert_eq!(syncs[0].idempotency_key, "key-1");
        assert_eq!(syncs[0].manager_user_ids, vec!["user-x".to_string()]);
    }

    #[tokio::test]
    async fn sync_outside_scopes_and_illegal_moves_fail_closed() {
        let fixture = fixture();
        // restricted credential, other team
        let mut command = sync_command(verified(
            "team-sync-2",
            Some(vec!["bot-a"]),
            Some(vec!["team-a"]),
            None,
        ));
        command.team_id = "team-b".to_string();
        let err = fixture
            .facade
            .sync(command)
            .await
            .expect_err("out-of-scope team");
        assert_eq!(err.code(), "invalid_manager_sync_source");
        assert!(fixture.core.syncs.lock().expect("syncs lock").is_empty());

        // move-only credential: snapshot sync is a sync-kind command
        let command = sync_command(verified(
            "team-sync-3",
            None,
            None,
            Some(vec![TeamManagerOperation::Move {
                new_team_id: String::new(),
            }]),
        ));
        let err = fixture
            .facade
            .sync(command)
            .await
            .expect_err("move-only credential cannot run snapshot syncs");
        assert_eq!(err.code(), "invalid_manager_sync_source");

        // move to the same team is structurally rejected as 400
        let command = TeamManagerSyncCommand {
            service: verified("team-sync-1", None, None, None),
            bot_id: "bot-a".to_string(),
            team_id: "team-a".to_string(),
            operation: TeamManagerOperation::Move {
                new_team_id: "team-a".to_string(),
            },
            manager_user_ids: vec![],
            idempotency_key: "key-2".to_string(),
        };
        let err = fixture
            .facade
            .sync(command)
            .await
            .expect_err("self-move is invalid");
        assert_eq!(err.code(), "invalid_request");
        assert!(fixture.core.syncs.lock().expect("syncs lock").is_empty());
    }

    #[tokio::test]
    async fn store_branches_map_to_the_lane_fixed_codes() {
        let fixture = fixture();
        *fixture.core.next_error.lock().expect("error lock") = Some(ServiceError::Authority(
            AuthorityError::Conflict("payload diverged".into()),
        ));
        let err = fixture
            .facade
            .sync(sync_command(verified("team-sync-1", None, None, None)))
            .await
            .expect_err("conflict maps to manager_sync_conflict");
        assert_eq!(err.code(), "manager_sync_conflict");

        *fixture.core.next_error.lock().expect("error lock") = Some(ServiceError::Authority(
            AuthorityError::InvalidSubject("not live".into()),
        ));
        let err = fixture
            .facade
            .sync(sync_command(verified("team-sync-1", None, None, None)))
            .await
            .expect_err("invalid membership snapshot");
        assert_eq!(err.code(), "invalid_membership_snapshot");
    }

    #[tokio::test]
    async fn repairs_reconcile_one_full_snapshot_under_the_same_lane() {
        let fixture = fixture();
        let receipt = fixture
            .facade
            .repair_add_team_member(repair(verified("team-sync-1", None, None, None)))
            .await
            .expect("add repair succeeds");
        assert_eq!(receipt.operation_id, "op-1");
        {
            let syncs = fixture.core.syncs.lock().expect("syncs lock");
            assert_eq!(syncs.len(), 1);
            // Derived from the CURRENT team set (user-x, user-y) plus the
            // repaired member: user-y is already there, so the reconcile
            // stays set-identical. Only team-a members derive it.
            assert_eq!(
                syncs[0].manager_user_ids,
                vec!["user-x".to_string(), "user-y".to_string()]
            );
            assert_eq!(syncs[0].operation, TeamManagerOperation::Sync);
            assert_eq!(syncs[0].idempotency_key, "repair-1");
        }

        let receipt = fixture
            .facade
            .repair_remove_team_member(repair(verified("team-sync-1", None, None, None)))
            .await
            .expect("remove repair succeeds");
        assert_eq!(receipt.operation_id, "op-1");
        let syncs = fixture.core.syncs.lock().expect("syncs lock");
        assert_eq!(syncs.len(), 2);
        // user-y left; user-x stays; the other team's member never moves.
        assert_eq!(syncs[1].manager_user_ids, vec!["user-x".to_string()]);
    }

    #[tokio::test]
    async fn repairs_honor_the_credential_scopes_and_blank_identities() {
        let fixture = fixture();
        // The restricted credential may not repair another team.
        let mut outside = repair(verified(
            "team-sync-2",
            Some(vec!["bot-a"]),
            Some(vec!["team-a"]),
            None,
        ));
        outside.team_id = "team-b".to_string();
        let err = fixture
            .facade
            .repair_add_team_member(outside)
            .await
            .expect_err("out-of-scope repair");
        assert_eq!(err.code(), "invalid_manager_sync_source");

        let mut blank = repair(verified("team-sync-1", None, None, None));
        blank.user_id = "  ".to_string();
        let err = fixture
            .facade
            .repair_remove_team_member(blank)
            .await
            .expect_err("blank identity");
        assert_eq!(err.code(), "invalid_request");
    }
}
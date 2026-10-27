//! Memory projection of the atomic retirement
//! (bot-authority spec §7.1) — the mirror of the SQL
//! composites in `crate::retirement` (ordinary Bot
//! retirement plus the Provider three-branch core
//! state).
//!
//! ONE staged swap of the shared authority state
//! revokes the owner/manager edges, closes the valid
//! pending transfers (fixed system marker,
//! `bot_retired` reason) and appends the `retire`
//! audit — the real mutation actor, no credentials.
//! All fallible work completes BEFORE any swap
//! publishes, so a failed command leaves the
//! preexisting state byte-identical. A repeat on an
//! already-tombstoned Bot returns `changed = false`
//! WITHOUT a second retire audit; the idempotent
//! revocation/closure steps still run (no-ops).

use bcs_service_api::types::bot_authority::{
    AuthorityActorKind, AuthorityMutation, TransferStatus,
};
use bcs_service_api::{ServiceError, ServiceResult};

use bcs_config::resolve_env_str as resolve_env;
use bcs_edge_permission_store::lifecycle;

use super::authority::{
    MemoryAuthorityAuditRow, MemoryAuthorityEdgeKind,
    MemoryAuthorityEdgeStatus, MemoryAuthorityState,
    MemoryTransferRow,
};
use super::MemoryBotRepo;
use crate::ownership;

/// Which retirement command invoked the shared
/// state pass (the ordinary command refuses
/// Provider-managed Bots; the Provider command IS
/// that command).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RetireCommand {
    Ordinary,
    Provider,
}

impl MemoryBotRepo {
    /// The foundation of BOTH memory retires: one
    /// staged authority-state swap (edge revocation,
    /// pending closure with per-row system audits,
    /// the retire audit when `changed`) plus the
    /// Bot tombstone (bot map removal, token map
    /// cleanup, tombstone set insertion). Returns
    /// whether THIS call executed the tombstone
    /// transition.
    pub(crate) async fn retire_core_state(
        &self,
        bot_id: &str,
        mutation: &AuthorityMutation,
    ) -> ServiceResult<bool> {
        self.retire_core_state_scoped(
            bot_id,
            mutation,
            RetireCommand::Ordinary,
        )
        .await
    }

    /// The Provider retirement passes
    /// [`RetireCommand::Provider`]: its OWN branch was
    /// verified on the durable Identity rows, so the
    /// ordinary-retire refusal (two commands, two
    /// contracts) does not apply.
    pub(crate) async fn retire_core_state_provider(
        &self,
        bot_id: &str,
        mutation: &AuthorityMutation,
    ) -> ServiceResult<bool> {
        self.retire_core_state_scoped(
            bot_id,
            mutation,
            RetireCommand::Provider,
        )
        .await
    }

    async fn retire_core_state_scoped(
        &self,
        bot_id: &str,
        mutation: &AuthorityMutation,
        command: RetireCommand,
    ) -> ServiceResult<bool> {
        ownership::validate_mutation(mutation)?;
        let env = resolve_env();
        let now = super::unix_millis();
        // Pre-read: an early NotFound fast-path (parameter
        // preparation only, spec §7). Every fact that
        // races concurrency — Bot liveness and the Human
        // operator's authority — is re-verified INSIDE
        // the write span below.
        if !self.bots.read().await.contains_key(bot_id)
            && !self.deleted_bot_ids.read().await.contains(bot_id)
        {
            return Err(ServiceError::BotNotFound(bot_id.into()));
        }

        // Lock order (this module's documented order):
        // authority → bots → tokens → deleted IDs.
        let mut state_guard = self.authority.write().await;
        // Bot retirement is not a Human lifecycle command. Refuse under the
        // same authority lock used by ownership registration and transfers.
        if self.bots.read().await.get(bot_id)
            .is_some_and(|bot| bot.actor_kind != bcs_service_api::ActorKind::Bot) {
            return Err(ServiceError::Forbidden("Bot retirement cannot delete a Human actor".into()));
        }
        let mut staged = state_guard.clone();

        // 事务内复验 Bot liveness: two racing retirements
        // serialize on THIS guard; the loser re-reads the
        // tombstoned state and reports changed = false
        // without a second retire audit.
        let changed = self.bots.read().await.contains_key(bot_id);

        // Two distinct retirement commands: an
        // ordinary retire NEVER drains a
        // Provider-managed Bot (spec §7.1) — the
        // Provider retirement is its own command.
        if command == RetireCommand::Ordinary
            && staged
                .provider_managed
                .contains(&(env.clone(), bot_id.to_string()))
        {
            return Err(ServiceError::Conflict(
                "provider-managed bot must be retired \
                 through the Provider retirement command"
                    .to_string(),
            ));
        }

        // 事务内复验 the operator's authority — only for a
        // REAL transition on an AUTHORITY-INITIALIZED Bot
        // (version > 0): a runtime version-0 Bot has no
        // owner/manager register to consult and the ordinary
        // lifecycle delete keeps its legacy contract; the
        // already-tombstoned retry keeps its idempotent no-op
        // outcome. An ordinary retirement by a Human whose
        // owner/manager edge is already gone must not
        // tombstone anything (a stale application-time check
        // proves nothing). The Provider retirement is
        // verified on its own branch.
        let authority_initialized = staged
            .ownership_versions
            .get(&(env.clone(), bot_id.to_string()))
            .copied()
            .unwrap_or(0)
            > 0;
        if changed && authority_initialized && command == RetireCommand::Ordinary {
            let operator_user = match &mutation.actor {
                bcs_service_api::types::bot_authority::AuthorityActor::Human {
                    user_id,
                } => user_id,
                _ => {
                    return Err(ServiceError::Forbidden(
                        "ordinary retirement requires a Human operator"
                            .to_string(),
                    ));
                }
            };
            let operator_actor =
                bcs_service_api::types::bot_authority::human_actor_id(
                    operator_user,
                );
            let operator_current = staged.edges.iter().any(
                |(key, (status, ref_id))| {
                    key.0 == env
                        && key.1 == bot_id
                        && key.2 == operator_actor
                        && matches!(
                            key.3,
                            MemoryAuthorityEdgeKind::Owner
                                | MemoryAuthorityEdgeKind::Manager
                        )
                        && *status == MemoryAuthorityEdgeStatus::Approved
                        && *ref_id == 0
                },
            );
            if !operator_current {
                return Err(ServiceError::Forbidden(format!(
                    "operator '{operator_user}' holds no current \
                     owner/manager edge on '{bot_id}'"
                )));
            }
        }

        // Owner/manager revocation (spec §3.2.7). Idempotent.
        let stale_edges: Vec<_> = staged
            .edges
            .iter()
            .filter(|(key, (status, _))| {
                key.0 == env
                    && key.1 == bot_id
                    && *status
                        == MemoryAuthorityEdgeStatus::Approved
                    && matches!(
                        key.3,
                        MemoryAuthorityEdgeKind::Owner
                            | MemoryAuthorityEdgeKind::Manager
                    )
            })
            .map(|(key, _)| key.clone())
            .collect();
        for key in stale_edges {
            staged
                .edges
                .insert(key, (MemoryAuthorityEdgeStatus::Revoked, 0));
        }

        // Valid pending closure: snapshot the rows
        // FIRST (the audit rows carry the pre-state),
        // then close under the audit writes.
        let pendings: Vec<((String, String, String),
            MemoryTransferRow)> = staged
            .transfers
            .iter()
            .filter(|(key, row)| {
                key.0 == env
                    && key.1 == bot_id
                    && row.status == TransferStatus::Pending
                    && now < row.expires_at
            })
            .map(|(key, row)| (key.clone(), row.clone()))
            .collect();
        for (key, row) in &pendings {
            if let Some(row) = staged.transfers.get_mut(key) {
                row.status = TransferStatus::Invalidated;
                row.decided_at = Some(now);
                row.decided_by_kind =
                    Some(AuthorityActorKind::System);
                row.decided_by_id =
                    Some(lifecycle::system_transfer_close_actor()
                        .to_string());
                row.reason = Some(
                    lifecycle::REASON_BOT_RETIRED.to_string(),
                );
                row.updated_at = now;
            }
            append_closure_audit(
                &mut staged,
                &env,
                bot_id,
                &mutation.mutation_id,
                &key.2,
                row,
                now,
            );
        }

        if changed {
            append_retire_audit(
                &mut staged,
                &env,
                bot_id,
                &mutation.mutation_id,
                mutation.actor.kind().as_str(),
                &mutation.actor.actor_id(),
                now,
            )?;
        }

        // Swap the authority projection (the staged pass
        // is all-or-nothing), then the infallible
        // runtime tombstone.
        *state_guard = staged;
        if changed {
            self.bots.write().await.remove(bot_id);
            self.token_to_bot
                .write()
                .await
                .retain(|_, value| value != bot_id);
            self.deleted_bot_ids
                .write()
                .await
                .insert(bot_id.to_string());
        }
        Ok(changed)
    }

    /// Mark the Bot as Provider-COMMAND owned
    /// (metadata/binding membership) so the ordinary
    /// retirement refuses it. Called by the Provider
    /// create/attach paths.
    pub(crate) async fn mark_provider_managed(
        &self,
        bot_id: &str,
    ) {
        let env = resolve_env();
        self.authority
            .write()
            .await
            .provider_managed
            .insert((env, bot_id.to_string()));
    }

}

/// The one `retire` audit row on the staged clone —
/// the same byte shapes as the SQL store (sequence 0,
/// real mutation actor, no credentials).
#[allow(clippy::too_many_arguments)]
fn append_retire_audit(
    staged: &mut MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    mutation_id: &str,
    actor_kind: &str,
    actor_id: &str,
    now: u64,
) -> ServiceResult<()> {
    if staged.audit.iter().any(|row| {
        row.env == env
            && row.mutation_id == mutation_id
            && row.sequence == 0
    }) {
        return Err(ServiceError::InternalError(format!(
            "bot authority memory store: audit row already \
             exists for mutation {mutation_id}"
        )));
    }
    staged.audit.push(MemoryAuthorityAuditRow {
        env: env.to_string(),
        bot_id: bot_id.to_string(),
        mutation_id: mutation_id.to_string(),
        sequence: 0,
        action: lifecycle::ACTION_RETIRE.to_string(),
        actor_kind: actor_kind.to_string(),
        actor_id: actor_id.to_string(),
        subject_id: bot_id.to_string(),
        before_state: None,
        after_state: lifecycle::retire_audit_state().to_string(),
        created_ms: now,
    });
    Ok(())
}

/// One `transfer_invalidate` closure audit row on the
/// staged clone (sequences start at 1; the `retire`
/// row owns sequence 0) — mirroring the SQL shapes.
pub(crate) fn append_closure_audit(
    staged: &mut MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    mutation_id: &str,
    target: &str,
    row: &MemoryTransferRow,
    now: u64,
) {
    let (before_state, after_state) =
        lifecycle::retirement_closure_states(
            &row.from_owner_actor_id,
            row.expected_ownership_version,
        );
    staged.audit.push(MemoryAuthorityAuditRow {
        env: env.to_string(),
        bot_id: bot_id.to_string(),
        mutation_id: mutation_id.to_string(),
        sequence: next_closure_sequence(staged, env, mutation_id),
        action: "transfer_invalidate".to_string(),
        actor_kind: "system".to_string(),
        actor_id: lifecycle::system_transfer_close_actor()
            .to_string(),
        subject_id: target.to_string(),
        before_state: Some(before_state),
        after_state,
        created_ms: now,
    });
}

fn next_closure_sequence(
    staged: &MemoryAuthorityState,
    env: &str,
    mutation_id: &str,
) -> u64 {
    1 + staged
        .audit
        .iter()
        .filter(|row| {
            row.env == env && row.mutation_id == mutation_id
        })
        .map(|row| row.sequence)
        .max()
        .unwrap_or(0)
}
